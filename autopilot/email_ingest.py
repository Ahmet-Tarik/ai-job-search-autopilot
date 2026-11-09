#!/usr/bin/env python3
"""Shared, deterministic processing of job-related email (used by every email source).

Input: a JSON list of messages, each {"id", "from", "subject", "snippet", "date"?,
"thread_id"?, "labels"?}. Each message gets a category (job, work, payment, account, opportunity, irrelevant) and urgency
(urgent -> Telegram alert, important -> briefing, informational -> log, irrelevant -> ignored);
see email_rules.py. Sources:
  - the Claude email-check run (official Gmail connector, search_threads summaries)
  - gmail_monitor.py (direct gmail.readonly OAuth, optional)
Both share autopilot/state/gmail_state.json, so a message is processed once no
matter which source saw it first.

For each new message: classify (EN/TR rules in gmail_monitor.CLASSES), match an
open tracker row, append notes, advance status only for clear signals, log an
email_* event, send one Telegram alert for important messages, and queue a
calendar proposal for interview invitations (never created without approval).

Usage: email_ingest.py FILE.json [--dry-run]     (or - for stdin)
Prints a JSON summary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail_monitor as gm  # noqa: E402
import email_rules as er  # noqa: E402
from common import STATE, load_json, log_event, notify, now, save_json, single_instance, tracker_rows  # noqa: E402

PROPOSALS = STATE / "calendar_proposals.json"


def process(messages: list[dict], dry_run: bool = False, source: str = "connector") -> dict:
    state = load_json(gm.STATE_FILE, {"processed": []})
    done = set(state["processed"])
    terms = gm.company_terms(tracker_rows())
    handled, ignored = [], 0
    for m in messages:
        mid = str(m.get("id") or "")
        if not mid or mid in done:
            continue
        sender, subject, snippet = m.get("from", ""), m.get("subject", ""), m.get("snippet", "")
        blob = f"{sender} {subject} {snippet}".lower()
        row = next((r for t, r in sorted(terms.items(), key=lambda kv: -len(kv[0])) if t in blob), None)
        c = er.classify_full(sender, subject, snippet, m.get("labels"), row)
        kind, urgency = c["kind"], c["urgency"]
        if urgency == "irrelevant":
            ignored += 1
            done.add(mid)
            continue
        item = {"id": mid, "category": c["category"], "kind": kind, "urgency": urgency,
                "contact": c["contact"], "company": row["company"] if row else None,
                "role": row["role"] if row else None, "from": sender[:80], "subject": subject[:120]}
        handled.append(item)
        if dry_run:
            continue
        new_status = None
        if c["category"] == "job" and row:
            new_status = gm.update_tracker(row, kind, subject)
            gm.note_outcome(row, kind, subject, sender)
        log_event(f"email_{kind}", category=c["category"], urgency=urgency, contact=c["contact"] or "",
                  flag=c.get("flag", ""),
                  company=item["company"] or "", role=item["role"] or "",
                  sender=sender[:80], subject=subject[:120], status=new_status or "", source=source)
        if kind == "interview_invitation":
            props = load_json(PROPOSALS, [])
            props.append({"id": len(props) + 1, "message_id": mid, "thread_id": m.get("thread_id"),
                          "company": item["company"], "role": item["role"], "subject": subject[:120],
                          "status": "proposed", "created": now().isoformat(timespec="seconds")})
            save_json(PROPOSALS, props)
        if urgency == "urgent":
            who = (f"{row['company']} - {row['role']}" if row else c["contact"] or sender[:60])
            extra = f" Tracker status -> {new_status}." if new_status else ""
            cal = " Say 'add calendar proposal' in Claude to schedule it." if kind == "interview_invitation" else ""
            label = "Job search" if c["category"] == "job" else "Work"
            notify(f"URGENT {label}: {kind.replace('_', ' ')}",
                   f"{who}\nSubject: {subject[:120]}{extra}{cal}\nNothing was replied to.")
        done.add(mid)
    if not dry_run:
        state["processed"] = list(done)[-3000:]
        state["last_run"] = now().isoformat(timespec="seconds")
        state["last_source"] = source
        save_json(gm.STATE_FILE, state)
        log_event("gmail_check", source=source, new=len(handled) + ignored, handled=len(handled))
    return {"handled": handled, "ignored": ignored}


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text(encoding="utf-8")
    messages = json.loads(raw)
    with single_instance("gmail") as ok:
        if not ok:
            print(json.dumps({"skipped": "another email run in progress"}))
            return 0
        print(json.dumps(process(messages, dry_run="--dry-run" in sys.argv), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
