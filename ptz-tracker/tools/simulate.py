"""Practice simulator: test the tracker's steering on a recorded video, no camera needed.

The recorded wide shot is treated as the "world". A pretend PTZ camera looks at part of
it (a moving, zooming crop). The real tracker code steers that pretend camera with the
same commands it would send a Sony camera, including video delay and gentle speed-up,
so we can watch and measure how smooth it is and tune it without anyone on stage.

Example:
    python tools/simulate.py "D:/Practice_11_11_2026 01.mp4" --start 2060 --dur 45 \
        --events "15:medium,30:pullout" --video out.mp4

Prints a short report (how often it moved, how often it reversed direction, how well
centered the person was) and optionally saves a side-by-side video:
left = what the pretend camera sees (with the tracker's boxes), right = whole stage
with the pretend camera's view drawn on it.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nlptz import tracker as trk  # noqa: E402
from nlptz.snapshots import ffmpeg_path  # noqa: E402
from nlptz.tuning import SPEC, TUNE  # noqa: E402
from nlptz.video import CONTENT_H, SIZE  # noqa: E402

FPS = 8.0
VIEW_FRACTION_AT_1X = 0.6      # at zoom 1x the pretend camera sees 60% of the recorded width
MAX_MAG = 5.0                  # limited by the recording's resolution


class VirtualCamera:
    """Behaves like ViscaCamera, but moves a crop around a recorded video."""

    def __init__(self, W: int, H: int, latency: float, accel: float = 80.0):
        self.W, self.H = W, H
        self.px_per_deg = W * VIEW_FRACTION_AT_1X / trk.HFOV_WIDE
        self.pan = 0.0      # degrees from the world center, right = +
        self.tilt = 0.0     # degrees, up = +
        self.zpos = 0.0     # VISCA zoom units
        self.v_pan = self.v_tilt = 0.0
        self.cmd_pan = self.cmd_tilt = 0.0
        self.zoom_rate = 0.0
        self.accel = accel
        self.latency = latency         # command delay (network + camera)
        self.zoom_max = trk.zoom_for_magnification(MAX_MAG)
        self.now = 0.0
        self.pending: deque = deque()  # (apply_time, fn)
        self.commands = 0

    # --- ViscaCamera interface ---
    def _queue(self, fn) -> bool:
        self.commands += 1
        self.pending.append((self.now + self.latency, fn))
        return True

    def pan_tilt(self, pan: int, tilt: int) -> bool:
        def fn():
            self.cmd_pan = trk.speed_to_deg_per_sec(pan)
            self.cmd_tilt = trk.speed_to_deg_per_sec(tilt)
        return self._queue(fn)

    def zoom(self, speed: int) -> bool:
        if speed == 0:
            return self._queue(lambda: setattr(self, "zoom_rate", 0.0))
        return self.zoom_drive(1 if speed > 0 else -1, abs(speed))

    def zoom_drive(self, direction: int, p: int) -> bool:
        full = 40.0 * (3.0 / 40.0) ** (max(0, min(7, p)) / 7)   # seconds for the whole 1x-30x range
        rate = direction * trk.ZOOM_OPTICAL_MAX / full
        return self._queue(lambda: setattr(self, "zoom_rate", rate))

    def zoom_position(self) -> int:
        return int(self.zpos)

    def stop(self) -> None:
        self.pan_tilt(0, 0)
        self.zoom(0)

    def recall_preset(self, n: int) -> bool:
        return True

    # --- physics ---
    def advance(self, t: float) -> None:
        dt = 1 / 80
        while self.now < t - 1e-9:
            while self.pending and self.pending[0][0] <= self.now:
                self.pending.popleft()[1]()
            for axis in ("pan", "tilt"):
                v, c = getattr(self, "v_" + axis), getattr(self, "cmd_" + axis)
                dv = max(-self.accel * dt, min(self.accel * dt, c - v))
                setattr(self, "v_" + axis, v + dv)
                setattr(self, axis, getattr(self, axis) + (v + dv) * dt)
            self.zpos = max(0.0, min(self.zoom_max, self.zpos + self.zoom_rate * dt))
            self._clamp()
            self.now += dt

    def view_rect(self, pan=None, tilt=None, zoom=None):
        pan = self.pan if pan is None else pan
        tilt = self.tilt if tilt is None else tilt
        zoom = self.zpos if zoom is None else zoom
        w = self.W * VIEW_FRACTION_AT_1X / trk.magnification(int(zoom))
        h = w * 9 / 16
        cx = self.W / 2 + pan * self.px_per_deg
        cy = self.H / 2 - tilt * self.px_per_deg
        cx = max(w / 2, min(self.W - w / 2, cx))
        cy = max(h / 2, min(self.H - h / 2, cy))
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2

    def _clamp(self) -> None:
        x1, y1, x2, y2 = self.view_rect()
        self.pan = ((x1 + x2) / 2 - self.W / 2) / self.px_per_deg
        self.tilt = (self.H / 2 - (y1 + y2) / 2) / self.px_per_deg
        # stop at the edge of the world instead of pushing into it
        if x1 <= 0.5 or x2 >= self.W - 0.5:
            self.v_pan = 0.0
        if y1 <= 0.5 or y2 >= self.H - 0.5:
            self.v_tilt = 0.0

    def state(self):
        return (self.pan, self.tilt, self.zpos)


def frames(path: str, start: float, dur: float, W: int, H: int):
    exe = ffmpeg_path() or "ffmpeg"
    p = subprocess.Popen([exe, "-v", "error", "-ss", str(start), "-t", str(dur), "-i", path, "-an",
                          "-vf", f"fps={FPS},scale={W}:{H}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE)
    n = W * H * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(H, W, 3)


def render(world: np.ndarray, rect) -> np.ndarray:
    """Crop the pretend camera's view and shape it like FrameReader's frames (416x416, padded)."""
    img = Image.fromarray(world).crop(tuple(int(round(v)) for v in rect)).resize((SIZE, CONTENT_H), Image.BILINEAR)
    out = np.full((SIZE, SIZE, 3), 0x72, np.uint8)
    out[:CONTENT_H] = np.asarray(img)
    return out


def parse_events(s: str):
    out = []
    for part in filter(None, (s or "").split(",")):
        t, what = part.split(":")
        out.append((float(t), what.strip()))
    return sorted(out)


def run(args) -> dict:
    W, H = 1920, 1080
    for kv in args.set or []:
        k, v = kv.split("=")
        if k not in SPEC:
            raise SystemExit(f"unknown setting {k}")
        TUNE.values[k] = TUNE._clean(k, v)        # just for this run, not saved
    cam = VirtualCamera(W, H, latency=args.cmd_delay)
    cam.pan, cam.tilt, cam.zpos = args.pan, args.tilt, trk.zoom_for_magnification(args.zoom)
    cam._clamp()
    t0 = 1_000_000.0                  # sim clock (seconds), far from 0 so "time since" math works
    cam.now = t0
    tr = trk.Tracker("sim", "", "", cam=cam, log=False)
    tr.mode = "steer"
    if args.framing:
        tr.set_framing(args.framing, args.zoom_speed)
    events = parse_events(args.events)
    history: deque = deque()          # (time, cam state) for video delay
    out = None
    if args.video:
        out = subprocess.Popen([ffmpeg_path() or "ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),
                                "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
                                args.video], stdin=subprocess.PIPE)
    rows = []
    lock_events = lost_events = blips = 0
    prev_status = ""
    for k, world in enumerate(frames(args.video_in, args.start, args.dur, W, H)):
        now = t0 + k / FPS
        while events and events[0][0] <= k / FPS:
            _, what = events.pop(0)
            if what in ("tight", "medium", "wide"):
                tr.set_framing(what, args.zoom_speed)
            elif what == "pullout":
                tr.set_framing("wide", 0)
            elif what == "hold":
                tr.set_framing(None)
            elif what == "relock":
                tr.relock()
        cam.advance(now)
        history.append((now, cam.state()))
        while len(history) > 1 and history[1][0] <= now - args.video_delay:
            history.popleft()
        seen_state = history[0][1]
        frame = render(world, cam.view_rect(*seen_state))
        tr.step(frame, now)
        if tr.status == "following" and not prev_status.startswith(("following", "lost")):
            lock_events += 1
        if tr.status.startswith("lost") and prev_status == "following":
            blips += 1
        if prev_status in ("following", "lost sight, holding") and tr.status.startswith(("looking", "checking")):
            lost_events += 1
        prev_status = tr.status
        tgt = tr.target
        rows.append({"t": k / FPS, "status": tr.status, "pt": tr.planned_pt, "zdir": tr.zoom_dir,
                     "ex": (trk._center(tgt)[0] - 0.5) if tgt else None,
                     "head": tr._head_top() if tgt else None, "top": tgt[1] if tgt else None,
                     "people": [p["box"] for p in tr.people], "zoom": cam.zpos, "tilt": round(cam.tilt, 2)})
        if out:
            left = tr.view_image(832)
            right = Image.fromarray(world).resize((832, 468))
            d = ImageDraw.Draw(right)
            s = 832 / W
            x1, y1, x2, y2 = cam.view_rect()
            d.rectangle([x1 * s, y1 * s, x2 * s, y2 * s], outline=(255, 210, 80), width=3)
            d.text((8, 8), f"t={k / FPS:5.1f}s  zoom {trk.magnification(int(cam.zpos)):.1f}x  pan {cam.v_pan:+.1f} deg/s", fill=(255, 255, 255))
            both = Image.new("RGB", (1664, 468))
            both.paste(left, (0, 0))
            both.paste(right, (832, 0))
            import io
            buf = io.BytesIO()
            both.save(buf, "JPEG", quality=82)
            out.stdin.write(buf.getvalue())
    if out:
        out.stdin.close()
        out.wait()
    if args.dump:
        with open(args.dump, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + chr(10))
    return report(rows, lock_events, lost_events, cam.commands, blips)


def report(rows, locks, losts, commands, blips=0) -> dict:
    n = len(rows)
    dur = n / FPS
    fol = [r for r in rows if r["status"] == "following"]
    ex = [abs(r["ex"]) for r in fol if r["ex"] is not None]
    pans = [r["pt"][0] for r in rows]
    tilts = [r["pt"][1] for r in rows]

    def changes(seq):
        return sum(1 for a, b in zip(seq, seq[1:]) if a != b)

    def reversals(seq):
        nz = [np.sign(v) for v in seq if v]
        return sum(1 for a, b in zip(nz, nz[1:]) if a != b)

    def starts(seq):
        return sum(1 for a, b in zip(seq, seq[1:]) if a == 0 and b != 0)

    heads = [r["head"] for r in fol if r["head"] is not None]
    per_min = 60 / max(dur, 1e-6)
    return {
        "seconds": round(dur, 1),
        "following_pct": round(100 * len(fol) / max(n, 1)),
        "locks": locks, "lost": losts, "blips_per_min": round(blips * 60 / max(dur, 1e-6), 1),
        "centered_pct": round(100 * sum(1 for e in ex if e < 0.10) / max(len(ex), 1)),
        "avg_off_center": round(float(np.mean(ex)), 3) if ex else None,
        "pan_starts_per_min": round(starts(pans) * per_min, 1),
        "pan_reversals_per_min": round(reversals(pans) * per_min, 1),
        "tilt_starts_per_min": round(starts(tilts) * per_min, 1),
        "tilt_reversals_per_min": round(reversals(tilts) * per_min, 1),
        "speed_changes_per_min": round((changes(pans) + changes(tilts)) * per_min, 1),
        "moving_pct": round(100 * sum(1 for p, t in zip(pans, tilts) if p or t) / max(n, 1)),
        "head_spread": round(float(np.std(heads)), 3) if heads else None,
        "commands_sent": commands,
    }


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_in")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--dur", type=float, default=60)
    ap.add_argument("--pan", type=float, default=0.0, help="starting pan in degrees (right = +)")
    ap.add_argument("--tilt", type=float, default=0.0)
    ap.add_argument("--zoom", type=float, default=1.6, help="starting zoom, e.g. 1.6 (= 1.6x)")
    ap.add_argument("--framing", choices=["tight", "medium", "wide"])
    ap.add_argument("--zoom-speed", type=int, default=1)
    ap.add_argument("--events", default="", help='e.g. "10:tight,25:pullout,40:relock"')
    ap.add_argument("--video-delay", type=float, default=0.5, help="seconds the camera's video lags behind")
    ap.add_argument("--cmd-delay", type=float, default=0.08, help="seconds before the camera acts on a command")
    ap.add_argument("--set", action="append", help="try a setting for this run only, e.g. --set catch_up_x=2.0")
    ap.add_argument("--dump", help="save every frame's numbers (JSON lines) here")
    ap.add_argument("--video", help="save a side-by-side MP4 here")
    a = ap.parse_args(argv)
    rep = run(a)
    print(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    main()
