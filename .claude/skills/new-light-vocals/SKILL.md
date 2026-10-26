---
name: new-light-vocals
description: Handles Cory's Slack reply confirming who sang lead on each worship song ("all good", "2 = Shelley", "3 = Josh, verse 2 Austin", "4 = none"). Applies it to the pending list in work/<date>/vocals_pending.json, then writes Vocals notes in Planning Center and teaches the voice profiles. Use whenever a message is about confirming or correcting singers/soloists/who sang which song or verse.
---

# Confirm who sang lead (reply from Cory)

Each Sunday the media routine runs `pipeline\voices.cmd sunday --date <date>`, which writes
`work\<date>\vocals_pending.json` and has Francis DM Cory a numbered list:
"1. *Praise The Lord*: Hayley ✅ …". **Nothing goes into Planning Center until Cory confirms.**
His reply comes to you (often through the Francis Slack bot).

1. Find the Sunday: the newest `work\<date>\vocals_pending.json` with `"confirmed": false`
   (or the date Cory names). Read it — `songs[n-1]` is item *n* in the list.
2. Apply his reply to that file:
   - "all good" / "yes" / "correct" → leave every song as is.
   - "2 = Shelley" → song 2: `lead` = the singer's full name (first names → `config.json` "singers";
     "Allison" = Alison Carroll, "Haley" = Hayley Batten). Remove any `note`.
   - "3 = Josh, verse 2 Austin" / "3 = verse 1 Josh, verse 2 Austin" → song 3: `lead` = "Josh Bryant / Austin Stone",
     `note` = "Verse 1: Josh · Verse 2: Austin" (his wording, first names, "·" between parts).
   - "4 = none" / "no solo" → song 4: `lead` = "" (left blank in Planning Center).
   - Anything unclear → don't guess; ask him in your reply which song/singer he means, and stop.
3. Run `cmd /c "C:\Users\NewLight\new_light_church\pipeline\voices.cmd apply --date <date>"`. It writes a
   Planning Center **Vocals** note on each song that has a lead ("[NLC media] Lead: Hayley" or the verse note),
   fills the **Lead** column in `G:\Shared drives\Worship\Song Log.csv` (full name, the verse note, or
   "None" for no soloist; blank = not confirmed yet), and adds each confirmed one-singer song to that
   singer's voice profile so next week is more accurate.
   Re-running is safe (it updates its own notes, never duplicates, never touches hand-typed notes).
4. Reply briefly: what went into Planning Center (song → singer) and who the voice tool learned from.

Planning Center: the soloist names Cory types in each song's **description** (the line under the song
title in Services) are the plan; the voice check confirms or flags them. Blank description = no solo.
