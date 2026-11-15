# New Light PTZ Tracker

Camera list, live status and test controls for the New Light Church PTZ cameras. Plan: [PLAN.md](PLAN.md). Steps 1 to 3 are built. The Stream Deck buttons are ready to add, and the first test with a real person is next ([TESTING.md](TESTING.md)).

## Start it

Double-click **`start-ptz-tracker.bat`** in this folder, or run:

```
python ptz-tracker/run.py
```

Then open **http://localhost:8890** on the Stream Deck PC.

## What you'll see

The three cameras sit across the top, in order: **Left**, **Stage (back)**, **Right**. Each shows its picture, or the tracker's view while tracking, and a badge reading **TRACKING**, **Watching** or **Not tracking**. Under each camera is a grid of large buttons for it, numbered **L1–L11**, **S1–S10** and **R1–R11**, so they can be matched to Stream Deck buttons in Companion later. The button for what a camera is doing right now lights up green. **STOP ALL** in the top bar stops every camera.

The small controls are in **Setup and fine control** further down: nudge, zoom, presets without tracking, stage edges, test video and measuring the video delay.

- Setup has one card per camera: online dot, model, IP, serial, a still picture that refreshes every few seconds, and the camera's pan, tilt and zoom.
- **Moves are locked** when the app starts. Click **Unlock moves (10 min)** to use the preset, nudge and zoom buttons. They lock again by themselves after 10 minutes. **STOP** always works.
- **Recent activity** lists every move, unlock, and camera dropping offline or coming back.

Only use the move buttons when the cameras aren't live (not during Wednesday practice or a Sunday service).

## Auto-tracking (step 3: steering)

On the Left and Right cards:

| Button | Does | Moves camera? |
|---|---|---|
| **Track ON** | Follows the person nearest center and keeps them framed | Yes (unlock moves first) |
| **Watch only** | Shows who it would follow and what it would do | No |
| **Re-lock center** | Switches to whoever is center now | Only if Track ON |
| **Tracking off** | Stops tracking; the camera holds where it is | No |
| **Tight / Medium / Wide** | Zooms slowly to head-and-shoulders, waist-up, or full body, still following | Yes |
| **Slow pull-out** | Eases out to wide at the slowest zoom speed, keeping them centered | Yes |
| **Hold zoom** | Keeps following but stops changing the zoom | Yes |
| **Go + track** | Goes to the chosen preset, waits about 4 seconds, then follows whoever is center there | Yes |

### Stay on stage (stage edges)

Each tracking camera can be given four edges: left, right, bottom and top. While tracking, it never pans or tilts past them, so it won't follow someone off the stage, into the seats or down the aisle. It can always move back toward the stage. The edges are in the camera database, so they're kept after a restart.

To set them: unlock moves, aim the camera at the left edge of the stage with the nudge buttons, and press **◀ Left edge here**. Do the same for the right edge, the bottom edge (the lowest the camera should ever point, usually the stage floor at the front) and the top edge. **Clear edges** removes all four. Setting an edge never moves the camera. Until edges are set, the card says "No edges set yet", and tracking can follow anyone, anywhere.

When it stops at an edge, the status line says "stopped at stage edge (left)", for example. Zoom still works. If the person keeps going, they leave the shot and the camera holds still.

**STOP** on the card, or any manual move (nudge, zoom, preset), immediately ends steering, and the tracker goes back to watch-only.

