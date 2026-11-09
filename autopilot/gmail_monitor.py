#!/usr/bin/env python3
"""Read-only Gmail monitor for job-application responses (no LLM). Run by launchd every 15 min.

Privacy: Gmail is queried ONLY for job-related mail (ATS senders, companies in the
tracker, recruiting keywords) within the last few days, and only headers plus
Gmail's short snippet are fetched - never full bodies or attachments. Nothing is
sent, deleted, labelled or changed (the token is gmail.readonly).

For each new relevant message: classify (EN/TR rules), match to a tracker row,
log an event, update the tracker for clear signals, append to outcome.md, and
send an immediate Telegram alert for important ones. Processed message ids are
remembered so nothing is handled twice.

Usage: gmail_monitor.py [--check] [--days N]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (APPS, STATE, TRACKER, load_json, log_event, notify, now,  # noqa: E402
                    save_json, single_instance, tracker_rows)

CONF = Path(os.path.expanduser("~/.config/job-search"))
CLIENT, TOKEN = CONF / "gmail_client_secret.json", CONF / "gmail_token.json"
STATE_FILE = STATE / "gmail_state.json"
API = "https://gmail.googleapis.com/gmail/v1/users/me"

ATS_SENDERS = ["lever.co", "hire.lever.co", "greenhouse.io", "greenhouse-mail.io", "workablemail.com",
               "workable.com", "myworkday.com", "smartrecruiters.com", "ashbyhq.com", "teamtailor.com",
               "recruitee.com", "personio.de", "bamboohr.com", "kariyer.net", "youthall.com",
               "jobs-noreply@linkedin.com", "hackerrank.com", "codility.com", "testgorilla.com"]
KEYWORDS = ["application", "applying", "interview", "assessment", "offer", "candidate", "recruit",
            "başvuru", "mülakat", "görüşme", "değerlendirme", "teklif", "aday", "işe alım"]

# (type, regex) - first match wins; order matters.
CLASSES = [
    ("offer", r"job offer|offer letter|pleased to offer|iş teklifi|teklif mektubu"),
    ("interview_invitation", r"interview|mülakat|görüşme (daveti|planla|ayarla)|schedule a (call|chat)|"
                             r"invite you to|davet|calendly|book a time"),
    ("assessment", r"assessment|coding (test|challenge)|take-?home|hackerrank|codility|testgorilla|case study|"
                   r"değerlendirme testi|teknik test"),
    ("rejection", r"unfortunately|regret to|not (be )?moving forward|other candidates|decided not to|"
                  r"position has been filled|maalesef|olumsuz|değerlendirme(miz)? sonucunda.*(devam|uygun)"),
    ("info_request", r"could you (please )?(send|provide|share)|please (send|provide|share|complete)|"
                     r"additional information|eksik|bilgi talep|lütfen.*(gönder|ilet)"),
    ("application_confirmation", r"thank(s| you) for (your )?appl|application (was )?received|we received your|"
                                 r"başvurunuz (alınmıştır|alındı)|başvurunuz için teşekkür"),
]
IMPORTANT = {"offer", "interview_invitation", "assessment", "rejection", "info_request", "needs_review"}
STATUS_FOR = {"interview_invitation": "interview", "offer": "offer", "rejection": "rejected"}
FINAL = {"hired", "rejected", "no_response", "no response", "offer_declined", "offer declined", "withdrawn"}


def access_token() -> str:
    raw = json.loads(CLIENT.read_text())
    c = raw.get("installed") or raw.get("web") or {}
    tok = json.loads(TOKEN.read_text())
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                   "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=body, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def gget(token: str, path: str, params: list[tuple[str, str]]) -> dict:
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def company_terms(rows: list[dict]) -> dict[str, dict]:
    """Map lowercase search term -> tracker row (company names of open applications)."""
    terms = {}
    for r in rows:
        if (r.get("status") or "").lower() in FINAL or (r.get("status") or "") == "drafted":
            continue
        name = (r.get("company") or "").strip()
        if not name:
            continue
        terms[name.lower()] = r
        first = re.sub(r"[^a-z0-9]", "", name.lower().split()[0])
        if len(first) >= 4:
            terms[first] = r
    return terms


def build_query(terms: dict, days: int) -> str:
    froms = " OR ".join(f"from:{s}" for s in ATS_SENDERS)
    names = " OR ".join(f'"{t}"' for t in sorted(terms) if " " in t or len(t) >= 4)
    subj = " OR ".join(f"subject:{k}" for k in KEYWORDS)
    parts = [froms, subj] + ([names] if names else [])
    return f"newer_than:{days}d -in:chats -in:sent -in:drafts (" + " OR ".join(parts) + ")"


def classify(text: str) -> str:
    hits = {kind for kind, pattern in CLASSES if re.search(pattern, text, re.I)}
    if "rejection" in hits and hits & {"interview_invitation", "offer", "assessment"}:
        return "needs_review"   # mixed signals: alert, never change the tracker
    for kind, pattern in CLASSES:
        if re.search(pattern, text, re.I):
            return kind
    return "recruiter_message"


def update_tracker(row: dict, kind: str, subject: str) -> str | None:
    """Append a dated note; advance status for clear signals. Returns new status or None."""
    rows = list(csv.DictReader(TRACKER.open(encoding="utf-8")))
    fields = list(rows[0].keys()) if rows else []
    changed = None
    clean = re.sub(r"[,\"\r\n]", " ", subject)[:80]
    for r in rows:
        if r["company"] == row["company"] and r["role"] == row["role"]:
            r["notes"] = (r.get("notes") or "") + f" | {now().date()} email: {kind} ({clean})"
            new = STATUS_FOR.get(kind)
            order = ["applied", "interview", "offer"]
            cur = (r.get("status") or "").lower()
            if new and cur not in FINAL and (new == "rejected" or order.index(new) > (order.index(cur) if cur in order else -1)):
                r["status"] = new
                changed = new
    with TRACKER.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return changed


def note_outcome(row: dict, kind: str, subject: str, sender: str) -> None:
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "", f"{row['company']}_{row['role']}".lower().replace(" ", "_"))).strip("_")
    path = APPS / slug / "outcome.md"
    if path.exists():
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {now().date()}: email ({kind}) from {sender}: \"{subject}\" [gmail monitor]\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify access and show what would be processed")
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    if not (CLIENT.exists() and TOKEN.exists()):
        print("gmail not configured (run autopilot/gmail_auth.py)")
        return 0
    with single_instance("gmail") as ok:
        if not ok:
            return 0
        try:
            token = access_token()
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            log_event("blocker", area="gmail", detail=f"authorization failed: {type(exc).__name__} - re-run gmail_auth.py")
            notify("Job search: Gmail access needs attention", "Re-run: python3 autopilot/gmail_auth.py")
            return 1
        state = load_json(STATE_FILE, {"processed": []})
        done = set(state["processed"])
        rows = tracker_rows()
        terms = company_terms(rows)
        q = build_query(terms, a.days)
        ids = [m["id"] for m in gget(token, "messages", [("q", q), ("maxResults", "50")]).get("messages", [])]
        new_ids = [i for i in ids if i not in done]
        handled = []
        for mid in new_ids:
            m = gget(token, f"messages/{mid}", [("format", "metadata"), ("metadataHeaders", "From"),
                                               ("metadataHeaders", "Subject"), ("metadataHeaders", "Date")])
            h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
            sender, subject, snippet = h.get("from", ""), h.get("subject", ""), m.get("snippet", "")
            blob = f"{sender} {subject} {snippet}".lower()
            row = next((r for t, r in sorted(terms.items(), key=lambda kv: -len(kv[0])) if t in blob), None)
            kind = classify(f"{subject} {snippet}")
            ats = any(s in sender.lower() for s in ATS_SENDERS)
            if row is None and not ats and kind in {"recruiter_message", "application_confirmation"}:
                done.add(mid)   # keyword-only hit with no job signal: ignore, remember id only
                continue
            handled.append({"id": mid, "kind": kind, "company": row["company"] if row else None,
                            "role": row["role"] if row else None, "from": sender[:80], "subject": subject[:120]})
            if a.check:
                continue
            new_status = update_tracker(row, kind, subject) if row else None
            if row:
                note_outcome(row, kind, subject, sender)
            log_event(f"email_{kind}", company=row["company"] if row else "", role=row["role"] if row else "",
                      sender=sender[:80], subject=subject[:120], status=new_status or "")
            if kind in IMPORTANT or (row and kind == "recruiter_message"):
                who = f"{row['company']} - {row['role']}" if row else sender[:60]
                extra = f" Tracker status -> {new_status}." if new_status else ""
                notify(f"Job search: {kind.replace('_', ' ')}", f"{who}\nSubject: {subject[:120]}{extra}\nCheck Gmail; nothing was replied to.")
            done.add(mid)
        if a.check:
            print(json.dumps({"query_ok": True, "matched_messages": len(ids), "new": len(new_ids),
                              "would_handle": handled[:10]}, ensure_ascii=False, indent=1))
            return 0
        state["processed"] = list(done)[-2000:]
        state["last_run"] = now().isoformat(timespec="seconds")
        save_json(STATE_FILE, state)
        log_event("gmail_check", new=len(new_ids), handled=len(handled))
        print(f"gmail: {len(new_ids)} new, {len(handled)} handled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
