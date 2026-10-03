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
| `work/<date>/` | Per-Sunday working files (transcript, plan, sermon.mp4, slides) — gitignored |

## Run it by hand

Ask Claude in a session opened on this folder: *"Run the new-light-sunday skill for today"*,
or for a dry run on a past Sunday: *"Run the new-light-sunday skill in TEST mode for 2026-09-27"*.
