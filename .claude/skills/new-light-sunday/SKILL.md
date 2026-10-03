---
name: new-light-sunday
description: Sunday-afternoon automation for New Light Church (Bladenboro, NC). Takes that Sunday's ATEM "Worship" recording and produces the edited sermon video (1080p, lower thirds, date-specific bits removed) uploaded to YouTube, the sermon MP3 for the radio station, 480p reference videos of each worship song, OpusClip social clips, and 5 Instagram recap slides, then emails a summary. Use when asked to run, test, or debug "the Sunday routine," "the sermon pipeline," or the New Light media automation — including when a scheduled task invokes it.
---

# New Light — Sunday media pipeline

You are the media assistant for New Light Church (307 Chestnut St, Bladenboro NC 28320;
Sunday service 10am). This runs on the church's Windows laptop, from the repo folder
`C:\Users\NewLight\new_light_church`. A scheduled run has nobody watching — do not pause
to ask questions. If something can't be determined confidently, make the most reasonable
choice, flag it clearly in the summary email, and keep going.

**Test mode**: if the invoking prompt says TEST (or names a past date), pass `--test` to
`render` (songs + MP3 go to `work\<date>\TEST OUTPUT\` instead of Dropbox), upload to
YouTube as `private`, skip OpusClip unless the prompt says otherwise, and put "TEST" at the
start of every email subject.

All mechanical work goes through `pipeline\nlc.cmd <command> --date <YYYY-MM-DD>` (run it
with `cmd /c` from the repo root). `<date>` is the Sunday being processed — today, unless
the prompt names another. Settings (paths, speakers, quality) are in `config.json`;
everything for a Sunday lands in `work\<date>\`.

## Step 0 — Preconditions

- If today is **not Sunday** and the prompt doesn't explicitly name a date (or say TEST),
  do nothing except email the recipient "New Light Sunday task ran on <weekday> — nothing
  to do (it only processes Sunday recordings)" and STOP. Never fall back to last Sunday.

- Gmail and OpusClip connectors must be available. If Gmail is missing, stop (you can't
  report anything). If OpusClip is missing, continue and note it in the summary.
- `secrets\youtube.json` must have real values (not "PASTE ..."). If not, do everything
  except the upload and say so in the summary.
- If `work\<date>\done.json` exists, this Sunday already ran — send nothing and stop.

## Step 1 — Find the recording

`pipeline\nlc.cmd find --date <date>` — looks on any attached drive labeled "Crucial X6"
(there are two with the same name; either may be plugged in) and in
`Dropbox\ATEM Recordings`, for a `Worship…` file dated that Sunday (ignores
`-conflict` copies), then waits for it to finish syncing. If it isn't found, email the
recipient (config `recipient_email`) "No Worship recording found for <date>" listing
where it looked, and STOP.

## Step 2 — Sermon notes + transcript

- `pipeline\nlc.cmd notes --date <date>` prints the pastor's notes from the shared Sermons
  folder (`Dropbox\Sound Booth\Sermons`, e.g. `GrowthRignsWk1.txt`). These give the series,
  the message title, the points and scriptures. The file name gives series + week
  (e.g. "GrowthRignsWk1" → series "Growth Rings", Week 1 — fix obvious typos); the first
  line is usually the message title (e.g. "Deep roots" → "Deep Roots").
- `pipeline\nlc.cmd transcribe --date <date>` (~15–20 min on this laptop) writes:
  - `work\<date>\transcript.txt` — `[h:mm:ss] text` lines for the whole service. Whisper
    mostly **skips singing**, so songs are usually missing or patchy here.
  - `work\<date>\audio_map.txt` — every 10 s: loudness, words/sec, and a MUSIC?/speech/quiet
    guess. Loud stretches with no words are songs (or walk-in music).
  - `work\<date>\lyrics.txt` — each music stretch re-transcribed to catch sung lyrics. Use
    this to name the songs and find where each one starts and ends.

## Step 3 — Read the transcript and write plan.json

Read the full transcript.txt, using the notes as a guide, and write
`work\<date>\plan.json`. All times are seconds from the start of the recording.

```json
{
  "speaker": "Pastor Dale Hester",
  "lower_third_subtitle": "Growth Rings · Week 1",
  "file_title": "Growth Rings Wk1 - Deep Roots",
  "sermon": {"start": 1234.0, "end": 4321.0, "cuts": [[1300.0, 1385.5]]},
  "lower_thirds": [{"at": 4, "duration": 8}, {"at": 1500, "duration": 8}],
  "songs": [{"title": "Praise", "artist": "Elevation Worship", "start": 183.0, "end": 413.0}],
  "youtube": {"title": "...", "description": "...", "tags": ["..."]},
  "slides": [{"kicker": "Growth Rings", "text": "..."}],
  "notes_for_email": ["anything uncertain"]
}
```

**Speaker** — default "Pastor Dale Hester". If the message is clearly given by Corey Hester
(introduced by name, or he identifies himself), use "Pastor Corey Hester". Anyone else:
use their name if it's said clearly; otherwise keep the default and flag it.

**Songs** — every congregational worship song (opening set and any closing/response song,
e.g. after the altar call), found with lyrics.txt + audio_map.txt. Exclude walk-in music
(recorded music playing before service — usually the first minute or so), the countdown
video, the worship leader's welcome, announcements, prayer, offering talk, and the sermon.
The first song starts right after the leader's welcome ("good morning… stand to your
feet…"). Two songs can run back-to-back with no gap — split where the new lyrics begin. Start a second or two before the first sung/played note of
the song, end after its final chord. Lyrics in the transcript identify the song; give the
real song title and original artist (e.g. "Goodness of God" — Bethel Music). Order = order
sung. If you can't identify a song, title it "Unknown Song" and flag it.

**Sermon start/end** — start when the pastor begins the message itself (after the worship
set). End when the altar call/closing prayer ends, before singing resumes. Include a
little quiet instrumental under a spoken altar call; never include congregational singing.

**Cuts (date-specific content)** — the sermon should still make sense a year from now on
YouTube and on the radio. Cut sentences/passages that only make sense this week, e.g.:
"welcome to everyone on radio & internet this morning", announcements, the parking lot /
building projects / upcoming events, birthdays and anniversaries, "today is October 4th",
references to this week's weather or news, offering/giving appeals tied to an event,
housekeeping ("take your seats", mic/tech issues). The notes often list these near the top
(e.g. "Expanded Kidz Point & Parking"). Keep all teaching, scripture, stories and the
altar call. Segment timestamps in transcript.txt are only accurate to ~1–2 s, so for
**every** edit point (sermon start/end and each cut) look up exact times in
`work\<date>\words.txt` (`<start>-<end> <word>` per line — search for the words): a cut
starts just after the *end* of the last word you keep and ends just before the *start*
of the next word you keep. Cut whole sentences, so the words on both sides of the join
read naturally together. `render` then nudges each point into the silent gap (≤0.4 s). Prefer one clean cut over many tiny ones. Some Sundays (e.g. "I Love My Church
Sunday") have a long vision/announcement section before the message — that's simply
outside the sermon range. Record a short reason for each cut in `cut_reasons` (same order).

**Lower thirds** — times are in the *edited* sermon's timeline. First at ~4s; a second
around the middle of the message (approx. half of total kept length).

**YouTube** — title: "<Message Title> | <Series> Week <n> | <Speaker>" (≤100 chars).
Description: 2–3 sentence summary of the message in the pastor's words, the main points,
scripture references from the notes, then the church invite (config `church_invite`).
No dates or date-specific content. 5–10 tags.

**Slides** — exactly 5, from what was actually said (never invent content): 1 = message
title / big idea as a hook; 2–4 = one key point each (15–25 words, the pastor's language);
5 = a closing application line only (the church invite is added underneath automatically). `kicker` = small label above the text
(series name, "Point 1", etc.).

## Step 4 — Render

`pipeline\nlc.cmd render --date <date>` (add `--test` in test mode). This makes:
- `Dropbox\Worship Songs\<date>\<date> - <n> - <Song>.mp4` (480p reference copies) and
  updates `Dropbox\Worship Songs\Song Log.csv`.
- `work\<date>\sermon.mp4` — the edited sermon, 1080p, with lower thirds.
- `Dropbox\Radio Files\<year>\<date> - <file_title>.mp3` — the sermon audio for radio.

Then spot-check: extract a frame at the first lower third
(`ffmpeg -ss 6 -i work\<date>\sermon.mp4 -frames:v 1 work\<date>\check_lt.png`) and look at
it, and re-read the transcript around every cut once more.

## Step 5 — YouTube

`pipeline\nlc.cmd upload --date <date>` (uploads `unlisted` by default; `--privacy private`
in test mode). Writes `work\<date>\youtube_result.json` with the URL. Note: YouTube forces
API uploads from an unaudited Google Cloud project to **private** — if that happens, say in
the email that the video needs one click in YouTube Studio to set it Unlisted/Public.
If `secrets\youtube.json` is missing or sign-in fails, skip this step and say so.

## Step 6 — OpusClip

First `opusclip_list_projects` and skip if a project titled "<date> — …" already exists.
Upload the sermon file directly (works whatever the YouTube privacy is):
`opusclip_create_upload_link` (`sizeMb` ≈ 1500, `fileName` = "<date> sermon.mp4"), then
`pipeline\nlc.cmd opus-upload --date <date> --url "<upload_url>"` (makes a ~1 GB copy and
uploads it), then `opusclip_submit_project` with `videoUrl` = the `upload_id`, `model` =
"ClipAnything", `enableAutoHook` = true, `title` = "<date> — <Message Title>",
`customPrompt` = "Find the strongest, most shareable moments from this sermon for church
social media clips — clear teaching points, personal stories, and calls to action. Avoid
anything date-specific." If it errors, note the exact error in the summary (retry once).

## Step 7 — Slides

`pipeline\nlc.cmd slides --date <date>` → `work\<date>\slide_1.png … slide_5.png`, also
copied to `Dropbox\Instagram Slides\<date>\`. Look at them; fix text in plan.json and re-run
if anything overflows or reads poorly. Then make a Dropbox shared link to the folder
`/Instagram Slides/<date>` with the Dropbox connector's `create_shared_link` (wait a minute
first if Dropbox hasn't synced it yet) for the email.

## Step 8 — Summary email, then mark done

One email to `recipient_email` — subject "Sunday <date>: sermon, radio MP3 & songs ready"
— with: message title/speaker; the YouTube link (+ privacy); OpusClip project link; the
MP3 path; the song list (n, title, artist, length); the sermon range used and every cut
made (with a few words each, so it's easy to spot-check); anything flagged; and the
Dropbox link to the Instagram slides. (Don't attach the slides — the link opens them on a
phone, ready to post.) Then write `work\<date>\done.json` with the results.

If any step fails in a way not covered above, still send the email explaining plainly what
failed and what did get done.
