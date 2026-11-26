#!/bin/bash
# Mac launcher for voices.py (the PC uses voices.cmd).
source "$(dirname "$0")/../mac/env.sh"
exec "$PYTHON" -u "$REPO/pipeline/voices.py" "$@"
