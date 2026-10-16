# New Light Church — Sunday media automation

Runs on the church laptop every **Sunday at 1:00 pm** (scheduled task in the Claude
desktop app) and turns that morning's ATEM recording into everything below.

## What it does each Sunday

1. **Finds the recording** — the `Worship…` file for that Sunday on whichever *Crucial X6*
   drive is plugged in (D:), or in `Dropbox\ATEM Recordings`. Waits for sync to finish.
2. **Reads the pastor's notes** from `Dropbox\Sound Booth\Sermons` (series, title, points,
   scriptures).
3. **Transcribes the service** on this laptop (Whisper — no YouTube captions needed).
4. **Marks the sections** — each worship song, the sermon start → end of altar call, and
   any date-specific parts (parking lot, announcements, "welcome radio & internet", events…).
5. **Sermon video** — cut from the 1080p master, date-specific parts removed, lower thirds
   ("Pastor Dale Hester" by default, "Pastor Corey Hester" when he preaches), high quality.
6. **Uploads the sermon to YouTube** as **Unlisted** with title, description, tags.
7. **Radio MP3** — `Dropbox\Radio Files\<year>\<date> - <Series WkN - Title>.mp3`.
8. **Worship songs** — each song as a 480p reference video in
   `Dropbox\Worship Songs\<date>\<date> - <n> - <Song>.mp4`, and adds them to `Song Log.csv`.
9. **OpusClip** — sends the new sermon video for social clips.
10. **Instagram** — 5 recap slides from what was actually preached.
11. **Emails a summary** to clhester1212@gmail.com with links, the cuts made (to spot-check),
    and the slides attached.

## Setup checklist

- [x] Git, Python 3.12, ffmpeg, yt-dlp installed
- [x] Repo cloned to `C:\Users\NewLight\new_light_church`
- [x] Gmail connector
- [x] OpusClip connector
- [x] Dropbox desktop sync (`C:\Users\NewLight\Dropbox`), Worship Songs folder joined
- [ ] YouTube credentials pasted into `secrets\youtube.json` (never committed)
- [ ] Scheduled task "New Light — Sunday 1pm media pipeline" created in the Claude app
- [ ] Laptop on, Claude app open, X6 drive plugged in on Sundays at 1pm

## Files

| Path | What |
|---|---|
| `.claude/skills/new-light-sunday/SKILL.md` | The procedure Claude follows (judgment calls) |
| `pipeline/nlc.py` (`nlc.cmd` launcher) | The mechanical steps: find, notes, transcribe, render, slides, upload |
| `config.json` | Folders, default speaker, quality, YouTube privacy |
| `secrets/youtube.json` | YouTube OAuth credentials — gitignored |
| `slackbot/francis.py` (`francis.cmd` launcher) | Slack bot that relays your messages to Claude here |
| `secrets/slack.json` | Slack tokens + your Slack user ID for Francis — gitignored |
| `work/<date>/` | Per-Sunday working files (transcript, plan, sermon.mp4, slides) — gitignored |

## Francis — message this computer from Slack

`slackbot/francis.py` is a small Slack bot. DM **Francis** from your phone ("the email
misspelled my name, can we fix that?") and it runs Claude Code here in this folder, then
replies in the thread. Replying in the same thread continues that conversation. It only
listens to the Slack user in `secrets/slack.json` → `allowed_user_id`, runs Claude in
`auto` permission mode, and asks in the thread before anything public or hard to undo.
It starts at login (shortcut in the Windows Startup folder); log: `work/francis.log`.

One-time setup:
1. **Sign Claude in** with the church account: open a terminal and run
   `& (Get-ChildItem "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code" -Recurse -Filter claude.exe | Sort LastWriteTime | Select -Last 1).FullName auth login`
2. **Create the Slack app**: api.slack.com/apps → *Create New App* → *From a manifest* → pick
   your workspace → paste `slackbot/slack-app-manifest.yaml` → *Create* → *Install to Workspace*.
3. **Save the two tokens**: copy each one, then double-click `slackbot\save-slack-key.cmd`
   (it puts the clipboard key in `secrets/slack.json`). The Bot User OAuth Token (`xoxb-…`) is
   under *OAuth & Permissions*; the app-level token (`xapp-…`) under *Basic Information* →
   *App-Level Tokens* → *Generate* with scope `connections:write`. Check with
   `slackbot\check-slack-keys.ps1`.
4. **Start it** (double-click `slackbot\francis.cmd`), DM Francis anything — it replies with
   your Slack user ID. Put that in `allowed_user_id`, then restart (end `pythonw.exe` in Task
   Manager and double-click `francis.cmd` again).

The laptop must be on, awake and signed in for Francis to answer.

## Run it by hand

Ask Claude in a session opened on this folder: *"Run the new-light-sunday skill for today"*,
or for a dry run on a past Sunday: *"Run the new-light-sunday skill in TEST mode for 2026-09-27"*.
