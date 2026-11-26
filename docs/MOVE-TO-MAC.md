# Moving New Light's booth computer from the PC to a Mac Mini

Written 2026-11-25. The backup lives in Google Drive, in **My Drive > "New Light - PC to Mac Move"**.
Keep the PC running every Sunday until the Mac has done one full Sunday on its own.

---

## 1. What's in the Google Drive folder

| Folder | What it holds | Size |
|---|---|---|
| **01 Code (offline copy)** | `new_light_church.bundle`, a full copy of the GitHub repo with every branch. Only needed if GitHub is ever unavailable. | small |
| **02 Claude notes and scheduled tasks** | `memory/` (everything Claude has learned about New Light: names, cameras, ATEMs, preferences) and `scheduled-tasks/` (the noon Sunday routine and the 8 pm practice routine). | small |
| **03 Companion (Stream Deck)** | `db.sqlite`, Companion's whole setup: all 9 pages, every button, the ATEM / Mini / camera connections. | 1 MB |
| **04 ATEM and Mini backups** | `New Light Backups/` (every dated backup, including "2026-11-25 Ready for Sunday" with the ATEM's restorable `.xml`) and `ATEM Autosave/`. | 80 MB |
| **05 Cameras app data** | The New Light Cameras app's database, tuning, stage edges, camera-to-ATEM links and the AI model. *Not* the rehearsal test videos (see section 5). | ~5 MB |
| **06 Work folder** | All 29 GB of `work/`: every past Sunday's files, singer voice profiles, practice logs, Slack bot history, thumbnail ideas. | 29 GB |
| **07 Windows setup reference** | The PC's scheduled tasks (as XML), startup programs, installed software, Python packages and tool versions. Just for reference when setting up the Mac. | small |

**The code itself** is on GitHub: `https://github.com/clhester1212-ui/new_light_church`.
The newest work is on the **`ptz-tracker`** branch (15 commits ahead of `main`). Use that branch.

## 2. Passwords and keys (NOT in the Drive folder)

These four files hold sign-ins. Copy each one's contents into the church's password manager
(one entry each), then paste them into the same place on the Mac:

| File on the PC | What it's for |
|---|---|
| `C:\Users\NewLight\new_light_church\secrets\youtube.json` | YouTube upload sign-in |
| `C:\Users\NewLight\new_light_church\secrets\client_secret.json` | Google app ID used by the YouTube sign-in |
| `C:\Users\NewLight\new_light_church\secrets\pco.json` | Planning Center |
| `C:\Users\NewLight\new_light_church\secrets\slack.json` | Francis (the Slack bot) |

If the YouTube sign-in doesn't carry over, sign in again on the Mac. It takes a few minutes.

## 3. Setting up the Mac (in this order)

1. **Sign in to the Mac** with the booth account and turn on auto-login, so things start after a power cut.
2. **Install:** Google Drive for desktop (sign in with the same Google account), Claude desktop app, Bitfocus Companion,
   Blackmagic ATEM Switchers software (ATEM Software Control + ATEM Setup), Elgato Stream Deck, Python 3.12, ffmpeg (`brew install ffmpeg`),
   Git, Node (Companion brings its own), DaVinci Resolve, Dropbox (the sermon notes and props live in Dropbox).
   The full PC list is in `07 Windows setup reference/installed programs.txt`.
3. **Get the code:** `git clone https://github.com/clhester1212-ui/new_light_church` and `git checkout ptz-tracker`.
4. **Put the secrets back** in `new_light_church/secrets/` from the password manager (section 2).
5. **Copy the work folder** from Drive `06 Work folder/work` into `new_light_church/work`.
6. **Copy the Cameras app data** from Drive `05 Cameras app data/data` and `models` into `new_light_church/ptz-tracker/`.
7. **Companion:** install it, quit it, put `03 Companion/db.sqlite` into
   `~/Library/Application Support/companion/v5.0/`, then start it. Check the "ATEM" connection points at 192.168.11.112.
8. **Claude:** open the project in Claude, then ask Claude to restore its notes from
   `02 Claude notes and scheduled tasks/memory` and to re-create the two scheduled tasks from `scheduled-tasks/`.
9. **Then ask Claude to do the Mac changes in section 4.** Claude has everything it needs in the repo and these notes.

## 4. Things that are Windows-only and must change for the Mac

Claude should do these, one at a time, testing each:

- **Folder paths in `config.json`:** `C:\Users\NewLight\Dropbox\…` and `G:\Shared drives\…` become the Mac's
  Dropbox and Google Drive paths (for example `~/Library/CloudStorage/GoogleDrive-…/Shared drives/…`).
- **`.cmd` / `.bat` / `.ps1` / `.vbs` launchers** (in `pipeline/`, `slackbot/`, `ptz-tracker/`, `bridge-watch/`) need
  shell-script versions.
- **Windows scheduled tasks** become macOS launch agents:
  - "New Light - ATEM Worship name" (Sundays 6:00 am) and "New Light - ATEM Practice name" (Wednesdays noon) run `atem_name`
  - "New Light PTZ Tracker" (at login) runs the Cameras app's `keep_running.py`
  - Francis (the Slack bot) starts at login
  - "New Light Bridge Watch" is retired. Don't move it.
- **Finding the Crucial X6 drive:** the code looks for it by drive letter / Windows volume label. On a Mac it appears under `/Volumes/Crucial X6`.
- **The 4-week cleanup** sends files to the Windows Recycle Bin. On a Mac it should use the Trash.
- **Companion's Node path:** the code runs Companion's own Node at `C:\Program Files\Companion\…` and the ATEM library from
  `%APPDATA%\companion\modules`. On a Mac those are inside `/Applications/Companion.app` and `~/Library/Application Support/companion/modules`.
- **The Cameras app** uses `pythonw` and a hidden-window flag. Use plain `python3` on the Mac.
- **The skills' instructions** (`.claude/skills/*/SKILL.md`) mention Windows paths and commands. Update them as the code changes.

## 5. Kept on the PC on purpose

- **Cameras app rehearsal test videos:** deleted on 2026-11-25 (sent to the PC's Recycle Bin) because they showed
  children from the play rehearsal. They were never uploaded. The 13 tracking logs from those sessions (numbers only,
  no pictures) are in `05 Cameras app data/data/sessions`.
- **The four secret files** (section 2): they go through the password manager instead.

## 6. Not on this PC at all (nothing to move)

- The ATEM, the Mini, the cameras and the Stream Decks keep their own settings. The Mac only has to reach them on the network
  (ATEM 192.168.11.112, Mini .139, cameras .176/.177/.178, Stream Deck Network Dock .61).
- YouTube, Planning Center, Gloo, OpusClip, Slack and Google Drive content is all online.

## 7. Before you turn the PC off for good

- [ ] The Mac ran one full Sunday by itself: recording name set at 6 am, the Stream Deck worked during the service, and the noon routine made the video, MP3, clips and email.
- [ ] Wednesday practice files were made on the Mac.
- [ ] Francis answers in Slack from the Mac.
- [ ] Re-copy anything new in `work/` since this backup (Claude can do a final sync).
