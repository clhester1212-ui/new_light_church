---
name: new-light-practice
description: Organizes New Light Church worship-team PRACTICE recordings. When a Crucial X6 drive is plugged in, finds each unprocessed "Practice…" recording, identifies every song the band worked on (through all the stops, restarts and talking), and saves a low-quality 480p clip of each song, labeled, into Google Drive "Worship\Practice Worship Files\<date>\" plus a Practice Log. Use when asked to run, test, or debug the practice routine, or when its scheduled task invokes it.
---

# New Light — practice recordings organizer

Runs on the church laptop from `C:\Users\NewLight\new_light_church`. No sermon, no YouTube,
no OpusClip — just the songs. A scheduled run has nobody watching: don't ask questions;
make the most reasonable call and flag doubts in the email.

All mechanical work: `cmd /c "C:\Users\NewLight\new_light_church\pipeline\practice.cmd <command> ..."`.

## Step 0 — Anything new? (the routine polls; most runs find nothing)

`practice.cmd pending --claim`
- Prints `NONE` → **stop immediately. No email, nothing else.** (Drive not plugged in, or
  everything is already done.)
- Otherwise prints `{"key", "date", "source", "gb"}` — process that one recording. One per
  run; the next poll picks up the next. `date` is when it was actually recorded (the ATEM
  re-uses old file names, so never trust the date in the name).
- `G:\Shared drives\Worship` must exist (Google Drive for Desktop running). If not, email
  that Google Drive isn't available, delete `work\practice\<key>\running.json`, and stop.

## Step 1 — Transcribe

`practice.cmd transcribe --key <key>` (~10–20 min). Writes in `work\practice\<key>\`:
- `transcript.txt` — `[h:mm:ss] text`, with the speech filter OFF, so it has both the band
  talking ("let's take it from the bridge", "one more time") and sung lyrics (sometimes
  garbled — match them to the real song).
- `audio_map.txt` — every 10 s: loudness + PLAYING / quiet. Playing stretches separated by
  quiet gaps are the takes.

## Step 2 — Identify the songs → plan.json

Read the whole transcript with the audio map. Practice is messy: false starts, a verse
played three times, stopping to talk, running a transition, coming back to a song later.

For each song, make **one entry covering all the time the band spent on it** — from the
count-off / first note of the first attempt to the end of the last attempt, including the
stops and talking in between (the team wants to see how the rehearsal went). If they
leave a song and come back to it later in the night, make a second entry with `"part": 2`.
Leave out long stretches that aren't a song (setup, line checks, chatting, prayer) unless
they're in the middle of working on one.

Use lyrics to name the song: real title and original artist. If a song can't be
identified, call it "Unknown Song" and describe it in `notes` (e.g. "fast, key of G,
'you are faithful' hook"). Instrumental-only runs (no vocals) — guess from context
("let's do X again") or label "Unknown Song (instrumental)".

`work\practice\<key>\plan.json`:
```json
{"songs": [
  {"title": "Way Maker", "artist": "Sinach", "start": 312.0, "end": 1105.5,
   "takes": 3, "notes": "stopped twice to fix the bridge"},
  {"title": "Way Maker", "artist": "Sinach", "part": 2, "start": 2400.0, "end": 2690.0,
   "takes": 1, "notes": "final run-through"}
]}
```
Times are seconds from the start of the recording; order = order played. `takes` = how many
times they started the song; `notes` = a few words a band member would find useful.

## Step 3 — Render

`practice.cmd render --key <key>` (add `--test` to write under `work\practice\<key>\TEST OUTPUT\`
instead). Makes low-quality 480p / 15 fps clips (small files, clear audio) in
`G:\Shared drives\Worship\Practice Worship Files\<date>\<date> - <n> - <Song>.mp4` and
updates `Practice Worship Files\Practice Log.csv`. Existing files are never overwritten;
two recordings from the same night share the date folder and keep the numbering going.

## Step 4 — Email, then mark done

One email to clhester1212@gmail.com — subject "Practice <date>: <n> songs organized" — with
the song table (n, song, artist, length, takes, notes), the source recording name, anything
uncertain, and the Google Drive link to the folder
(`cmd /c "pipeline\nlc.cmd drive-link --path \"G:\Shared drives\Worship\Practice Worship Files\<date>\""`;
if that times out, give the G:\ path). Then write `work\practice\<key>\done.json` and delete
`running.json`.

If anything fails, still email what failed and what got done, and write `done.json` with
`"status": "failed"` so the poll doesn't loop on it.
