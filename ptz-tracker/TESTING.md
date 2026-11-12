# Test day checklist (first real-person test)

You need: one person who can walk around on stage for about 20 minutes, the stage lights on, and the dashboard open at http://localhost:8890.

## 0. Before you start (2 min)
- Lights on. Make sure nothing is going out live.
- Dashboard: press **Unlock moves (10 min)**. Press it again whenever it runs out.

## 1. Measure the video delay (1 min, Left camera)
- Press **Measure video delay** on the Left card. The camera makes one small pan and goes back to where it was.
- A message shows the delay, for example "0.6 s". The setting updates by itself.
- If it says "couldn't see the picture move", the shot is too dark or empty. Point it at something lit and try again.

## 2. Watch only (3 min)
- Press **Watch only**, then **Record test video**.
- Have the person stand center, walk left, walk right, raise their hands, turn around, and walk behind the pulpit.
- Check: the green box stays on them, and no gray "shadow?" box steals it.

## 3. Track ON (10 min)
- Press **Track ON**. Keep a finger on **STOP**.
- The person should: walk slowly across the stage; stop and preach in place for a minute (the camera should stay still); walk fast for a few steps; raise hands, bow and kneel (the shot should NOT bob up and down); and walk behind someone or the pulpit.
- Try **Medium**, then **Tight**, then **Slow pull-out**, then **Go + track** to the Preach preset.
- Repeat on the Right camera.

## 4. Adjust (as needed)
Open **Tracking settings** at the bottom of the dashboard. Changes take effect right away.

| If you see this | Try this |
|---|---|
| Moves too often while they sway in place | Raise **Start moving when off center by** (0.10 → 0.14) |
| Trails behind when they walk | Lower **Catch-up time** (1.6 → 1.2) or raise **Lead walkers by** |
| Overshoots, then comes back | Raise **Catch-up time**, check **Video delay** |
| Bobs up and down | Raise tilt **Catch-up time** (3 → 4) or **Head smoothing** (1 → 1.5) |
| Doesn't tilt when they step down off the stage | Lower **Start tilting when head is off by** (0.12 → 0.08) |
| Starts and stops feel jerky | Lower **Speed change per frame** (3 → 2) |
| Locks onto a light or a shadow | Raise **Sureness to start following** or **Smallest vs. tallest person** |
| Tight is too tight or too loose | Change **Tight: zoom (body heights)** |

**Reset all to starting values** at the top of the settings undoes everything.

## 5. Afterwards
Press **Stop recording**. Tell Francis "review today's tracking test". The video and a frame-by-frame log are in `ptz-tracker/data/sessions/`.
