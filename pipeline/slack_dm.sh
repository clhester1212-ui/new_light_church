#!/bin/bash
# Mac launcher for slack_dm.py (the PC uses slack_dm.cmd).
source "$(dirname "$0")/../mac/env.sh"
exec "$PYTHON" -u "$REPO/pipeline/slack_dm.py" "$@"
