# New Light Church — Sunday media automation

A scheduled task in the Claude desktop app checks **every 15 minutes** for a new Sunday
recording on a plugged-in *Crucial X6* drive. When it finds one (plug the drive into the
laptop after service), it turns that recording into everything below. Finished files go to
the Google Drive shared drives, synced on the laptop as `G:\Shared drives\`.

## What it does for each new Sunday recording

1. **Finds the recording** — a `Worship…` file from a Sunday on whichever *Crucial X6*
   drive is plugged in (D:), dated on/after `process_recordings_since` in config.json.
2. **Reads the pastor's notes** from `Dropbox\Sound Booth\Sermons` (series, title, points,
   scriptures).
3. **Transcribes the service** on this laptop (Whisper — no YouTube captions needed).
4. **Marks the sections** — each worship song, the sermon start → end of altar call, and
   any date-specific parts (parking lot, announcements, "welcome radio & internet", events…).
5. **Sermon video** — cut from the 1080p master, date-specific parts removed, lower thirds
   ("Pastor Dale Hester" by default, "Pastor Cory Hester" when he preaches), high quality —
   saved to `Sermons\<year>\<date> - <Series WkN - Title>\`.
6. **Uploads the sermon to YouTube** as **Unlisted** with title, description, tags.
7. **Radio MP3** — next to the video in that same Sermons folder, plus a copy in
   `Media\Sermon MP3s\` (all sermon MP3s since Feb 2026 in one place).
8. **Worship songs** — each song as a 480p reference video in
   `Worship\<date>\<date> - <n> - <Song>.mp4`, and adds them to `Worship\Song Log.csv`.
9. **OpusClip** — sends the new sermon video for social clips.
10. **Instagram** — 5 recap slides in `Media\Instagram Slides\<date>\`; the DaVinci Resolve
    timeline goes to `Media\Editing Projects\`. Raw recordings stay on the X6 drive.
11. **Emails a summary** to clhester1212@gmail.com with Google Drive links and the cuts made
    (to spot-check).

## Setup checklist

- [x] Git, Python 3.12, ffmpeg, yt-dlp installed
- [x] Repo cloned to `C:\Users\NewLight\new_light_church`
- [x] Gmail and OpusClip connectors (Google Drive connector as a fallback for folder links)
- [x] Google Drive for Desktop — `G:\Shared drives\Media`, `Sermons`, `Worship`
- [x] Dropbox desktop sync — now only for reading the pastor's notes
- [ ] YouTube credentials pasted into `secrets\youtube.json` (never committed)
- [x] Scheduled task "New Light — process new Sunday recording from X6 drive" (every 15 min)
- [ ] Laptop on and awake, Claude app open, when the X6 drive is plugged in

## Files

| Path | What |
|---|---|
| `.claude/skills/new-light-sunday/SKILL.md` | The procedure Claude follows (judgment calls) |
| `pipeline/nlc.py` (`nlc.cmd` launcher) | The mechanical steps: pending, find, notes, transcribe, render, slides, upload, drive-link |
| `pipeline/atem_name.js` (`atem_name.cmd` launcher) | Sets the ATEM recording name: `Practice_<m_d_yyyy>` Wednesdays (Task Scheduler, noon), `Worship_<m_d_yyyy>` Sundays (6 am). `--read` shows current names; log in `work/atem_name.log` |
| `config.json` | Folders, default speaker, quality, YouTube privacy, ATEM Mini Extreme IP |
| `secrets/youtube.json` | YouTube OAuth credentials — gitignored |
| `slackbot/francis.py` (`francis.cmd` launcher) | Slack bot that relays your messages to Claude here |
| `secrets/slack.json` | Slack tokens + your Slack user ID for Francis — gitignored |
| `work/<date>/` | Per-Sunday working files (transcript, plan, sermon.mp4, slides) — gitignored |

## Francis — message this computer from Slack

`slackbot/francis.py` is a small Slack bot. DM **Francis** from your phone ("the email
misspelled my name, can we fix that?") and it runs Claude Code here in this folder, then
replies in the thread. Replying in the same thread continues that conversation. It only
listens to the Slack users in `secrets/slack.json` → `allowed_user_ids`, runs Claude in
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
   your Slack user ID. Add it to the `allowed_user_ids` list, then restart (end `pythonw.exe` in Task
   Manager and double-click `francis.cmd` again).

The laptop must be on, awake and signed in for Francis to answer.

## Run it by hand

Ask Claude in a session opened on this folder: *"Run the new-light-sunday skill for 2026-10-11"*,
or for a dry run on a past Sunday: *"Run the new-light-sunday skill in TEST mode for 2026-09-27"*.
