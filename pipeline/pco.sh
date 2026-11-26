#!/bin/bash
# Mac launcher for pco.py (the PC uses pco.cmd).
source "$(dirname "$0")/../mac/env.sh"
exec "$PYTHON" -u "$REPO/pipeline/pco.py" "$@"
