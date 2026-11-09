#!/usr/bin/env python3
"""Send a short notification to the candidate (Telegram, macOS fallback).

Usage: notify_cli.py "Title" "Body text"
Only for messages to the candidate themself - never to employers or anyone else.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import log_event, notify  # noqa: E402

if len(sys.argv) != 3:
    raise SystemExit(__doc__)
channel = notify(sys.argv[1], sys.argv[2])
log_event("notification", title=sys.argv[1], channel=channel)
print(channel)
