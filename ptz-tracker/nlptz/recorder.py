"""Keeps a record of each tracking session so tests can be reviewed and tuned afterwards.

- Always: one line per frame (who it follows, where they are, what it told the camera)
  in data/sessions/<camera>-<date>.jsonl. About 1 MB per hour. Files older than 14 days
  are deleted.
- When "Record test video" is on: the tracker's view (with boxes) is also saved as an
  MP4 at 4 frames per second in data/sessions/, so we can watch what happened.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .snapshots import ffmpeg_path
from .tuning import TUNE

DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"
KEEP_DAYS = 14
VIDEO_FPS = 4


class SessionLog:
    def __init__(self, cam_id: str, folder: Path = DIR):
        self.cam_id = cam_id
        self.folder = folder
        self._fh = None
        self._day = ""
        self._lock = threading.Lock()
        self.video_on = False
        self.video_path: Path | None = None
        self._ff: subprocess.Popen | None = None
        self._last_video = 0.0

    # ---- per-frame log ----
    def _file(self):
        day = time.strftime("%Y-%m-%d")
        if self._fh is None or day != self._day:
            if self._fh:
                self._fh.close()
            self.folder.mkdir(parents=True, exist_ok=True)
            self._cleanup()
            self._fh = open(self.folder / f"{self.cam_id}-{day}.jsonl", "a", encoding="utf-8")
            self._day = day
        return self._fh

    def _write(self, obj: dict) -> None:
        with self._lock:
            try:
                fh = self._file()
                fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
                fh.flush()
            except OSError:
                pass

    def event(self, what: str) -> None:
        self._write({"t": round(time.time(), 2), "event": what, "tune": TUNE.values if what.startswith("mode") else None})

    def frame(self, now: float, tr) -> None:
        self._write({
            "t": round(now, 2), "mode": tr.mode, "status": tr.status,
            "target": [round(v, 3) for v in tr.target] if tr.target else None,
            "head": round(tr._head_top(), 3) if tr.target else None,
            "n": len(tr.people), "drop": tr.dropped,
            "pt": list(tr.planned_pt), "z": tr.zoom_pos, "zdir": tr.zoom_dir if tr.mode == "steer" else 0,
            "framing": tr.framing,
        })
        if self.video_on and now - self._last_video >= 1 / VIDEO_FPS:
            self._last_video = now
            self._video_frame(tr)

    def close(self) -> None:
        self.stop_video()
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None

    def _cleanup(self) -> None:
        cutoff = time.time() - KEEP_DAYS * 86400
        for f in self.folder.glob(f"{self.cam_id}-*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    # ---- test video ----
    def start_video(self) -> Path | None:
        exe = ffmpeg_path()
        if not exe or self.video_on:
            return self.video_path
        self.folder.mkdir(parents=True, exist_ok=True)
        self.video_path = self.folder / f"{self.cam_id}-{time.strftime('%Y-%m-%d_%H%M%S')}.mp4"
        self._ff = subprocess.Popen(
            [exe, "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(VIDEO_FPS),
             "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
             str(self.video_path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.video_on = True
        return self.video_path

    def _video_frame(self, tr) -> None:
        img = tr.view_image(832)
        if img is None or not self._ff or self._ff.poll() is not None:
            return
        import io
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        try:
            self._ff.stdin.write(buf.getvalue())
        except OSError:
            self.video_on = False

    def stop_video(self) -> Path | None:
        if self._ff:
            try:
                self._ff.stdin.close()
                self._ff.wait(timeout=15)
            except Exception:
                self._ff.kill()
            self._ff = None
        self.video_on = False
        return self.video_path
