#!/bin/bash
# Mac launcher for the New Light Cameras app (the PC uses start-ptz-tracker.bat / keep_running.py).
#   start-ptz-tracker.sh          keep it running (restarts it if it stops) - used at login
#   start-ptz-tracker.sh --open   also open the dashboard in the browser
source "$(dirname "$0")/../mac/env.sh"
cd "$REPO/ptz-tracker"
[ "$1" = "--open" ] && (sleep 3; open http://localhost:8890) &
exec "$PYTHON" "$REPO/ptz-tracker/tools/keep_running.py"
