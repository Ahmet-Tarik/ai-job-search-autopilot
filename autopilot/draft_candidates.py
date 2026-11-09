#!/usr/bin/env python3
"""Deterministic draft selection for the daily AI run (AUTHORIZATION.md section 2).

Reads every 'ranked' job in seen_jobs.json and prints, as JSON, the jobs that may
be drafted today (up to the remaining daily cap), in location-priority order,
plus the jobs to list for the candidate instead.

Usage: draft_candidates.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import APPS, config, excluded_company, now, read_events, seen_jobs, tracker_rows  # noqa: E402

TURKEY = re.compile(r"t[üu]rk(iye|ey)|istanbul|İstanbul|ankara|izmir|kocaeli|bursa|antalya|\btr\b", re.I)
AZ = re.compile(r"azerbaijan|baku|bak[ıi]", re.I)
REMOTE = re.compile(r"remote|uzaktan|worldwide|anywhere|emea", re.I)


def slugify(company: str, role: str) -> str:
    s = re.sub(r"[^a-z0-9_]", "", f"{company}_{role}".lower().replace(" ", "_"))
    return re.sub(r"_+", "_", s).strip("_")


def priority(job: dict) -> int | None:
    """Lower is better. None = never auto-draft."""
    loc = f"{job.get('location') or ''} {job.get('title') or ''}"
    if AZ.search(loc):
        return None
    if re.search(r"ankara", loc, re.I):
        return 1
    if TURKEY.search(loc) and REMOTE.search(loc):
        return 2
    if TURKEY.search(loc) and not re.search(r"istanbul|İstanbul", loc, re.I):
        return 2
    if REMOTE.search(loc) and job.get("location_verdict") == "PASS":
        return 3
    if re.search(r"istanbul|İstanbul", loc, re.I):
        # Istanbul onsite/hybrid needs relocation and conflicts with university:
        # list for the candidate. Only a remote Istanbul role may be drafted.
        return 4 if REMOTE.search(loc) or job.get("remote_confirmed") else None
    if job.get("location_verdict") == "PASS":   # e.g. EU onsite where sponsorship was stated
        return 5
    return None


def main() -> int:
    cfg = config()["drafting"]
    today = now().date().isoformat()
    done_today = sum(1 for e in read_events() if e["type"] == "packet_ready" and e["ts"].startswith(today))
    remaining = max(0, cfg["daily_draft_cap"] - done_today)
    applied = [(r.get("company", "").lower(), r.get("role", "").lower()) for r in tracker_rows()]
    has_packet = {p.parent.name for p in APPS.glob("*/packet.json")}

    eligible, for_candidate = [], []
    for key, j in seen_jobs().items():
        if j.get("status") != "ranked":
            continue
        score = j.get("rank_score") or 0
        company, title = j.get("company", ""), j.get("title", "")
        if any(company.lower() == c and r and r in title.lower() for c, r in applied):
            continue
        if slugify(company, title) in has_packet:
            continue
        if j.get("location_verdict") == "FAIL" or j.get("language_gate") == "FAIL":
            continue
        if key in cfg.get("do_not_draft", {}) or excluded_company(company):
            continue
        row = {"key": key, "company": company, "title": title, "url": j.get("url"),
               "location": (j.get("location") or "")[:80], "score": score,
               "location_verdict": j.get("location_verdict"), "language_gate": j.get("language_gate"),
               "slug": slugify(company, title)}
        prio = priority(j)
        if score >= cfg["min_rank_score"] and j.get("language_gate") == "PASS" and prio is not None:
            eligible.append({**row, "priority": prio})
        elif score >= config()["briefings"]["consider_score"]:
            is_ist = re.search(r"istanbul|İstanbul", j.get("location") or "", re.I)
            reason = ("language flag" if j.get("language_gate") == "FLAG"
                      else "Istanbul onsite - relocation, your review" if prio is None and is_ist
                      else "location needs your decision" if prio is None
                      else "score below 70")
            for_candidate.append({**row, "reason": reason})

    eligible.sort(key=lambda r: (r["priority"], -r["score"]))
    print(json.dumps({"remaining_cap_today": remaining, "draft_now": eligible[:remaining],
                      "eligible_waiting": eligible[remaining:],
                      "for_candidate": sorted(for_candidate, key=lambda r: -r["score"])},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
