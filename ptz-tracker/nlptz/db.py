"""SQLite camera database: the list of cameras, their presets, and a health/event log."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "ptz.sqlite"
SEED_PATH = ROOT / "cameras.seed.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,      -- short name used in URLs: stage, left, right
    name        TEXT NOT NULL,
    ip          TEXT NOT NULL,
    model       TEXT,
    serial      TEXT,
    rtsp_url    TEXT,
    role        TEXT NOT NULL DEFAULT 'presets',   -- track | presets | retired
    companion   TEXT,                  -- Companion connection label
    notes       TEXT,
    sort        INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS presets (
    camera_id   TEXT NOT NULL REFERENCES cameras(id),
    number      INTEGER NOT NULL,      -- VISCA preset number (0 = Companion's P1)
    name        TEXT NOT NULL,
    PRIMARY KEY (camera_id, number)
);
CREATE TABLE IF NOT EXISTS status_log (
    ts          REAL NOT NULL,
    camera_id   TEXT NOT NULL,
    online      INTEGER NOT NULL,
    power       TEXT, pan INTEGER, tilt INTEGER, zoom INTEGER
);
CREATE TABLE IF NOT EXISTS stage_edges (     -- "stay on stage": tracking never steers past these
    camera_id   TEXT PRIMARY KEY REFERENCES cameras(id),
    pan_left    INTEGER,               -- camera pan/tilt numbers, as the dashboard shows them
    pan_right   INTEGER,
    tilt_low    INTEGER,
    tilt_high   INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    ts          REAL NOT NULL,
    camera_id   TEXT,
    action      TEXT NOT NULL,
    detail      TEXT
);
"""

_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


_con: sqlite3.Connection | None = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = connect()
        _con.executescript(SCHEMA)
        if not _con.execute("SELECT 1 FROM cameras LIMIT 1").fetchone():
            seed(_con)
    return _con


def seed(c: sqlite3.Connection) -> None:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for i, cam in enumerate(data["cameras"]):
        c.execute(
            "INSERT OR REPLACE INTO cameras (id,name,ip,model,serial,rtsp_url,role,companion,notes,sort)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cam["id"], cam["name"], cam["ip"], cam.get("model"), cam.get("serial"), cam.get("rtsp_url"),
             cam.get("role", "presets"), cam.get("companion"), cam.get("notes"), i),
        )
        for num, name in cam.get("presets", {}).items():
            c.execute("INSERT OR REPLACE INTO presets VALUES (?,?,?)", (cam["id"], int(num), name))
    c.commit()


def cameras(include_retired: bool = False) -> list[dict]:
    with _lock:
        rows = con().execute("SELECT * FROM cameras ORDER BY sort").fetchall()
        out = []
        for r in rows:
            if r["role"] == "retired" and not include_retired:
                continue
            d = dict(r)
            d["presets"] = [dict(p) for p in con().execute(
                "SELECT number, name FROM presets WHERE camera_id=? ORDER BY number", (r["id"],))]
            last = con().execute(
                "SELECT * FROM status_log WHERE camera_id=? ORDER BY ts DESC LIMIT 1", (r["id"],)).fetchone()
            d["last_status"] = dict(last) if last else None
            out.append(d)
        return out


def camera(cam_id: str) -> dict | None:
    for c in cameras(include_retired=True):
        if c["id"] == cam_id:
            return c
    return None


EDGES = {"left": "pan_left", "right": "pan_right", "low": "tilt_low", "high": "tilt_high"}


def stage_edges(cam_id: str) -> dict:
    """{'left': pan, 'right': pan, 'low': tilt, 'high': tilt}; missing edges are None."""
    with _lock:
        r = con().execute("SELECT * FROM stage_edges WHERE camera_id=?", (cam_id,)).fetchone()
    return {k: (r[col] if r else None) for k, col in EDGES.items()}


def set_stage_edge(cam_id: str, edge: str, value: int | None) -> dict:
    col = EDGES[edge]
    with _lock:
        con().execute("INSERT OR IGNORE INTO stage_edges (camera_id) VALUES (?)", (cam_id,))
        con().execute(f"UPDATE stage_edges SET {col}=? WHERE camera_id=?", (value, cam_id))
        con().commit()
    return stage_edges(cam_id)


def clear_stage_edges(cam_id: str) -> dict:
    with _lock:
        con().execute("DELETE FROM stage_edges WHERE camera_id=?", (cam_id,))
        con().commit()
    return stage_edges(cam_id)


def log_status(cam_id: str, st: dict) -> None:
    with _lock:
        con().execute(
            "INSERT INTO status_log VALUES (?,?,?,?,?,?,?)",
            (time.time(), cam_id, int(st.get("online", False)), st.get("power"),
             st.get("pan"), st.get("tilt"), st.get("zoom")),
        )
        # keep the log small: last 7 days
        con().execute("DELETE FROM status_log WHERE ts < ?", (time.time() - 7 * 86400,))
        con().commit()


def log_event(cam_id: str | None, action: str, detail: str = "") -> None:
    with _lock:
        con().execute("INSERT INTO events VALUES (?,?,?,?)", (time.time(), cam_id, action, detail))
        con().commit()


def recent_events(limit: int = 30) -> list[dict]:
    with _lock:
        return [dict(r) for r in con().execute("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))]
