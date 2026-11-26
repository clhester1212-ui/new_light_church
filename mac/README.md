# Running New Light's programs on the Mac Mini

The same code runs on the booth PC (Windows) and the Mac. The differences are handled in:

- `pipeline/platform_paths.py`: on a Mac, the Windows folder paths in `config.json` are translated
  automatically (`G:\…` → the Mac's Google Drive folder, `C:\Users\NewLight\Dropbox\…` → the Mac's Dropbox),
  and it finds the Crucial X6 under `/Volumes`, uses the Trash instead of the Recycle Bin, and finds Companion's files.
  If a folder is somewhere unusual, add `"mac_paths": {"google_drive": "...", "dropbox": "..."}` to `config.json`.
- `*.sh` launchers next to each `.cmd` (`pipeline/`, `slackbot/`, `ptz-tracker/`) and `mac/env.sh` (finds Python, ffmpeg, Companion's Node).
- `mac/launch-agents/` + `mac/install-launch-agents.sh`: the Mac's version of the PC's scheduled tasks.

## One-time setup

```bash
brew install python@3.12 ffmpeg git
git clone https://github.com/clhester1212-ui/new_light_church && cd new_light_church
git checkout mac-port
python3.12 -m venv .venv && .venv/bin/pip install -r mac/requirements.txt
# secrets/: paste youtube.json, client_secret.json, pco.json, slack.json from the password manager
# work/ and ptz-tracker/data, ptz-tracker/models: copy from the Google Drive "New Light - PC to Mac Move" folder
```

## Quick checks (safe to run while the PC is still in charge)

```bash
pipeline/atem_name.sh --read          # reads the Mini's recording name (changes nothing)
pipeline/nlc.sh pending               # looks for a new Sunday recording on the Crucial X6
ptz-tracker/start-ptz-tracker.sh --open   # Cameras app at http://localhost:8890
```

## Start-at-login jobs

```bash
mac/install-launch-agents.sh                    # list them, on/off
mac/install-launch-agents.sh --on ptz-tracker   # Cameras app at login: fine beside the PC
```

**Cutover day only** (after turning off the PC's tasks "New Light - ATEM Worship name",
"New Light - ATEM Practice name", "New Light PTZ Tracker" and Francis's Startup shortcut):

```bash
mac/install-launch-agents.sh --on atem-worship-name atem-practice-name francis
```

Also move the two Claude scheduled tasks (noon Sunday routine, 8 pm practice routine) at the same time:
turn them off on the PC, on on the Mac.

## Not yet converted

- `.claude/skills/*/SKILL.md` still describe Windows commands (`nlc.cmd …`). On the Mac, use the `.sh` launchers
  with the same arguments; update the skills once the Mac is in charge.
- `ptz-tracker/tools/open_dashboard.py` (the PC desktop button) is Windows-only; on the Mac use `start-ptz-tracker.sh --open`.
- `bridge-watch/` is retired (the Streaming Bridge was removed) and isn't converted.
