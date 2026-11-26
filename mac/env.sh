#!/bin/bash
# Shared settings for the Mac launchers (the Mac version of the PATH lines in the .cmd files).
# Start-at-login jobs get a very short PATH, so add Homebrew and the usual install spots.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO
# Python: the project's own .venv if it exists, otherwise python3.12, otherwise python3.
if [ -x "$REPO/.venv/bin/python" ]; then PYTHON="$REPO/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then PYTHON="$(command -v python3.12)"
else PYTHON="$(command -v python3)"; fi
export PYTHON
# Companion's bundled Node (runs the ATEM scripts); falls back to any node.
NODE=""
for n in /Applications/Companion.app/Contents/Resources/node-runtimes/node*/bin/node \
         /Applications/Companion.app/Contents/Resources/node-runtimes/node*/node; do
  [ -x "$n" ] && NODE="$n"
done
[ -z "$NODE" ] && NODE="$(command -v node || true)"
export NODE
