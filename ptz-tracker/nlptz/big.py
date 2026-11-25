"""Big = the ATEM 2 M/E Constellation HD. Its input names, program/preview, and a still of every input.

Big can't send live video over the network, so the app shows stills: tools/big_atem.js cuts
M/E 1 program through each input, captures a still, and puts program back. That cut is visible
on Big's output, so it's a setup-only button.

Settings live in data/big.json: {"ip": "...", "links": {"stage": 2, "left": 18, "right": 20}}.
"links" says which Big input each app camera is plugged into.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "big.json"
STILLS = ROOT / "data" / "big_stills"
HELPER = ROOT / "tools" / "big_atem.js"
DEFAULT = {"ip": "192.168.11.112", "links": {"stage": 2, "left": 18, "right": 20}}
VIDEO_MODES = {9: "1080p24", 10: "1080p25", 11: "1080p29.97", 12: "1080p50", 13: "1080p59.94",
               26: "1080p30", 27: "1080p60", 6: "1080i50", 7: "1080i59.94", 4: "720p50", 5: "720p59.94"}

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": None}
_busy = {"stills": False}


def config() -> dict:
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text())
        return {**DEFAULT, **cfg, "links": {**cfg.get("links", DEFAULT["links"])}}
    return json.loads(json.dumps(DEFAULT))


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))


def _node() -> str | None:
    found = sorted(glob.glob(r"C:\Program Files\Companion\resources\node-runtimes\node*\node.exe"))
    return found[-1] if found else shutil.which("node")


def _ffmpeg() -> str | None:
    from .snapshots import ffmpeg_path
    return ffmpeg_path()


def _run(*args: str, timeout: float = 15) -> dict:
    node = _node()
    if not node:
        return {"error": "Can't find Companion's Node (needed to talk to Big)."}
    try:
        p = subprocess.run([node, str(HELPER), config()["ip"], *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"error": "Big took too long to answer."}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"error": (p.stderr or p.stdout or "no reply").strip()[-300:]}


def status(max_age: float = 4.0) -> dict:
    """Big's input names and program/preview, at most a few seconds old."""
    with _lock:
        if _cache["data"] is None or time.time() - _cache["at"] > max_age:
            _cache["data"], _cache["at"] = _run("status"), time.time()
        data = dict(_cache["data"])
    cfg = config()
    data["ip"] = cfg["ip"]
    data["links"] = cfg["links"]
    data["format"] = VIDEO_MODES.get(data.get("videoMode"), str(data.get("videoMode", "?")))
    data["stills"] = stills_info()
    data["stills_running"] = _busy["stills"]
    return data


def rename(input_id: int, name: str, short: str) -> dict:
    res = _run("rename", str(input_id), name, short)
    _cache["at"] = 0
    return res


def link(camera_id: str, input_id: int | None) -> dict:
    cfg = config()
    links = {k: v for k, v in cfg["links"].items() if v != input_id and k != camera_id}
    if input_id:
        links[camera_id] = input_id
    cfg["links"] = links
    save_config(cfg)
    return links


def stills_info() -> dict | None:
    f = STILLS / "stills.json"
    return json.loads(f.read_text()) if f.exists() else None


def take_stills() -> dict:
    """Blocking: about 4 s per input (20 inputs = ~80 s)."""
    ff = _ffmpeg()
    if not ff:
        return {"error": "ffmpeg not found"}
    if _busy["stills"]:
        return {"error": "Already taking pictures."}
    _busy["stills"] = True
    try:
        return _run("stills", str(STILLS), ff, timeout=240)
    finally:
        _busy["stills"] = False
        _cache["at"] = 0


def still_file(input_id: int) -> Path | None:
    p = STILLS / f"in{input_id:02d}.jpg"
    return p if p.exists() else None
