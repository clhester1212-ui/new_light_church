#!/bin/bash
# Turns New Light's start-at-login and scheduled jobs on or off on the Mac (the Mac version of
# the PC's Windows scheduled tasks). The job files are in mac/launch-agents/.
#
# macOS starts EVERY file in ~/Library/LaunchAgents at login, so a job is only copied there
# when it's turned on, and removed when it's turned off.
#
#   mac/install-launch-agents.sh                     list the jobs and whether each is on
#   mac/install-launch-agents.sh --on NAME ...       turn jobs on (copies + starts them)
#   mac/install-launch-agents.sh --off NAME ...      turn jobs off (stops + removes them)
#
# Jobs:
#   ptz-tracker          the Cameras app at login (fine to run beside the PC)
#   atem-worship-name    Sundays 6:00 am, names the Mini's recording  } turn these on only on
#   atem-practice-name   Wednesdays noon, names the Mini's recording  } CUTOVER DAY, after the
#   francis              the Slack bot at login                       } PC's are turned off -
#                                                                       otherwise both act at once.
set -e
source "$(dirname "$0")/env.sh"
DEST="$HOME/Library/LaunchAgents"
mkdir -p "$DEST" "$REPO/work"
DOMAIN="gui/$(id -u)"

if [ $# -eq 0 ]; then
  for src in "$REPO"/mac/launch-agents/*.plist; do
    n="$(basename "$src" .plist)"; n="${n#org.newlightchurch.}"
    if [ -f "$DEST/org.newlightchurch.$n.plist" ]; then echo "ON   $n"; else echo "off  $n"; fi
  done
  exit 0
fi

mode=""
for arg in "$@"; do
  case "$arg" in
    --on) mode=on ;;
    --off) mode=off ;;
    *)
      src="$REPO/mac/launch-agents/org.newlightchurch.$arg.plist"
      dest="$DEST/org.newlightchurch.$arg.plist"
      [ -f "$src" ] || { echo "No such job: $arg"; exit 1; }
      [ -n "$mode" ] || { echo "Say --on or --off before the job names"; exit 1; }
      launchctl bootout "$DOMAIN" "$dest" 2>/dev/null || true
      if [ "$mode" = on ]; then
        sed "s#__REPO__#$REPO#g" "$src" > "$dest"
        launchctl bootstrap "$DOMAIN" "$dest"
        echo "ON:  $arg"
      else
        rm -f "$dest"
        echo "OFF: $arg"
      fi ;;
  esac
done
