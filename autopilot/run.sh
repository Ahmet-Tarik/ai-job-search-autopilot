#!/bin/bash
# launchd entry point: run one autopilot step with a predictable environment.
# Usage: run.sh scrape | morning | evening
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/Library/TinyTeX/bin/universal-darwin"
PY=python3
LOG="$ROOT/autopilot/logs/launchd-$1.log"
mkdir -p "$ROOT/autopilot/logs"
cd "$ROOT" || exit 1
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') $1"
  case "$1" in
    scrape)  "$PY" autopilot/scrape_daily.py; "$PY" autopilot/local_screen.py --limit 15; "$PY" autopilot/local_audit.py --auto-disable ;;  # local screen: no-op unless local_llm.enabled
    morning) "$PY" autopilot/briefing.py morning; "$PY" autopilot/shortlist.py build --send ;;
    inbox)   "$PY" autopilot/telegram_inbox.py ;;
    evening) "$PY" autopilot/drive_backup.py; "$PY" autopilot/briefing.py evening ;;
    gmail)   "$PY" autopilot/gmail_monitor.py ;;
    *) echo "unknown step $1"; exit 2 ;;
  esac
  echo "exit=$?"
} >> "$LOG" 2>&1
