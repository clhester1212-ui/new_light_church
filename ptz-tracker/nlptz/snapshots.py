"""Still pictures from each camera's network video (RTSP), using ffmpeg.

Pictures are only grabbed when someone is looking at the dashboard, and are cached
for a few seconds so several browser tabs don't each open a new stream.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import threading
import time

CACHE_SECONDS = 4.0
_cache: dict[str, tuple[float, bytes]] = {}
_locks: dict[str, threading.Lock] = {}


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    # Not on PATH (a start-at-login app gets a short PATH): look where it's usually installed.
    if sys.platform == "darwin":
        patterns = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]   # Homebrew (Apple / Intel)
    else:
        patterns = [os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe")]
    hits = sorted(h for p in patterns for h in glob.glob(p))
    return hits[-1] if hits else None


def snapshot(cam_id: str, rtsp_url: str, width: int = 640) -> bytes | None:
    lock = _locks.setdefault(cam_id, threading.Lock())
    with lock:
        hit = _cache.get(cam_id)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        exe = ffmpeg_path()
        if not exe or not rtsp_url:
            return None
        try:
            proc = subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
                 "-i", rtsp_url, "-frames:v", "1", "-vf", f"scale={width}:-2",
                 "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None
        _cache[cam_id] = (time.monotonic(), proc.stdout)
        return proc.stdout
