# Stream Deck buttons (Companion), ready to add

Not added yet. Adding them needs the Companion admin page unlocked, which you do yourself.

**Setup (once):** in Companion, add a **Generic HTTP Requests** connection with the base URL `http://127.0.0.1:8890`. Each button below is one **POST** action with no body.

**Safety switch:** the URLs end in `?from=deck`. They only move cameras when **Stream Deck control** is ticked at the top of the dashboard, or when moves are unlocked there. Untick it to make the Stream Deck tracking buttons do nothing. Companion's own camera buttons still work either way.

Use the same buttons for the Right camera with `right` in place of `left`.

| Button text | POST URL |
|---|---|
| L TRACK ON | `/api/track/left/steer?from=deck` |
| L TRACK OFF | `/api/track/left/off` |
| L RE-LOCK | `/api/track/left/relock` |
| L TIGHT | `/api/track/left/frame?size=tight&from=deck` |
| L MED | `/api/track/left/frame?size=medium&from=deck` |
| L WIDE | `/api/track/left/frame?size=wide&from=deck` |
| L PULL OUT | `/api/track/left/pullout?from=deck` |
| L HOLD ZOOM | `/api/track/left/frame?size=hold&from=deck` |
| L PREACH + TRACK | `/api/track/left/preset/13?from=deck` |
| L STOP | `/api/cameras/left/stop` (always works) |

Stage camera presets (no tracking): `/api/cameras/stage/preset/<n>?from=deck`. The numbers are in `cameras.seed.json`, for example 0 = Piano and 1 = Drums / R Guitar. Which ones you want on the deck, and what to call them, is still your call.

**Button feedback (optional, later):** `GET /api/track` returns `"mode": "steer"` while a camera is tracking. A Companion trigger can turn the TRACK button red from that.
