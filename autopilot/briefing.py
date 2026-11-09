#!/usr/bin/env python3
"""Morning (09:00) and evening (22:00) briefings, Europe/Istanbul. Run by launchd.

Built only from local state - the event log, seen_jobs.json, packets and the
tracker - so it costs no Claude usage and works while the Claude app is closed.
Sends via Telegram (macOS notification fallback), writes reports/<date>-<slot>.md,
and sends at most once per slot per day unless --force.

Usage: briefing.py morning|evening [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (REPORTS, STATE, config, load_json, log_event, now, notify,  # noqa: E402
                    read_events, save_json, seen_jobs, tracker_rows)
from packets import all_packets  # noqa: E402

STATE_FILE = STATE / "briefing_state.json"
FINAL = {"hired", "rejected", "no_response", "no response", "offer_declined", "offer declined", "withdrawn"}


def fmt_job(e: dict) -> str:
    flags = []
    if e.get("location_verdict") == "FLAG":
        flags.append("location flag")
    if e.get("language_gate") == "FLAG":
        flags.append("language flag")
    tail = f" [{', '.join(flags)}]" if flags else ""
    loc = (e.get('location') or '?')
    loc = loc if len(loc) <= 50 else loc[:47] + '...'
    return f"- {e.get('rank_score')} {e.get('title')} @ {e.get('company')} ({loc}){tail}\n  {e.get('url')}"


def build(slot: str) -> str:
    cfg, t = config(), now()
    state = load_json(STATE_FILE, {})
    last = state.get("last_sent_at")
    since = dt.datetime.fromisoformat(last) if last else t - dt.timedelta(hours=24)
    today0 = t.replace(hour=0, minute=0, second=0, microsecond=0)
    events = read_events(since)
    today_events = read_events(today0)
    seen = seen_jobs()

    applied = [(r.get("company", "").lower(), r.get("role", "").lower()) for r in tracker_rows()]

    def already_applied(e: dict) -> bool:
        c, title = e.get("company", "").lower(), e.get("title", "").lower()
        return any(c == ac and role and role in title for ac, role in applied)

    ranked_new = sorted(
        [e for k, e in seen.items() if e.get("status") == "ranked"
         and k not in cfg["drafting"].get("do_not_draft", {})
         and e.get("location_verdict") != "FAIL" and e.get("language_gate") != "FAIL"
         and not already_applied(e)
         and (e.get("rank_date") or "") >= since.date().isoformat()
         and (e.get("rank_score") or 0) >= cfg["briefings"]["consider_score"]],
        key=lambda e: -(e.get("rank_score") or 0))
    awaiting_rank = sum(1 for e in seen.values() if e.get("status") in {"new", "unreachable"})
    packets = all_packets()
    ready = [p for p in packets if p["status"] == "ready_for_approval"]
    blocked = [p for p in packets if p["status"] == "blocked"]
    submitted = [e for e in (today_events if slot == "evening" else events) if e["type"] == "application_submitted"]
    closed_companies = {p["company"] for p in packets if p["status"] in {"closed", "declined", "submitted"}}
    blockers = [e for e in events if e["type"] == "blocker" and e.get("company") not in closed_companies]
    outcomes = [e for e in events if e["type"] in {"outcome_update", "followup_logged"}]
    emails = [e for e in events if e["type"].startswith("email_")]
    for e in emails:  # events logged before urgency levels existed
        if not e.get("urgency"):
            e["urgency"] = ("informational" if e["type"] == "email_application_confirmation"
                            else "urgent" if e["type"] in {"email_interview_invitation", "email_assessment",
                                                           "email_offer", "email_info_request", "email_needs_review"}
                            else "important")
    open_apps = [r for r in tracker_rows() if (r.get("status") or "").lower() not in FINAL]

    # Pipeline health: did the background parts actually run?
    last_scrape = next((e for e in reversed(read_events()) if e["type"] == "scrape_run"), None)
    last_ai = next((e for e in reversed(read_events()) if e["type"] == "daily_run_complete"), None)
    health = []
    for name, ev in (("job discovery", last_scrape), ("AI ranking/drafting run", last_ai)):
        if not ev:
            health.append(f"- {name}: has not run yet")
        elif t - dt.datetime.fromisoformat(ev["ts"]) > dt.timedelta(hours=27):
            health.append(f"- {name}: last ran {ev['ts'][:16]} (overdue - is the Mac/Claude app running?)")

    title = f"Job search {'morning' if slot == 'morning' else 'evening'} briefing - {t:%a %d %b %H:%M}"
    L = []
    if slot == "morning":
        L.append(f"New relevant jobs (score >= {cfg['briefings']['consider_score']}): {len(ranked_new)}")
        L += [fmt_job(e) for e in ranked_new[:8]]
        if awaiting_rank:
            L.append(f"{awaiting_rank} discovered jobs are waiting for the AI ranking run.")
        L.append(f"\nApplications submitted since last report: {len(submitted)}")
        L += [f"- {e.get('company')} - {e.get('role')}" for e in submitted]
    else:
        counts = Counter(e["type"] for e in today_events)
        L.append("Work completed today:")
        L.append(f"- jobs discovered: {counts.get('job_discovered', 0)} (filtered out: {counts.get('job_prefiltered_out', 0)})")
        L.append(f"- jobs ranked: {counts.get('job_ranked', 0)}")
        L.append(f"- application packets prepared: {counts.get('packet_ready', 0)}")
        L.append(f"- applications submitted: {len(submitted)}")
        L += [f"  - {e.get('company')} - {e.get('role')}" for e in submitted]
        if ranked_new:
            L.append("\nNotable new jobs:")
            L += [fmt_job(e) for e in ranked_new[:5]]

    L.append(f"\nReady for your approval: {len(ready)}")
    L += [f"- #{p.get('id')} {p['company']} - {p['role']} (score {p.get('score')})"
          + (" [application route unverified - checked before submission]" if p.get("route_status") == "unverified" else "")
          + f"\n  {p.get('url')}" for p in ready]
    if ready:
        L.append("To submit: open Claude in the job-search project and say e.g. 'submit packets 1 and 3'.")
    if blocked or blockers:
        L.append("\nBlockers / needs you:")
        L += [f"- #{p.get('id')} {p['company']}: {(p['history'][-1]['detail'] if p.get('history') else '')}" for p in blocked]
        L += [f"- {e.get('area') or e.get('company') or ''}: {e.get('detail', '')}" for e in blockers if not e.get("slug")]
    if outcomes:
        L.append("\nApplication status changes:")
        L += [f"- {e.get('company')}: {e.get('status') or e.get('detail')}" for e in outcomes]
    L.append(f"\nOpen applications in tracker: {len(open_apps)}")
    L += [f"- {r['company']} - {r['role']}: {r['status']}" for r in open_apps[:8]]
    props = [p for p in load_json(STATE / "calendar_proposals.json", []) if p.get("status") == "proposed"]
    upcoming = load_json(STATE / "calendar_upcoming.json", {}).get("events", [])
    if upcoming:
        L.append("\nUpcoming interviews (Google Calendar):")
        L += [f"- {(ev.get('start') or '')[:16]} {ev.get('summary', '')} {('@ ' + ev['location']) if ev.get('location') else ''}" for ev in upcoming[:5]]
    if props:
        L.append("\nCalendar proposals (say 'add calendar proposal N' in Claude to create):")
        L += [f"- #{p['id']} {p.get('company') or ''}: {p.get('subject', '')[:60]} {('at ' + p['start']) if p.get('start') else '(time not fixed)'}" for p in props]
    backup = next((e for e in reversed(read_events()) if e["type"] in {"drive_backup", "drive_backup_skipped"}), None)
    if backup and backup["type"] == "drive_backup_skipped" and slot == "evening":
        health.append(f"- Drive backup skipped: {backup.get('reason', '')}")
    gmail_last = next((e for e in reversed(read_events()) if e["type"] == "gmail_check"), None)
    if gmail_last is None:
        L.append("\nEmail: Gmail monitoring not connected yet.")
    else:
        def line(e: dict) -> str:
            who = e.get("company") or e.get("contact") or e.get("sender", "")
            tail = f" -> tracker {e['status']}" if e.get("status") else ""
            return f"- [{e.get('urgency', '')}] {e['type'][6:].replace('_', ' ')}: {who} - {e.get('subject', '')[:70]}{tail}"
        job_mail = [e for e in emails if e.get("category", "job") == "job" and e.get("urgency") != "informational"]
        review_mail = [e for e in emails if e.get("urgency") == "needs_review"]
        work_mail = [e for e in emails if e.get("category", "job") != "job"
                     and e.get("urgency") not in {"informational", "needs_review"}]
        info = [e for e in emails if e.get("urgency") == "informational"]
        L.append(f"\nEmail (read-only): {len(emails)} relevant message(s) since last report")
        if job_mail:
            L.append("Job applications:")
            L += [line(e) for e in job_mail[:8]]
        if work_mail:
            L.append("Work, payments and contracts:")
            L += [line(e) for e in work_mail[:8]]
        if review_mail:
            L.append("Needs your review (unknown senders - not verified, nothing was opened or answered):")
            L += [line(e) + (f"  [{e['flag']}]" if e.get("flag") else "") for e in review_mail[:8]]
        if info:
            L.append(f"Routine (logged only): {len(info)} confirmation(s) / notice(s)")
        if t - dt.datetime.fromisoformat(gmail_last["ts"]) > dt.timedelta(hours=5):
            health.append(f"- Gmail monitor: last check {gmail_last['ts'][:16]} (overdue)")
    if slot == "evening":
        L.append("Other phases: Phase 2 not started.")
    if health:
        L.append("\nPipeline health:")
        L += health

    prio = []
    if any(e.get("urgency") == "urgent" for e in emails):
        prio.append("Handle the urgent email(s) above (nothing was answered automatically).")
    if any(e.get("urgency") == "needs_review" for e in emails):
        prio.append("Check the unverified email(s) from unknown senders before replying to any.")
    if any(e.get("urgency") == "important" for e in emails) and not any(e.get("urgency") == "urgent" for e in emails):
        prio.append("Read the important email(s) above when you have time.")
    if props:
        prio.append(f"Decide on {len(props)} calendar proposal(s).")
    if ready:
        prio.append(f"Approve or decline {len(ready)} ready application(s).")
    if blocked:
        prio.append(f"Unblock {len(blocked)} paused application(s).")
    if health:
        prio.append("Open the Claude app so the scheduled AI run can catch up.")
    if not prio:
        prio.append("Nothing needs you - study and build.")
    L.append(f"\n{'Priorities today' if slot == 'morning' else 'Priorities for tomorrow'}:")
    L += [f"- {p}" for p in prio]
    return title + "\n\n" + "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slot", choices=["morning", "evening"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    t = now()
    state = load_json(STATE_FILE, {})
    key = f"{t.date().isoformat()}-{a.slot}"
    if key in state.get("sent", []) and not (a.force or a.dry_run):
        print(f"already sent {key}")
        return 0
    text = build(a.slot)
    if a.dry_run:
        print(text)
        return 0
    title, body = text.split("\n\n", 1)
    (REPORTS / f"{key}.md").write_text(text + "\n", encoding="utf-8")
    channel = notify(title, body)
    state.setdefault("sent", []).append(key)
    state["sent"] = state["sent"][-60:]
    state["last_sent_at"] = t.isoformat(timespec="seconds")
    save_json(STATE_FILE, state)
    log_event("briefing_sent", slot=a.slot, channel=channel)
    print(f"{key}: sent via {channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
