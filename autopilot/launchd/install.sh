#!/bin/bash
# Install (or reinstall) the autopilot launchd jobs for the current user.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/LaunchAgents"
mkdir -p "$DEST"
for f in "$DIR"/com.jobsearch.autopilot.*.plist; do
  label="$(basename "$f" .plist)"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  cp "$f" "$DEST/"
  launchctl bootstrap "gui/$(id -u)" "$DEST/$label.plist"
  echo "installed $label"
done
