#!/bin/bash
# Mac launcher for nlc.py (the PC uses nlc.cmd).
source "$(dirname "$0")/../mac/env.sh"
exec "$PYTHON" -u "$REPO/pipeline/nlc.py" "$@"
