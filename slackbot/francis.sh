#!/bin/bash
# Mac launcher for Francis, the Slack bot (the PC uses francis.cmd). Runs the watchdog,
# which keeps one copy of the bot running and logs to work/francis.log.
source "$(dirname "$0")/../mac/env.sh"
exec "$PYTHON" "$REPO/slackbot/francis.py"
