---
name: new-light-sunday
description: Automation for New Light Church (Bladenboro, NC), triggered when a new Sunday recording shows up on the Crucial X6 drive. Takes that Sunday's ATEM "Worship" recording and produces the edited sermon video (1080p, lower thirds, date-specific bits removed) uploaded to YouTube, the sermon MP3 for the radio station, a Gloo Content Studio project (from the YouTube video), 480p reference videos of each worship song, OpusClip social clips, and 5 Instagram recap slides, then emails a summary. Use when asked to run, test, or debug "the Sunday routine," "the sermon pipeline," or the New Light media automation — including when a scheduled task invokes it.
---

# New Light — Sunday media pipeline

You are the media assistant for New Light Church (307 Chestnut St, Bladenboro NC 28320;
Sunday service 10am). This runs on the church's Windows laptop, from the repo folder
`C:\Users\NewLight\new_light_church`. A scheduled run has nobody watching — do not pause
to ask questions. If something can't be determined confidently, make the most reasonable
choice, flag it clearly in the summary email, and keep going.

**Test mode**: if the invoking prompt says TEST, pass `--test` to
`render` (songs + MP3 go to `work\<date>\TEST OUTPUT\` instead of Google Drive), upload to
YouTube as `private`, skip OpusClip unless the prompt says otherwise, and put "TEST" at the
start of every email subject.

All mechanical work goes through `pipeline\nlc.cmd <command> --date <YYYY-MM-DD>` (run it
with `cmd /c` from the repo root). `<date>` is the Sunday the recording is from — given by
`pending` (Step 0) or named in the prompt — not necessarily today. Settings (paths, speakers,
quality) are in `config.json`; working files for a Sunday land in `work\<date>\`.

**Where finished files go** (Google Drive shared drives, synced on this laptop as `G:\`):
- `G:\Shared drives\Worship\<date>\<date> - <n> - <Song>.mp4` + `Worship\Song Log.csv`
- `G:\Shared drives\Sermons\<year>\<date> - <file_title>\` — the edited 1080p video and the
  radio MP3, same base name
- `G:\Shared drives\Media\Instagram Slides\<date>\` and `Media\Editing Projects\` (Resolve timeline)
- Raw recordings are NOT copied — they stay on the X6 drive. The pastor's notes are still read
  from Dropbox. Nothing is written to Dropbox any more.

## Step 0 — Is there anything new? (the routine checks once a day at noon)

Most runs find nothing, so this must be quick and silent.
- Unless the prompt names a date, run `pipeline\nlc.cmd pending --claim`. It looks on every
  plugged-in "Crucial X6" drive for a Worship recording from a **Sunday**, on/after config
  `process_recordings_since`, at least `min_recording_gb`, with no `work\<date>\done.json` and
  not already being worked on (`--claim` writes `work\<date>\running.json` so the next poll
  skips it; a claim older than 8 hours is treated as dead).
- If it prints `NONE`: **stop immediately — no email, no other steps.**
- Otherwise it prints `{"date": ..., "source": ...}` — process that date. One Sunday per run;
  if there are more, the next poll picks them up.

- Gmail and OpusClip connectors must be available. If Gmail is missing, stop (you can't
  report anything). If OpusClip is missing, continue and note it in the summary. Claude in
  Chrome is needed for Gloo (Step 7c); if it isn't connected, skip that step and say so.
- `secrets\youtube.json` must have real values (not "PASTE ..."). If not, do everything
  except the upload and say so in the summary.
- If `work\<date>\done.json` exists, this Sunday already ran — send nothing and stop.
- `G:\Shared drives\` must exist (Google Drive for Desktop running and signed in). If not,
  email that Google Drive isn't available on the laptop, delete `work\<date>\running.json`
  so a later poll retries, and stop.

## Step 1 — Find the recording

`pipeline\nlc.cmd find --date <date>` — looks on any attached drive labeled "Crucial X6"
(there are two with the same name; either may be plugged in) for a `Worship…` file dated
that Sunday — by the date in the name, else the file's modified date (the ATEM now names files
like `Worship 185.mp4`); ignores `-conflict` copies — then waits until the file stops changing. If it isn't found, email the
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
  "live": {"guest": "Special Guest Worship Leader Russ Lee",
           "note": "Worship this morning was led by special guest worship leader Russ Lee."},
  "slides": [{"kicker": "Growth Rings", "text": "..."}],
  "instagram_caption": "...",
  "notes_for_email": ["anything uncertain"]
}
```

**Speaker** — default "Pastor Dale Hester". If the message is clearly given by Cory Hester
(introduced by name, or he identifies himself), use "Pastor Cory Hester". Anyone else:
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
(series name, "Point 1", etc.). For the designed carousel (Step 7): on slides 2–4 write the point as
its own first sentence, then one short supporting sentence (the first sentence becomes the big
headline); wrap the one key word or phrase of each slide in `*asterisks*` to show it in the series
accent color (e.g. "Life is more than what you *own.* What you own doesn't…"). The plain slides
ignore the asterisks.

**Instagram caption** (`instagram_caption`, approved Nov 2, 2026) — ready to copy and paste:
a one-line hook from the message, a line naming the speaker, series and message title, the
big idea in the pastor's words, the points (with their scripture references), one key verse
quoted exactly (name the translation shown on the slides/screen, e.g. NLT), a short
"swipe / save / share" line, the church invite (📍 + config `church_invite`), and 5–7 hashtags
(#NewLightChurch #Bladenboro + series/topic tags). A few emoji are fine. No dates or "this
Sunday", and no claims about links or the bio.

## Step 4 — Render

`pipeline\nlc.cmd render --date <date>` (add `--test` in test mode). This makes:
- `G:\Shared drives\Worship\<date>\<date> - <n> - <Song>.mp4` (480p reference copies) and
  updates `Worship\Song Log.csv`.
- `work\<date>\sermon.mp4` — the edited sermon, 1080p, with lower thirds — also copied to
  `G:\Shared drives\Sermons\<year>\<date> - <file_title>\<date> - <file_title>.mp4`.
- `...\Sermons\<year>\<date> - <file_title>\<date> - <file_title>.mp3` — the sermon audio for radio.
  A copy also goes to `G:\Shared drives\Media\Sermon MP3s\` (every sermon MP3 in one folder).
- `<date> - Resolve timeline.fcpxml` in `work\<date>\` and `G:\Shared drives\Media\Editing Projects\`
  — the same edit as a DaVinci Resolve timeline (File → Import → Timeline) for hand
  fine-tuning; it points at the raw file on the X6 drive. Mention its path in the email.

Then spot-check: extract a frame at the first lower third
(`ffmpeg -ss 6 -i work\<date>\sermon.mp4 -frames:v 1 work\<date>\check_lt.png`) and look at
it, and re-read the transcript around every cut once more.

## Step 4b — YouTube thumbnail (every week)

Style (approved Oct 26, 2026, "Combined A"): the pastor **cut out** of a real,
sharpened camera frame, waist-up on the left (no glow or halo around him — he asked, Nov 16, 2026), in front of the **series
artwork** (crisp, dimmed); the series logo as a badge top-right; the big hook caption below it.
Example: `work\2026-10-25\thumbnail.jpg`.
1. `pipeline\nlc.cmd thumb-frames --date <date>` → contact sheets
   `work\<date>\thumbs\sheet_*.jpg` (a frame every ~55 s, labeled with its second in the
   edited sermon). Most of the sermon is a wide shot — look for the camera **close-ups** /
   medium shots where he's big in frame. Avoid ~0–12 s and the mid-sermon lower third.
2. `pipeline\nlc.cmd thumb-frames --date <date> --around <sec>` → 12 frames 0.5 s apart; pick
   the moment where he looks good: facing the camera, eyes open, natural/confident expression.
   **Never** mid-blink, mid-word grimace, mouth wide open, eyes half shut, turned away.
3. **Sharpest frame**: `pipeline\nlc.cmd thumb-frames --date <date> --around <sec> --raw --face <x>,<y>`
   (face position as fractions of the frame) → `thumbs\raw_<sec>.jpg`: every frame of the RAW
   camera file (X6 drive) within ±0.6 s, face close-ups labeled with their second and a
   sharpness score; the 5 sharpest have green borders. Pick the sharpest one **with a good
   expression** — the sharpest often catches a blink, so always look. Use its second as `at`.
4. Add to plan.json:
   ```json
   "thumbnail": {"at": 580.74, "pastor_x": 0.105, "pastor_y": 0.36, "pastor_side": "left",
                 "pastor_at": 0.2, "caption": ["WINTER DOESN'T", "MEAN YOU'RE"], "highlight": ["DEAD"]}
   ```
   `at` = second in the edited sermon; `pastor_x`/`pastor_y` = his face in the full frame
   (fractions); `pastor_side` = which side he goes on (caption + badge go on the other — his
   image is never mirrored); `pastor_at` = how far across the thumbnail his face sits.
   Optional: `body_box` [l,t,r,b] (fractions of the frame) if the default waist-up cut-out box
   misses an arm or grabs something else; `fade_from` (0–1, default 0.72) = where down the
   cut-out he fades out — raise/lower so the pulpit/Bible never shows; `series` to override
   the series name (default: from `lower_third_subtitle`).
   **Caption** — separate from the YouTube title (don't repeat it): 2–5 words total, ALL CAPS,
   a question or punchy key line from what was actually said that makes people want to
   click (e.g. "WINTER DOESN'T MEAN YOU'RE DEAD", "WHAT DO YOU NEED TO CUT?"). `caption`
   lines are white; `highlight` lines (the payoff word) are bigger, in the series accent color.
5. `pipeline\nlc.cmd thumbnail --date <date>` → `work\<date>\thumbnail.jpg` (1280×720), also
   copied into the Sermons folder. It takes the frame straight from the raw recording (falls
   back to sermon.mp4 if the X6 drive is gone), sharpens it (denoise, 2× upscale, unsharp
   mask, a touch of contrast — **no AI face restore; never retouch, reshape or redraw him**),
   and cuts him out with rembg. **Look at it**: clean cut-out edges (no stage/pulpit chunks,
   no missing hands/head), face sharp and natural, caption big and readable at phone size.
   Adjust and re-run until it's right. `upload` (Step 5) then sets it on the video.
6. **Series art** comes from config.json `series_art` → `{"<Series>": {"path", "badge_crop",
   "accent"}}`. For a new series, look for its artwork (e.g. `Dropbox\Sound Booth\Props`,
   `Sermon Slides`, `Welcome`) and add an entry (badge_crop = the title area as fractions;
   accent = a bright color from the art). If none is found, the thumbnail falls back to a
   blurred stage background with a text badge — flag in the email that series art is needed.

## Step 4c — Who sang lead (Cory confirms before anything is written)

After `render` has put the songs in `G:\Shared drives\Worship\<date>\`:
`cmd /c "C:\Users\NewLight\new_light_church\pipeline\voices.cmd sunday --date <date>"` (~3–5 min per song:
it separates the vocals and matches voice profiles). It reads the soloist names Cory typed in each song's
**description** in Planning Center (blank = no solo), checks them against the voices — only singers
scheduled that Sunday are considered — and for songs with two soloists works out who sang which part
(with the first words of each part). It writes `work\<date>\vocals_pending.json` and
`work\<date>\vocals_slack.md`. Send that list to Cory:
`cmd /c "C:\Users\NewLight\new_light_church\pipeline\slack_dm.cmd --file work\<date>\vocals_slack.md"`.
**Do not write Vocals notes to Planning Center yourself** — that happens only after Cory replies
(the `new-light-vocals` skill handles his reply). In the summary email, just say the singer list was
sent for confirmation. If this step errors, note it in the email and carry on.

## Step 5 — YouTube

`pipeline\nlc.cmd upload --date <date>` (uploads `unlisted` by default; `--privacy private`
in test mode). Writes `work\<date>\youtube_result.json` with the URL. Note: YouTube forces
API uploads from an unaudited Google Cloud project to **private** — if that happens, say in
the email that the video needs one click in YouTube Studio to set it Unlisted/Public.
If `secrets\youtube.json` is missing or sign-in fails, skip this step and say so.
The result's `"thumbnail"` says whether the custom thumbnail was set; if it says `failed`,
put the reason in the email (the channel may need phone verification for custom thumbnails).
The result's `"playlist"` says whether it was added to the series playlist (the one named
"<Series> | Sermon Series | New Light Church"); if it says `no playlist` or `failed`, put
that in the email so someone adds it by hand.
To redo only the thumbnail later: re-run `thumbnail`, then `upload --date <date> --thumbnail-only`.

## Step 5b — YouTube livestream (every week)

The 10am stream also lands on YouTube as a live video titled "Online Worship Service" with no
description. `pipeline\nlc.cmd live --date <date>` finds the stream that started that day and
sets its title to "<YouTube title> | New Light Church", sets the description to the sermon summary
plus the standard channel welcome and links, sets the tags, and adds it to the series playlist (the
same playlist as the edited sermon, which holds both videos for each week). Writes
`work\<date>\youtube_live_result.json`.
- **Special guests** (guest worship leader, guest speaker, baptisms, etc.): add the optional
  `"live"` block to plan.json. `guest` replaces "New Light Church" at the end of the title when it
  fits in 100 characters, and `note` becomes the first line of the description. Check
  Planning Center and the transcript for guests (e.g. a guest leading every song).
- Skip in test mode (the stream is public). If it says
  no livestream was found, say so in the email. Put the title and the playlist result in the email.

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
copied to `G:\Shared drives\Media\Instagram Slides\<date>\`. Look at them; fix text in plan.json
and re-run if anything overflows or reads poorly. Then get Google Drive links for the email:
`pipeline\nlc.cmd drive-link --path "G:\Shared drives\Media\Instagram Slides\<date>"` (waits up
to 10 min for Drive to sync the folder), and the same for the Sermons folder and
`G:\Shared drives\Worship\<date>`. If `drive-link` times out, use the Google Drive connector to
find the folder and link it; failing that, give the G:\ path and flag it. (These links work
for members of the shared drive — nothing is made public.)

**Designed carousel (every week, approved Nov 2, 2026)** — after Step 4b (it reuses the
thumbnail's cut-out of the pastor): `pipeline\nlc.cmd carousel --date <date>` (add `--test` in
test mode) → `work\<date>\carousel_1.jpg … carousel_6.jpg` + `instagram_caption.txt`, copied
into the same Instagram Slides folder as `<date> - Carousel <n>.jpg` and
`<date> - Instagram caption.txt` (the plain slides stay too, so they can be edited by hand).
Slide 1 = cover: the pastor in front of the series art, the series logo, the message title,
speaker · series · week. **No glow or halo around the pastor** (he asked, Nov 16, 2026 — it made
him look "angelic"); the code uses only a soft dark shadow. Don't add glow, rim light or a
bright outline back. Slides 2–6 = the 5 plan slides, on one panorama of the series
background that flows across the swipe, inside a thin gold double frame, the `*key word*` in
the accent color, the series logo at the bottom. **Look at all six**: nothing cut off, the
pastor's cut-out clean, the title balanced, the highlight on the right word. Fix plan.json and re-run.
Series theming comes from config `series_art`: `carousel_bg` = 1–2 plain backgrounds from the
series art without the title (first = the cover, last = the panorama; falls back to the main
art), optional `carousel_cover_focus` (0–1, which part of the cover background stays in view)
and `carousel_pastor_at` (0–1, where his face sits across the cover). For a new series, look
for those backgrounds next to the series art (e.g. `Dropbox\Sound Booth\Props\<Series>BG*.png`).

**Send the carousel to Slack** (same Francis DM as the report): write a short
`work\<date>\instagram_slack.md` ("*Instagram carousel for Sunday, <Mon d>: <Title>*", 6 slides
in swipe order, caption comes next as its own message so it can be long-pressed and copied),
then `cmd /c "pipeline\slack_dm.cmd --file work\<date>\instagram_slack.md --attach work\<date>\carousel_1.jpg … work\<date>\carousel_6.jpg"`,
then `cmd /c "pipeline\slack_dm.cmd --file work\<date>\instagram_caption.txt"`. If the upload fails with
`missing_scope`, the Francis app still needs Slack's **files:write** permission: send the
Instagram Slides Drive link in its place (then the caption), and say so in the email.

## Step 7b — Subsplash (the live-broadcast media item)

Each Sunday's 10am livestream lands in Subsplash as a media item (usually titled "Online
Worship Service", video file `Live_<date>_10am.mp4`, "Broadcasted on Sun, <date>"). Give it
the same message info as YouTube, in the laptop's Chrome with the Claude in Chrome tools
(`mcp__claude-in-chrome__*` — Chrome is the browser signed in to Subsplash; the built-in browser
pane's session has expired, so don't use it). Work in a new tab and close it when done.

1. Open `https://dashboard.subsplash.com/-d/#/library/media/items`. If it shows the login page,
   **don't try to log in** — skip this step and put "Subsplash needs you to log in again in
   Chrome, then say 'redo Subsplash for <date>'" in the email.
2. Find the item dated `<date>` (list rows read "<Title> / <Series> • Oct 11, 2026"). It's the
   one whose video says "Broadcasted on Sun, <date>". If there's none (stream didn't run or
   isn't archived yet), skip and say so in the email. Never edit podcast (NYTCP) items.
3. On its page, set (the page is an Ember app — click into a field, select-all, type; check the
   values with `javascript_tool` since screenshots lag):
   - **Title**: the message title only, e.g. "The Cutting Season" (not the full YouTube title).
   - **Description** (the `.ql-editor` box): replace with the YouTube description, exactly.
     Easiest: `javascript_tool` → `document.querySelector('.ql-container').__quill.setText(<text>, 'user')`
     (the `'user'` source makes the app register the change).
   - **Speakers**: type the name and pick the *existing* entry — "Lead Pastor Dale Hester" or
     "Creative Pastor Cory Hester" (always "Cory", never "Corey"); others as listed in Subsplash.
     Remove any wrong speaker that's already there.
   - **Media Series** (Subsplash's name for the sermon series, right-hand column): choose the
     series from the notes, e.g. "Growth Rings". The dropdown only lists the 10 newest series;
     for an older one pick "See more" → a side panel opens → type the name in its search box and
     press Enter → click the series (it gets a check) → click **Add** at the top of the panel
     (closing the panel with ✕ cancels). Then confirm the dropdown shows the series and Save.
     If the series doesn't exist in Subsplash yet, don't create one — leave it and flag it.
   - Speaker picker: type the full name, wait ~2 s for the list, click the existing entry (just
     typing leaves loose text that isn't saved as a speaker); check it shows as a chip.
   - Leave date, artwork, visibility and the video alone.
4. Click **Save** (top right), then reload the item and confirm title, description, speaker and
   series stuck. Record the item URL in `done.json` and the email.

## Step 7c — Gloo Content Studio (the edited sermon video, via YouTube)

Skip in test mode. Run after Step 5 (needs the YouTube URL). Done in the laptop's Chrome with
the Claude in Chrome tools (Chrome must already be signed in to Gloo — never sign in or type a
password; if it shows the Gloo sign-in page, skip this step and say in the email that Chrome
needs signing in to Gloo).
1. Source = the **unlisted YouTube video** from `work\<date>\youtube_result.json` (the edited
   sermon, same as OpusClip gets). Don't use the radio MP3 — Gloo failed to import the MP3's
   Drive link ("Upload Failed", Oct 25, 2026), while the unlisted YouTube link imported fine.
2. Open `https://contentstudio.gloo.us/home/new-light-church/workspaces` in a new tab. If a
   project for this sermon already exists and isn't "Upload Failed" (same message title, or
   dated this Sunday), skip — someone already uploaded it by hand.
3. **Add Project** → paste the YouTube URL in "Paste a link from YouTube, Vimeo, Google Drive,
   or Dropbox" (don't use "Upload video/audio file" — the browser tool caps files at 10 MB)
   → **Continue**. If Gloo can't read the YouTube video yet (still processing on YouTube's
   side), wait a few minutes and retry once; if it still fails, use the sermon **MP4's** Drive
   link instead: `pipeline\nlc.cmd drive-link --path "G:\Shared drives\Sermons Videos\<year>\<date> - <file_title>\<date> - <file_title>.mp4"`.
   On "Set Sermon Start & End" keep the full range (the video is already just the sermon) →
   **Continue**. On "Confirm sermon details": Sermon Title = the YouTube title, Sermon
   Date = this Sunday, Speaker = pick the matching name from the list (e.g. "Cory Hester";
   if the speaker isn't listed, leave it and flag it — don't add speakers; type the first name
   in the dropdown's search box before clicking, since the list's top row is easy to mis-click —
   then check the field shows the right name). Sermon Series = the series (e.g. "Growth Rings").
   Scripture References = the 2–3 main passages, Enter after each. Fill any other
   fields from plan.json only if they're obvious; then **Create Project**.
4. Wait for it to process (check every minute or so, up to ~10 min). If it shows "Upload Failed"
   or can't access the file, note it in the email; do NOT change any file's sharing (the Drive
   fallback MP4 is in a members-only shared drive — the user decides). Close the tab when done.

## Step 8 — Summary email, then mark done

One email to `recipient_email`, with everyone in `summary_also_to` also on the To line
(only this summary — not the "no recording found" or error emails) — subject "Sunday <date>: sermon, radio MP3 & songs ready"
— with: message title/speaker; the YouTube link (+ privacy); the Subsplash item (updated or
why not, series set); the thumbnail caption used
(and whether it was set); OpusClip project link; the Gloo Content Studio result (created,
skipped as already there, or what went wrong); the
MP3 path; the song list (n, title, artist, length); the sermon range used and every cut
made (with a few words each, so it's easy to spot-check); anything flagged; and the
Google Drive links to the Instagram slides (the designed carousel + caption are in the same
folder; say whether they went out on Slack), the Sermons folder (video + MP3) and the Worship
songs folder. (Don't attach the slides — the link opens them on a phone, ready to post.)

**Slack copy**: send the same report to Cory Hester in Slack (via the Francis bot). Write it
as Slack mrkdwn to `work\<date>\slack_report.md` — same content as the email, formatted for
Slack: `*bold*` headings, `• ` bullets, links as `<url|text>`, no HTML or tables (songs and
cuts as bullet lines) — then run
`cmd /c "C:\Users\NewLight\new_light_church\pipeline\slack_dm.cmd --file work\<date>\slack_report.md"`.
If it errors (Francis not set up, token revoked), mention that in the email instead and move
on — Slack never blocks the run.

Then write `work\<date>\done.json` with the results and delete `work\<date>\running.json`.

**4-week cleanup (every run, after done.json; standing request from Nov 16, 2026)**: run
`cmd /c "C:\Users\NewLight\new_light_church\pipeline\nlc.cmd cleanup"`. It keeps the most recent
Sunday and the 4 weeks before it (e.g. Sunday Nov 15 → keeps Oct 18 and later) and sends older
items to the Recycle Bin / Drive Trash: the laptop's `work\<date>\` files (keeping the small
.json records — done.json must stay so old Sundays aren't re-processed), `Media\Instagram
Slides\<date>` and `Media\Editing Projects\<date> - Resolve timeline`. It never touches Sermon
MP3s, sermon videos, Worship song videos or Practice files — keep it that way. Add one line to
the email/Slack report ("Cleanup: removed items dated before <cutoff>, freed X GB"); a cleanup
failure never blocks the run. Skip in test mode.

If any step fails in a way not covered above, still send the email explaining plainly what
failed and what did get done, then still write `done.json` (with `"status": "failed"`) so the
daily check doesn't redo it — a re-run is then a manual request ("run the Sunday routine
for <date>"; delete that done.json first).
