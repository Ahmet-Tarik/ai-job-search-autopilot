#!/usr/bin/env python3
"""Calendar proposals: interviews and deadlines the candidate may add to Google Calendar.

Nothing is ever created on the calendar automatically. The email-check run
records proposals here (from interview invitations) and the details it can read;
the candidate approves in a Claude chat ("add calendar proposal 2"), and only
then Claude creates the event with the Calendar connector and marks it created.

Usage:
  calendar_proposals.py list [--status proposed]
  calendar_proposals.py set ID [--start ISO] [--end ISO] [--location TEXT] [--link URL] [--note TEXT]
  calendar_proposals.py mark ID created|declined [--event-id ID]
  calendar_proposals.py upcoming FILE.json   # store upcoming job-related events read from Calendar
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import STATE, load_json, log_event, now, save_json  # noqa: E402

PROPOSALS = STATE / "calendar_proposals.json"
UPCOMING = STATE / "calendar_upcoming.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    st = sub.add_parser("set")
    st.add_argument("id", type=int)
    for f in ("--start", "--end", "--location", "--link", "--note"):
        st.add_argument(f)
    mk = sub.add_parser("mark")
    mk.add_argument("id", type=int)
    mk.add_argument("status", choices=["created", "declined"])
    mk.add_argument("--event-id")
    up = sub.add_parser("upcoming")
    up.add_argument("file")
    a = ap.parse_args()

    props = load_json(PROPOSALS, [])
    if a.cmd == "list":
        print(json.dumps([p for p in props if not a.status or p["status"] == a.status], ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "upcoming":
        events = json.loads(Path(a.file).read_text(encoding="utf-8"))
        save_json(UPCOMING, {"updated": now().isoformat(timespec="seconds"), "events": events[:20]})
        print(f"stored {len(events)} upcoming event(s)")
        return 0
    p = next((x for x in props if x["id"] == a.id), None)
    if p is None:
        print(f"no proposal {a.id}", file=sys.stderr)
        return 1
    if a.cmd == "set":
        for key in ("start", "end", "location", "link", "note"):
            if getattr(a, key):
                p[key] = getattr(a, key)
    else:
        if a.status == "created" and p["status"] != "proposed":
            print("refusing: only a proposed item can be marked created", file=sys.stderr)
            return 1
        p["status"] = a.status
        if a.event_id:
            p["event_id"] = a.event_id
        log_event(f"calendar_{a.status}", company=p.get("company") or "", subject=p.get("subject", ""))
    save_json(PROPOSALS, props)
    print(f"proposal {a.id}: {p['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
