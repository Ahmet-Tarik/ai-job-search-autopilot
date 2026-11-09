#!/bin/bash
# Install (or reinstall) the autopilot launchd jobs for the current user.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/LaunchAgents"
mkdir -p "$DEST"
PY="${PYTHON:-$(command -v python3)}"
"$PY" -c "import pydantic" || { echo "install pydantic for $PY first (or set PYTHON)"; exit 1; }
echo "$PY" > "$DIR/../.python"   # run.sh uses this interpreter under launchd
for f in "$DIR"/com.jobsearch.autopilot.*.plist; do
  label="$(basename "$f" .plist)"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  ROOT="$(cd "$DIR/../.." && pwd)"   # workspace root (or set JOB_SEARCH_ROOT)
  ROOT="${JOB_SEARCH_ROOT:-$ROOT}"
  sed -e "s#__PROJECT_ROOT__#$ROOT#g" -e "s#__HOME__#$HOME#g" "$f" > "$DEST/$(basename "$f")"
  launchctl bootstrap "gui/$(id -u)" "$DEST/$label.plist"
  echo "installed $label"
done
