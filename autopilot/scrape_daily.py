#!/usr/bin/env python3
"""Daily job discovery (no LLM). Run by launchd.

1. Runs the enabled portal CLIs (linkedin-search, freehire-search) with the
   queries in config.json.
2. Applies the deterministic prefilter (role-type exclusions, developer-title
   requirement, location rules) - see AUTHORIZATION.md.
3. Deduplicates against job_scraper/seen_jobs.json and the tracker via
   tools/scrape_state.py, and stores every new job: prefilter passes as
   status "new" (picked up by /rank in the Claude daily run), rejects as
   "skipped" so they are never re-fetched.
4. Logs everything to logs/events.jsonl.

Usage: scrape_daily.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, config, excluded_company, log_event, now, notify, single_instance  # noqa: E402
import ats_sources  # noqa: E402

BUN = shutil.which("bun") or "/opt/homebrew/bin/bun"
PY = sys.executable
LI = ROOT / ".agents/skills/linkedin-search/cli/src/cli.ts"
FH = ROOT / ".agents/skills/freehire-search/cli/src/cli.ts"


def run_cli(cli: Path, args: list[str]) -> tuple[list[dict], str | None]:
    cmd = [BUN, "run", str(cli), "search", *args, "--format", "json"]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return [], "timeout"
    if proc.returncode != 0:
        return [], (proc.stderr or proc.stdout).strip()[:200] or f"exit {proc.returncode}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], "unparseable output"
    return (data.get("results", []) if isinstance(data, dict) else data), None


def prefilter(hit: dict, rules: dict) -> str | None:
    """Return a rejection reason, or None when the job passes."""
    if excluded_company(hit.get("company") or ""):
        return "company permanently excluded"
    title = (hit.get("title") or "").lower()
    for pattern in rules["exclude_title"]:
        if re.search(pattern, title):
            return f"title excluded ({pattern})"
    if not any(re.search(p, title) for p in rules["require_title_any"]):
        return "not a developer title"
    if rules.get("internship_needs_tech") and re.search(rules["internship_terms"], title) \
            and not re.search(rules["tech_terms"], title):
        return "non-technical internship"
    # freehire carries structured geography; use it to drop ineligible locations.
    countries = [c.lower() for c in hit.get("countries") or []]
    mode = (hit.get("work_mode") or "").lower()
    if hit.get("_portal") == "freehire-search" and countries:
        if mode == "remote":
            if "tr" not in countries and len(countries) <= 3:
                return f"remote restricted to {','.join(countries)}"
        elif not set(countries) & set(rules["allowed_onsite_countries"]):
            return f"onsite outside allowed countries ({','.join(countries)})"
    return None


def main() -> int:
    with single_instance("scrape") as ok:
        if not ok:
            print("another discovery run is in progress; skipping")
            return 0
        return run()


def run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="search and filter, but store nothing")
    args = ap.parse_args()

    cfg = config()
    sc, rules = cfg["scrape"], cfg["prefilter"]
    jobage, limit = str(sc["jobage_days"]), str(sc["limit_per_query"])
    hits, errors = [], []

    for q in sc["linkedin"]:
        cli_args = ["-q", q["query"], "-l", q["location"], "--jobage", jobage, "--limit", limit]
        if q.get("remote"):
            cli_args += ["--remote", q["remote"]]
        res, err = run_cli(LI, cli_args)
        if err:
            errors.append(f"linkedin '{q['query']}' @ {q['location']}: {err}")
        for h in res:
            h["_portal"] = "linkedin-search"
        hits += res
        time.sleep(sc["pause_seconds_between_calls"])

    for q in sc["freehire"]:
        res, err = run_cli(FH, [*q["args"], "--jobage", jobage, "--limit", limit])
        if err:
            errors.append(f"freehire {' '.join(q['args'])}: {err}")
        for h in res:
            h["_portal"] = "freehire-search"
        hits += res
        time.sleep(sc["pause_seconds_between_calls"])

    if sc.get("ats"):
        ats_hits, ats_errors = ats_sources.fetch_all(sc["ats"], sc["pause_seconds_between_calls"] / 2)
        hits += ats_hits
        errors += ats_errors

    # Normalise to the scrape_state.py entry shape.
    today = now().date().isoformat()
    jobs = []
    for h in hits:
        if not h.get("url") or not h.get("company") or not h.get("title"):
            continue
        reason = prefilter(h, rules)
        jobs.append({
            "title": h["title"], "company": h["company"], "url": h["url"],
            "location": (h.get("location") or "")[:120], "first_seen": today,
            "posted_date": h.get("date"), "deadline": None,
            "fit": "low" if reason else "medium",
            "status": "skipped" if reason else "new",
            "portal": h["_portal"], "source": "cli",
            **({"skip_reason": reason} if reason else {}),
        })

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "jobs.json"
        path.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
        cand = json.loads(subprocess.run(
            [PY, "tools/scrape_state.py", "candidates", "--results", str(path)],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout)
        selected = cand.get("selected", [])
        added = []
        if selected and not args.dry_run:
            path.write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
            stored = json.loads(subprocess.run(
                [PY, "tools/scrape_state.py", "store", "--results", str(path)],
                cwd=ROOT, capture_output=True, text=True, check=True).stdout)
            added = stored.get("added", [])

    new = [j for j in selected if j["status"] == "new"]
    skipped = [j for j in selected if j["status"] == "skipped"]
    summary = {
        "hits": len(hits), "unique_new": len(selected), "passed_prefilter": len(new),
        "prefiltered_out": len(skipped), "stored": len(added), "errors": errors,
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        log_event("scrape_run", **summary)
        for j in new:
            log_event("job_discovered", company=j["company"], title=j["title"],
                      url=j["url"], location=j["location"], portal=j["portal"])
        for j in skipped:
            log_event("job_prefiltered_out", company=j["company"], title=j["title"],
                      reason=j.get("skip_reason"))
        if errors and not hits:
            log_event("blocker", area="scrape", detail="every portal call failed")
            notify("Job search: scrape failed", "All portal searches failed today. Check autopilot/logs.")
    print(json.dumps({**summary, "sample_new": [f"{j['company']} | {j['title']}" for j in new[:15]],
                      "sample_skipped": [f"{j['title']} -> {j.get('skip_reason')}" for j in skipped[:10]]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
