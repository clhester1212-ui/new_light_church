"""Measures how late the camera's network video arrives (needed to stop overshooting).

How: watch the video, give the camera one short, small pan, and time how long it takes
for the picture to start changing. Then put the camera back exactly where it was.
Needs the stage lit (a dark picture doesn't show the move) and moves unlocked.
"""
from __future__ import annotations

import time

import numpy as np

from .video import CONTENT_H, FrameReader
from .visca import ViscaCamera


def measure_delay(ip: str, rtsp_url: str, tries: int = 2) -> dict:
    cam = ViscaCamera(ip)
    start_pos = cam.status()
    if "pan" not in start_pos:
        return {"ok": False, "error": "camera didn't report its position"}
    reader = FrameReader(rtsp_url, fps=15).start()
    results = []
    try:
        if not reader.wait_new(0, timeout=8):
            return {"ok": False, "error": "no video from the camera"}
        time.sleep(1.5)  # let the stream settle
        for i in range(tries):
            frames: list[tuple[float, np.ndarray]] = []

            def grab(seconds: float):
                end = time.time() + seconds
                seen = reader.frame_count
                while time.time() < end:
                    if reader.wait_new(seen, timeout=0.5):
                        seen = reader.frame_count
                        f = reader.frame[:CONTENT_H].astype(np.int16)
                        frames.append((reader.frame_time, f))

            grab(1.0)
            base = [f for _, f in frames]
            if not base:
                return {"ok": False, "error": "no video frames"}
            noise = max(float(np.abs(a - b).mean()) for a, b in zip(base, base[1:])) if len(base) > 1 else 1.0
            t_cmd = time.time()
            direction = 1 if i % 2 == 0 else -1
            cam.pan_tilt(4 * direction, 0)
            time.sleep(0.35)
            cam.pan_tilt(0, 0)
            frames.clear()
            grab(2.5)
            ref = base[-1]
            changed = None
            for t, f in frames:
                if float(np.abs(f - ref).mean()) > max(3.0, noise * 3):
                    changed = t
                    break
            if changed is not None:
                results.append(changed - t_cmd)
            time.sleep(0.3)
    finally:
        reader.stop()
        cam.pan_tilt_to(start_pos["pan"], start_pos["tilt"], 6)  # put it back exactly
    if not results:
        return {"ok": False, "error": "couldn't see the picture move (is the stage lit?)"}
    delay = float(np.median(results))
    return {"ok": True, "delay": round(delay, 2), "tries": [round(r, 2) for r in results]}
