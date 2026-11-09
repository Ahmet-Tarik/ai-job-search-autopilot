#!/bin/bash
# Stop and remove the autopilot launchd jobs. State and logs are kept.
set -u
for p in "$HOME"/Library/LaunchAgents/com.jobsearch.autopilot.*.plist; do
  [ -e "$p" ] || continue
  label="$(basename "$p" .plist)"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$p"
  echo "removed $label"
done
