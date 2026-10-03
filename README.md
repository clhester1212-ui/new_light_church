# New Light sermon-recap skill — upload & wiring guide

## What's in here
```
.claude/skills/new-light-sermon-recap/SKILL.md   <- the actual procedure (the skill)
routine-prompt.md                                 <- the short Routine prompt that invokes it
```

## 1. Upload to your GitHub repo
Copy the `.claude/skills/new-light-sermon-recap/` folder into the **root** of whatever
repo you want the business account's Claude Code environment to use (create a new repo
if you don't have one yet — it can be empty otherwise, even private). The `.claude/skills/`
path is what Claude Code scans for repo-local skills, so the folder must sit at that
exact path relative to the repo root, not nested inside another folder.

Commit and push it like any other file. `routine-prompt.md` and this README don't need
to be in the repo — they're just instructions for you.

## 2. Connect that repo to your business Claude account
On claude.ai (business account) → Settings → Connectors / GitHub, connect GitHub if not
already connected, and install the Claude GitHub App on that repo if prompted
(https://claude.ai/connect-github). Then start a Claude Code on the web session and pick
that repo as the session's source — a session only sees the skill once the repo is
attached when it starts.

Once attached, `new-light-sermon-recap` should show up in that session's available-skills
listing.

## 3. One-time environment setup (same as before)
- Connect Gmail and OpusClip at claude.ai/customize/connectors (business account).
- In the environment's settings (cloud environment menu → Edit → environment variables),
  add: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` — same values
  as the current live Routine (not repeated here on purpose — pull them from the existing
  Routine or wherever you have them stored).
- Optionally add `RECIPIENT_EMAIL` as an env var if status emails should go somewhere
  other than clhester1212@gmail.com — the skill checks for it and falls back to that
  address if it's unset.

## 4. Create the Routine
Paste `routine-prompt.md`'s content into that same Claude Code session and ask it to
create the Routine with the settings listed there.

## Why split it this way
The old version had the entire 7-step procedure typed directly into the Routine's
stored prompt. Keeping the procedure in git instead means:
- you can read/diff/review changes to the logic like any other code,
- the same skill can back multiple Routines (e.g. a manual "run it now" trigger) without
  duplicating the text,
- updating the process is a normal commit + push, not re-typing a wall of text into a
  Routine's prompt field.

The Routine itself stays tiny — it only carries the schedule and "go run this skill."