How it behaves:
- **Choosing someone:** before locking on, it has to see the same person in 5 of the last 8 frames (about 1 second) with confidence of at least 0.6. It also skips anything much shorter than the tallest person in the shot (stage lights, mic stands), and any figure right beside someone whose feet are lower (a shadow on the screen).
- **Losing them:** if it misses them for a moment, it keeps easing along for up to half a second. If it loses them for 2.5 seconds, the camera holds still and it looks for the center person again.
- **Dead zone:** small sways in the middle don't move the camera. It starts moving past 10% off center and stops once within 4%. Speeds depend on zoom, so a tight shot moves gently, and speed changes are eased so starts and stops aren't jerky.
- **Smoothness:** each move lasts at least 1 second, and after stopping it rests 1.2 seconds before moving again, unless the person is near the edge of the shot. The still zone in the middle grows when zoomed in, and it doesn't tilt during a zoom. All of these can be changed under **Tracking settings → Smoothness**. They were added after the first live test, where Tight looked jerky because the camera started and stopped about once a second.
- **Real camera speeds:** the SRG-300SE pans at about 0.47 times the textbook Sony speed curve (measured: speed 1 is about 0.5°/s, and 51 position units = 1°). This is `SPEED_SCALE` in `tracker.py`. Before it was measured, the tracker thought it had caught up too soon, which caused most of the jerkiness.
- **Losing someone in Tight or Medium:** it eases the zoom out a little for up to 8 seconds to find them again, and remembers their height for 20 seconds, so it goes straight back to the framing.
- **Zoom limit:** Tight never goes past about 25x (800 short of the optical limit), so it never slips into blurry digital zoom.
- **Tilt:** gentler than pan. It uses the middle value of the head position over the last second, so raised hands and bows don't make the shot bob.
- **Video delay:** the network video is a little late. The tracker allows for moves it already made that the picture doesn't show yet, so it doesn't overshoot.
- **Framing:** it measures the person's height whenever their whole body is visible. If it hasn't yet, Tight/Medium/Wide first zooms out a little to measure.
- **Lost video:** if video stops for 1 second, the camera stops moving.

### Tuning, testing and the Stream Deck

- **Tracking settings** (bottom of the dashboard): sliders for every setting, with an explanation of each. They take effect right away and are saved in `data/tuning.json`. **Reset all** goes back to the starting values in `nlptz/tuning.py`.
- **Record test video**: saves the tracker's view (with boxes) as an MP4 in `data/sessions/`. A frame-by-frame log is always kept there too. Logs older than 14 days are deleted.
- **Measure video delay**: one small pan and back, timing how late the picture is. It updates the delay setting by itself. Needs the stage lit.
- **Stream Deck control** (top of the page): lets Companion buttons move cameras without the 10-minute unlock. See [COMPANION-BUTTONS.md](COMPANION-BUTTONS.md).
- First test with a real person: [TESTING.md](TESTING.md).

### Practice simulator (no camera or person needed)

`tools/simulate.py` treats a recorded wide shot (for example a practice recording on the Crucial drive) as the stage, and lets the tracker steer a pretend camera around it, with video delay and gentle speed-up included. It prints how often it moved, reversed and stayed centered, and can save a side-by-side video:

```
python tools/simulate.py "D:/Practice_11_11_2026 01.mp4" --start 2060 --dur 40 --zoom 3 --events "10:medium,25:pullout" --video sim.mp4
```

