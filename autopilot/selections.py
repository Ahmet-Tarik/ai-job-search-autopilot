#!/usr/bin/env python3
"""Shortlist selections made by the candidate (Telegram 'apply to N' or Claude chat).

Usage:
  selections.py list [--status selected]
  selections.py set KEY STATUS [--detail TEXT]     STATUS: preparing | prepared | blocked | skipped | closed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import STATE, load_json, log_event, now, save_json  # noqa: E402

SELECTIONS = STATE / "selections.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    st = sub.add_parser("set")
    st.add_argument("key")
    st.add_argument("status", choices=["preparing", "prepared", "blocked", "skipped", "closed"])
    st.add_argument("--detail", default="")
    a = ap.parse_args()
    sel = load_json(SELECTIONS, [])
    if a.cmd == "list":
        print(json.dumps([s for s in sel if not a.status or s["status"] == a.status], ensure_ascii=False, indent=1))
        return 0
    hit = [s for s in sel if s["key"] == a.key and s["status"] not in {"skipped"}]
    if not hit:
        print(f"no active selection for {a.key}", file=sys.stderr)
        return 1
    for s in hit:
        s["status"], s["detail"], s["updated"] = a.status, a.detail, now().isoformat(timespec="seconds")
    save_json(SELECTIONS, sel)
    log_event(f"selection_{a.status}", key=a.key, detail=a.detail)
    print(f"{a.key}: {a.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
