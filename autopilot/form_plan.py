#!/usr/bin/env python3
"""Prepare an application-form plan for a packet (read-only; never submits).

Reads the employer's public application form, maps every field to an approved
answer from form_answers.json, and marks everything else as PAUSE (needs the
candidate). Writes documents/applications/<slug>/form_plan.md and stores a
summary in packet.json.

Supported: Lever (jobs.lever.co .../apply page), Greenhouse (public board API
with questions). Other hosts (LinkedIn, Workable, company sites) get a plan that
says how to reach the form and that the fields will be read at submission time.

Usage: form_plan.py SLUG
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import APPS, AUTOPILOT, ROOT, load_json, log_event, save_json  # noqa: E402

_ANS_PATH = AUTOPILOT / "form_answers.json"   # private, git-ignored; the example only shows the format
ANS = json.loads((_ANS_PATH if _ANS_PATH.exists() else AUTOPILOT / "form_answers.example.json").read_text(encoding="utf-8"))

# label regex -> form_answers key (first match wins)
RULES = [
    (r"full name|^name\b|ad soyad", "full_name"), (r"e-?mail", "email"), (r"phone|telefon", "phone"),
    (r"current location|where are you (based|located)|city|şehir|konum", "current_location"),
    (r"current (company|employer)|organi[sz]ation", "current_company"),
    (r"linkedin", "linkedin_url"), (r"github|portfolio|website|personal site", "portfolio_or_github_url"),
    (r"universit|school|college|üniversite", "university"),
    (r"degree|graduat|mezun", "degree_completed"),
    (r"based in turkey|located in turkey|live in turkey|türkiye.*(ikamet|yaşıyor)", "based_in_turkey"),
    (r"authori[sz]ed to work|work permit|right to work|çalışma izni", "authorized_to_work_in_turkey"),
    (r"sponsor", "requires_sponsorship_outside_turkey"),
    (r"english|ingilizce", "english_level"), (r"language", "languages"),
    (r"golang|\bgo\b", "golang_experience"),
    (r"which ai|ai-powered tools|tools do you (actively )?use", "ai_tools_used"),
    (r"experience using ai|ai proficiency", "ai_proficiency_statement"),
    (r"how long .*ai assistant|ai assistants? in your daily", "ai_assistants_daily_use"),
    (r"agentic|workflow|plugin", "agentic_workflow_experience"),
    (r"future (job )?opportunit|marketing|contact me about", "marketing_or_future_contact_consent"),
    (r"resume|cv\b|özgeçmiş", "_cv"), (r"cover letter|ön yazı", "_cover"),
]
# Always pause, even if a rule would match (sensitive or not pre-approved).
HARD_PAUSE = re.compile(
    r"salary|ücret|maaş|notice period|start date|başlama|relocat|gender|cinsiyet|ethnic|race|disab|veteran|"
    r"criminal|background check|reference|referans|years of experience|how many years|rate yourself", re.I)
# Pause only when no approved-answer rule matched (free text / essays).
SOFT_PAUSE = re.compile(r"why do you|motivation|tell us|describe|explain|essay|cover note|"
                        r"additional information|anything else", re.I)


class LeverForm(HTMLParser):
    """Collect (label, required, kind, options) from a Lever apply page."""

    def __init__(self):
        super().__init__()
        self.fields, self._label, self._in_label, self._opts, self._kind = [], "", False, [], ""
        self._in_q = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "li" and "application-question" in cls:
            self._flush()
            self._in_q = 1
        if self._in_q and tag == "div" and ("application-label" in cls):
            self._in_label = True
        if self._in_q and tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self._kind = self._kind or (a.get("type") or tag)
            if a.get("type") in ("radio", "checkbox") and a.get("value"):
                self._opts.append(a["value"])

    def handle_endtag(self, tag):
        if tag == "div" and self._in_label:
            self._in_label = False

    def handle_data(self, data):
        if self._in_label:
            self._label += data

    def _flush(self):
        label = re.sub(r"\s+", " ", self._label).strip()
        if label:
            self.fields.append({"label": label.rstrip("✱* "), "required": "✱" in label or "*" in label,
                                "kind": self._kind, "options": self._opts[:8]})
        self._label, self._opts, self._kind = "", [], ""

    def close(self):
        self._flush()
        super().close()


def lever_fields(url: str) -> list[dict]:
    apply_url = url.rstrip("/") + ("" if url.rstrip("/").endswith("/apply") else "/apply")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "form.html"
        subprocess.run([sys.executable, str(ROOT / "tools/fetch_page.py"), apply_url, "--out", str(out)],
                       capture_output=True, text=True, timeout=60, check=True)
        parser = LeverForm()
        parser.feed(out.read_text(encoding="utf-8", errors="ignore"))
        parser.close()
    return parser.fields


def greenhouse_fields(url: str) -> list[dict]:
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url) or \
        re.search(r"for=([\w-]+).*?token=(\d+)", url)
    if not m:
        raise ValueError("cannot parse Greenhouse board/job id")
    api = f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}?questions=true"
    with urllib.request.urlopen(api, timeout=20) as r:
        data = json.loads(r.read())
    fields = []
    for q in data.get("questions", []):
        opts = [v.get("label") for f in q.get("fields", []) for v in f.get("values", []) if v.get("label")]
        kind = (q.get("fields") or [{}])[0].get("type", "")
        fields.append({"label": html.unescape(q.get("label", "")), "required": q.get("required", False),
                       "kind": kind, "options": opts[:8]})
    return fields


def plan_for(field: dict) -> tuple[str, str]:
    label = field["label"]
    if HARD_PAUSE.search(label):
        return "PAUSE", "needs the candidate (see form_answers.json pause_on)"
    for pattern, key in RULES:
        if re.search(pattern, label, re.I):
            if key == "_cv":
                return "UPLOAD", "packet CV PDF (candidate attaches)"
            if key == "_cover":
                return "UPLOAD", "packet cover letter PDF (candidate attaches)"
            return "ANSWER", str(ANS.get(key))
    if SOFT_PAUSE.search(label):
        return "PAUSE", "free-text answer needs the candidate"
    return "PAUSE", "no approved answer for this question"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    slug = sys.argv[1]
    folder = APPS / slug
    packet = load_json(folder / "packet.json", None)
    if packet is None:
        print(f"no packet for {slug}", file=sys.stderr)
        return 1
    url = packet["url"]
    host = re.sub(r"^https?://([^/]+).*", r"\1", url)
    try:
        if "lever.co" in host:
            fields, source = lever_fields(url), "Lever application form"
        elif "greenhouse.io" in host:
            fields, source = greenhouse_fields(url), "Greenhouse application API"
        else:
            fields, source = [], None
    except Exception as exc:  # noqa: BLE001
        fields, source = [], None
        log_event("blocker", area="form_plan", slug=slug, detail=f"form read failed: {type(exc).__name__}")

    lines = [f"# Application form plan: {packet['company']} - {packet['role']}", "",
             f"Posting: {url}", ""]
    pauses = 0
    if source:
        lines += [f"Read from: {source}. Values come only from autopilot/form_answers.json.", "",
                  "| # | Field | Req | Action | Value / reason |", "|---|---|---|---|---|"]
        for i, f in enumerate(fields, 1):
            action, value = plan_for(f)
            pauses += action == "PAUSE"
            opts = f" (options: {', '.join(f['options'])})" if f["options"] else ""
            lines.append(f"| {i} | {f['label']}{opts} | {'yes' if f['required'] else ''} | {action} | {value} |")
    else:
        how = ("LinkedIn: open the posting and press Apply. Easy Apply needs your LinkedIn login; "
               "an external Apply link leads to the employer's form." if "linkedin" in host
               else "Open the posting and follow its Apply link.")
        lines += [how, "", "Fields could not be read in advance; they are read at submission time and "
                  "anything not in form_answers.json pauses for you."]
        pauses = -1
    lines += ["", f"CV: {packet.get('cv_pdf')}", f"Cover letter: {packet.get('cover_pdf') or '(none)'}"]
    (folder / "form_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    packet["form_plan"] = {"source": source, "fields": len(fields), "pauses": pauses}
    save_json(folder / "packet.json", packet)
    log_event("form_plan_ready", slug=slug, fields=len(fields), pauses=pauses)
    print(f"form_plan.md written: {len(fields)} fields, {pauses} pause(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
