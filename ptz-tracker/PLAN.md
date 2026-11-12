# New Light PTZ Tracker — Plan (draft for approval)

A small app on the Stream Deck PC (NLCstreamdeck) that keeps a list of our cameras, lets the Left and Right cameras follow a person by themselves, and gives the Stream Deck buttons for smooth framing.

## What we have (checked 2026-11-11)

| Camera | IP | Model | Video over network | Tracking built in? |
|---|---|---|---|---|
| Stage (the "back" camera) | 192.168.11.176 | Sony SRG-X120 | RTSP 1080p | No — presets only (by choice) |
| Left | 192.168.11.177 | Sony SRG-300SE (serial 3306440) | RTSP 1080p30, no password | No — **we add it** |
| Right | 192.168.11.178 | Sony SRG-300SE (serial 3306570) | RTSP 1080p30, no password | No — **we add it** |
| "Visca03Left2" in Companion | 192.168.11.77 | — | — | Doesn't respond. Looks like an old leftover. |

All three cameras take VISCA-over-IP (UDP 52381). Video also goes to the ATEM Mini Extreme (.111) / Mini Pro (.139) as it does now. Nothing about the ATEM path changes.

The PC has a Ryzen 7 4700U (8 cores, no graphics card) and 16 GB of RAM, plus ffmpeg. That's enough for 2 tracked cameras if the app looks at a small, slower copy of the video (about 640×360 at about 10 checks per second). The camera itself still moves smoothly, because steering is updated many times a second.

## Research note

Church Front's tracking videos (OBSBOT Tail 2, PTZOptics Move SE/4K, Canon CR-N) all use cameras that have tracking **built in**. On those cameras, zoom is locked while tracking is on. Our build does better here: zoom keeps working while the camera follows someone. Sony's own add-on (the REA-C1000 box plus a license) costs several thousand dollars and only pans. The cheapest paid add-on is PTZOptics Hive Studio. It runs in the cloud, lists Sony among supported brands, and costs about $270 a year for one tracked camera, but it's non-refundable and the SRG-300SE isn't confirmed. You chose to **build our own**. Hive Studio stays a fallback if our tracking isn't good enough.

## How it works

1. **Camera list (database).** A small SQLite file holds each camera's name, IP address, model, serial number, stream address, job (tracking or presets), saved shots, and a health log. A web page at `http://localhost:8890` shows every camera: online or offline, a live snapshot, its current position and zoom, and whether it's tracking.
2. **Watching.** For each tracking camera, the app pulls the camera's network video, shrinks it, and finds people in it with a free, lightweight person detector that runs offline on this PC.
3. **Who to follow.** It locks on to the person **nearest center stage** and stays on them while they move, even if someone walks in front of them for a moment. A **Re-lock** button switches to whoever is center now, for example when the pastor takes over from the worship leader.
4. **Smooth steering.** It doesn't jump from preset to preset. It nudges pan, tilt and zoom at slowly changing speeds. A "dead zone" in the middle of the picture means small sways don't move the camera.
5. **Framing while tracking.** Three framings: Tight (head and shoulders), Medium (waist up) and Wide (full body). Pressing one zooms **slowly** to that size and keeps the person centered. A **Slow pull-out** button eases from tight to wide over about 10–20 seconds and keeps the person centered the whole time.
6. **Stream Deck.** Companion's buttons call the app over the PC's own network (Companion's generic HTTP connection). Buttons per side camera: Track on/off, Tight / Medium / Wide, Slow pull-out, Re-lock center, and Go to preset then track (for example, swing to the pulpit and start following). The Stage camera gets preset buttons: Drums, Keys, Wide stage and others you name. Its existing presets stay as they are.
7. **Safety.** Track Off freezes the camera where it is. Any normal Companion move, or the Sony remote, also takes over. If the video drops, the camera holds still instead of drifting.

## Build order (each step something you can try)

1. **Camera list and test page.** Database, dashboard, live snapshots, and a few test buttons (nudge, zoom, go to preset), on one camera at a time. ← first piece
2. **Tracking on the Left camera** in "watch-only" mode first: the page draws a box around who it would follow. Then it steers for real, tested on a weekday, never during practice or a service.
3. **Right camera, framing buttons, slow pull-out, Re-lock.**
4. **Stream Deck buttons in Companion.** You unlock the admin page and I add the HTTP connection and the buttons.
5. **Stage camera preset buttons.**
6. **A trial Sunday** with an operator watching and Track Off ready, then tuning: speed, dead zone, framing sizes.

## Things to watch

- Companion also talks to the cameras from port 52381. The app has to share that port without stealing Companion's replies. I'll test this first.
- Dim stage lighting and crowded stages make tracking harder. One person in the center works best.
- The app's code will be kept in this `ptz-tracker/` folder, ready to move into its own GitHub project later (newlight-church-nc/church-work).