`--set catch_up_x=2.0` tries a setting for that run only. The pretend camera can only zoom to about 5x (it's cropping a recording), so its pictures get blurry at high zoom.

Results on 7 clips from the 2026-11-11 practice, compared with the first version: false lock-ons down by more than half (it had locked onto a floor light), tilt starts down from 4.5 to 1.7 a minute, no back-and-forth reversals, and about 40% fewer commands sent to the camera.

### The tracker's view

While tracking is running, the picture switches to the tracker's view:

- **Gray boxes:** everyone it sees, with how sure it is. Shadows on the screen sometimes show up here.
- **Green box (FOLLOWING):** the person it has locked onto. It picks the person nearest center, then sticks with them.
- **The bottom line:** what it *would* do, for example "pan left 1" or "hold still".

**Re-lock center** switches to whoever is center now. **Stop** ends tracking. Each tracked camera uses about 10% of this PC's processor.

The person detector is **YOLOX-nano** (Apache-2.0), from the YOLOX GitHub release 0.1.1rc0. It's saved at `models/yolox_nano.onnx` (3.7 MB, sha256 `c789161e…a0b7d`) and isn't kept in git. To set up another PC, download it again from https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx. It needs `numpy`, `onnxruntime` and `pillow` (already installed here).

## Where things live

| Thing | Where |
|---|---|
| Camera database (SQLite) | `ptz-tracker/data/ptz.sqlite` (created on first start, not saved in git) |
| Starting camera list | `cameras.seed.json`. Read only when the database is first created. To start over, delete `data/ptz.sqlite`. |
| Camera control (Sony VISCA over IP, UDP 52381) | `nlptz/visca.py` |
| Still pictures (RTSP through ffmpeg) | `nlptz/snapshots.py` |
| Web server and API | `nlptz/server.py` |
| Dashboard page | `nlptz/static/index.html` |
| Video frames for tracking (ffmpeg → 416×416) | `nlptz/video.py` |
| Person detector | `nlptz/detector.py` |
| Who to follow, and what it would do | `nlptz/tracker.py` |
| Live settings (starting values and explanations) | `nlptz/tuning.py` |
| Session logs and test videos | `nlptz/recorder.py` → `data/sessions/` |
| Video delay test | `nlptz/measure.py` |
| Practice simulator | `tools/simulate.py` |

## API (for Stream Deck / Companion buttons later)

All are `POST`, except the reads. Camera ids: `left`, `right`, `stage`.

| Call | Does |
|---|---|
| `GET /api/cameras` | Camera list with live status |
| `GET /api/cameras/<id>/snapshot.jpg` | Current picture |
| `POST /api/moves/unlock` / `lock` | Allow or block moves (unlock lasts 10 min) |
| `POST /api/cameras/<id>/preset/<n>` | Go to preset n (0 = Companion's P1) |
| `POST /api/cameras/<id>/nudge?dir=left&speed=4&ms=300` | Short pan or tilt move |
| `POST /api/cameras/<id>/zoom?dir=in&speed=2&ms=500` | Short zoom |
| `POST /api/cameras/<id>/position?pan=..&tilt=..&zoom=..&speed=8` | Go to an exact position (the numbers the dashboard shows) |
| `POST /api/cameras/<id>/home` | Home position |
| `POST /api/cameras/<id>/stop` | Stop everything (works even when locked) |
| `POST /api/track/<left\|right>/steer` | Track ON: follow and steer (moves unlocked) |
| `POST /api/track/<id>/frame?size=tight\|medium\|wide\|hold&speed=1` | Framing while tracking (zoom speed 0 = slowest to 7) |
| `POST /api/track/<id>/pullout` | Slow pull-out to wide |
| `POST /api/track/<id>/preset/<n>` | Go to preset n, then track |
| `POST /api/track/<left\|right>/watch` | Start watch-only tracking |
| `POST /api/track/<id>/relock` | Follow whoever is center now |
| `POST /api/track/<id>/off` | Stop tracking |
| `GET /api/track` | Tracking state for each camera (who it follows, what it would do) |
| `GET /api/track/<id>/view.jpg` | Tracker's view with boxes |
| `POST /api/track/<id>/record?on=1\|0` | Start or stop the test video |
| `POST /api/track/<id>/measure-delay` | Measure video delay (moves the camera a little, then back) |
| `POST /api/cameras/<id>/edge?set=left\|right\|low\|high` | Stage edge at where the camera points now (doesn't move it) |
| `POST /api/cameras/<id>/edge?clear=1` | Clear that camera's stage edges |
| `GET /api/tuning` / `POST /api/tuning?key=value` / `POST /api/tuning/reset` | Read, change or reset settings |
| `POST /api/deck?on=1\|0` | Stream Deck control on or off |

Calls that move a camera need moves unlocked on the dashboard, or **Stream Deck control** on and `?from=deck` at the end of the URL.

## Notes

- Sony cameras always reply to UDP port 52381 on this PC. Companion doesn't listen there, so this app does. If the dashboard says cameras aren't answering, check that nothing else has taken port 52381.
- The RTSP streams don't need a password.
- Needs Python 3.12 and ffmpeg, both already on the Stream Deck PC. No extra Python packages for step 1.
