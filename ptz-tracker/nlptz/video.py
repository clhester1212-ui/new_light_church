"""Reads a camera's RTSP video through ffmpeg as small raw frames for the person detector.

ffmpeg shrinks the 1080p picture to 416x234 and pads it to 416x416 (the detector's input
size), a few times per second. Only the newest frame is kept.
"""
from __future__ import annotations

import subprocess
import threading
import time

import numpy as np

from .snapshots import ffmpeg_path

SIZE = 416
CONTENT_H = 234  # 416 * 9/16: the real picture occupies the top 234 rows, the rest is padding


class FrameReader:
    def __init__(self, url: str, fps: float = 8.0):
        self.url = url
        self.fps = fps
        self.frame: np.ndarray | None = None  # HxWx3 uint8 RGB, 416x416
        self.frame_time = 0.0
        self.frame_count = 0
        self.error = ""
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FrameReader":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def wait_new(self, after: int, timeout: float = 2.0) -> bool:
        """Block until a frame newer than `after` arrives."""
        with self._cond:
            return self._cond.wait_for(lambda: self.frame_count > after or self._stop.is_set(), timeout)

    def _run(self) -> None:
        exe = ffmpeg_path()
        frame_bytes = SIZE * SIZE * 3
        vf = f"fps={self.fps},scale={SIZE}:{CONTENT_H},pad={SIZE}:{SIZE}:0:0:color=0x727272"
        while not self._stop.is_set():
            if not exe:
                self.error = "ffmpeg not found"
                time.sleep(5)
                continue
            try:
                self._proc = subprocess.Popen(
                    # Note: "-fflags nobuffer -flags low_delay" made these Sony streams repeat
                    # one frozen frame (tested 2026-11-11), so they are left out.
                    [exe, "-hide_banner", "-loglevel", "error",
                     "-rtsp_transport", "tcp", "-i", self.url, "-an", "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 2,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.error = ""
                while not self._stop.is_set():
                    buf = self._proc.stdout.read(frame_bytes)
                    if not buf or len(buf) < frame_bytes:
                        raise IOError("video stream ended")
                    with self._cond:
                        self.frame = np.frombuffer(buf, np.uint8).reshape(SIZE, SIZE, 3)
                        self.frame_time = time.time()
                        self.frame_count += 1
                        self._cond.notify_all()
            except Exception as e:  # stream dropped: wait a moment and reconnect
                self.error = str(e)
                if self._proc and self._proc.poll() is None:
                    self._proc.kill()
                time.sleep(2)
