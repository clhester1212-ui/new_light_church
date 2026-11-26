#!/bin/bash
# Mac launcher for atem_name.js (the PC uses atem_name.cmd): sets the Mini's recording name.
source "$(dirname "$0")/../mac/env.sh"
if [ -z "$NODE" ]; then echo "No Node found (install Companion, or Node)"; exit 1; fi
exec "$NODE" "$REPO/pipeline/atem_name.js" "$@"
