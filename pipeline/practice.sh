#!/bin/bash
# Mac launcher for practice.py (the PC uses practice.cmd).
source "$(dirname "$0")/../mac/env.sh"
exec "$PYTHON" -u "$REPO/pipeline/practice.py" "$@"
