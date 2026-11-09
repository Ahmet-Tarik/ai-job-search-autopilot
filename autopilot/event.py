#!/usr/bin/env python3
"""Append an event to the autopilot log from a Claude run.

Usage: event.py TYPE key=value [key=value ...]
Example: event.py job_ranked company="Acme" title="Junior Dev" score=74
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import log_event  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit(__doc__)
fields = dict(arg.split("=", 1) for arg in sys.argv[2:] if "=" in arg)
log_event(sys.argv[1], **fields)
print("logged", sys.argv[1])
