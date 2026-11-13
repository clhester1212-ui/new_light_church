"""Local web server: dashboard + HTTP API (Companion's buttons will call this later).

Camera moves are LOCKED by default. Unlock them from the dashboard for 10 minutes at a
time, so nothing moves a camera by accident during practice or a service.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, snapshots
from .measure import measure_delay
from .tracker import TrackerManager
from .tuning import TUNE
from .visca import ViscaCamera, transport

STATIC = Path(__file__).resolve().parent / "static"
UNLOCK_SECONDS = 600
TRACKERS = TrackerManager()


DECK_FILE = Path(__file__).resolve().parent.parent / "data" / "deck.json"


class State:
    moves_unlocked_until = 0.0
    live: dict[str, dict] = {}  # latest status per camera
    # Stream Deck switch: when on, calls that end in ?from=deck may move cameras without the
    # 10-minute unlock (so Companion buttons work during a service). Off until turned on.
    deck_enabled = json.loads(DECK_FILE.read_text()).get("enabled", False) if DECK_FILE.exists() else False

    @classmethod
    def moves_unlocked(cls) -> bool:
        return time.time() < cls.moves_unlocked_until

    @classmethod
    def may_move(cls, q: dict) -> bool:
        return cls.moves_unlocked() or (cls.deck_enabled and q.get("from") == "deck")

    @classmethod
    def set_deck(cls, on: bool) -> None:
        cls.deck_enabled = on
        DECK_FILE.parent.mkdir(parents=True, exist_ok=True)
        DECK_FILE.write_text(json.dumps({"enabled": on}))


def poll_status_forever(interval: float = 5.0) -> None:
    """Ask every active camera for its status every few seconds and keep it in memory."""
    last_logged: dict[str, float] = {}
    misses: dict[str, int] = {}
    while True:
        for cam in db.cameras():
            st = ViscaCamera(cam["ip"]).status()
            st["checked"] = time.time()
            prev = State.live.get(cam["id"], {})
            # One missed reply happens (e.g. while a video stream is opening);
            # only call a camera offline after two misses in a row.
            misses[cam["id"]] = 0 if st["online"] else misses.get(cam["id"], 0) + 1
            if not st["online"] and misses[cam["id"]] < 2 and prev.get("online"):
                continue
            State.live[cam["id"]] = st
            # write to the log on change of online state, or once a minute
            if prev.get("online") != st["online"] or time.time() - last_logged.get(cam["id"], 0) > 60:
                db.log_status(cam["id"], st)
                last_logged[cam["id"]] = time.time()
                if prev and prev.get("online") != st["online"]:
                    db.log_event(cam["id"], "online" if st["online"] else "offline")
        time.sleep(interval)


def _timed(start, stop, ms: int) -> None:
    """Start a move, then stop it after `ms` milliseconds (stop is sent twice, to be safe)."""
    start()
    def _stop():
        time.sleep(ms / 1000)
        stop()
        time.sleep(0.1)
        stop()
    threading.Thread(target=_stop, daemon=True).start()


NUDGE = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}


class Handler(BaseHTTPRequestHandler):
    server_version = "NLPTZ/0.1"

    def log_message(self, fmt, *args):  # quiet console
        pass

    # ---- helpers ----
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode())

    # ---- routes ----
    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if url.path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if parts == ["api", "cameras"]:
            cams = db.cameras(include_retired=True)
            for c in cams:
                c["live"] = State.live.get(c["id"])
                c["edges"] = db.stage_edges(c["id"])
            return self._json({"cameras": cams, "moves_unlocked": State.moves_unlocked(), "deck_enabled": State.deck_enabled,
                               "unlocked_seconds_left": max(0, int(State.moves_unlocked_until - time.time())),
                               "reply_port_ok": transport().listening})
        if parts == ["api", "events"]:
            return self._json(db.recent_events())
        if parts == ["api", "tuning"]:
            return self._json(TUNE.describe())
        if parts == ["api", "track"]:
            return self._json({k: t.state() for k, t in TRACKERS.trackers.items()})
        if len(parts) == 4 and parts[:2] == ["api", "track"] and parts[3] == "view.jpg":
            t = TRACKERS.trackers.get(parts[2])
            img = t.view_jpeg() if t else None
            if not img:
                return self._json({"error": "not tracking"}, 404)
            return self._send(200, img, "image/jpeg")
        if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "snapshot.jpg":
            cam = db.camera(parts[2])
            if not cam:
                return self._json({"error": "no such camera"}, 404)
            img = snapshots.snapshot(cam["id"], cam["rtsp_url"])
            if not img:
                return self._json({"error": "no picture"}, 503)
            return self._send(200, img, "image/jpeg")
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [p for p in url.path.split("/") if p]

        if parts == ["api", "moves", "unlock"]:
            State.moves_unlocked_until = time.time() + UNLOCK_SECONDS
            db.log_event(None, "moves unlocked", f"{UNLOCK_SECONDS // 60} min")
            return self._json({"ok": True})
        if parts == ["api", "moves", "lock"]:
            State.moves_unlocked_until = 0
            db.log_event(None, "moves locked")
            return self._json({"ok": True})
        if parts == ["api", "deck"]:
            on = q.get("on") == "1"
            State.set_deck(on)
            db.log_event(None, "Stream Deck control " + ("on" if on else "off"))
            return self._json({"ok": True, "deck_enabled": on})

        # Live tuning: POST /api/tuning?catch_up_x=2.0&dead_start_x=0.12   or   /api/tuning/reset
        if parts == ["api", "tuning"]:
            changes = {k: v for k, v in q.items() if k != "from"}
            try:
                TUNE.update(changes)
            except ValueError:
                return self._json({"error": "bad number"}, 400)
            db.log_event(None, "tuning", ", ".join(f"{k}={TUNE[k]}" for k in changes if k in TUNE.values))
            return self._json({"ok": True, "tuning": TUNE.describe()})
        if parts == ["api", "tuning", "reset"]:
            TUNE.reset()
            db.log_event(None, "tuning reset to defaults")
            return self._json({"ok": True, "tuning": TUNE.describe()})

        # Tracking controls.
        #   watch / off / relock never move a camera, so they need no unlock.
        #   steer, frame, pullout and preset move the camera, so they need moves unlocked
        #   (Stream Deck buttons will get their own switch in step 4).
        if len(parts) in (4, 5) and parts[:2] == ["api", "track"]:
            cam_row = db.camera(parts[2])
            if not cam_row or cam_row["role"] != "track":
                return self._json({"error": "this camera isn't set up for tracking"}, 404)
            t = TRACKERS.get(cam_row["id"], cam_row["rtsp_url"], cam_row["ip"])
            act = parts[3]
            detail = ""
            if act in ("steer", "frame", "pullout", "preset", "measure-delay") and not State.may_move(q):
                return self._json({"error": "Camera moves are locked. Unlock them on the dashboard first."}, 423)
            try:
                if act == "watch":
                    t.start("watch")
                elif act == "steer":
                    t.start("steer")
                elif act == "off":
                    t.stop()
                elif act == "relock":
                    t.relock()
                elif act == "frame":
                    size = q.get("size", "")
                    if size not in ("tight", "medium", "wide", "hold"):
                        return self._json({"error": "size must be tight/medium/wide/hold"}, 400)
                    t.set_framing(None if size == "hold" else size, int(q.get("speed", 1)))
                    if t.mode == "off":
                        t.start("steer")
                    detail = f"{size}, zoom speed {t.zoom_speed}"
                elif act == "pullout":
                    t.set_framing("wide", 0, out_only=True)  # slowest zoom speed, never zooms in
                    if t.mode == "off":
                        t.start("steer")
                    detail = "slow pull-out to wide"
                elif act == "record":
                    on = q.get("on", "1") == "1"
                    if on and t.mode == "off":
                        return self._json({"error": "Start tracking (or Watch only) first."}, 409)
                    path = t.log.start_video() if on else t.log.stop_video()
                    detail = ("recording test video" if on else "stopped test video") + (f": {path.name}" if path else "")
                elif act == "measure-delay":
                    if t.mode == "steer":
                        return self._json({"error": "Turn tracking off (or Watch only) first."}, 409)
                    res = measure_delay(cam_row["ip"], cam_row["rtsp_url"])
                    if res.get("ok"):
                        TUNE.update({"video_delay": round(res["delay"] / 0.05) * 0.05})
                    db.log_event(cam_row["id"], "measured video delay",
                                 f"{res['delay']} s {res['tries']}" if res.get("ok") else res.get("error", ""))
                    return self._json(res, 200 if res.get("ok") else 422)
                elif act == "preset" and len(parts) == 5:
                    n = int(parts[4])
                    t.set_framing(None)
                    t.pause(4.0)          # hold still while the preset move runs
                    t.cam.recall_preset(n)
                    t.relock()            # then follow whoever is center of the new shot
                    if t.mode != "steer":
                        t.start("steer")
                    detail = f"preset {n}, then track"
                else:
                    return self._json({"error": "unknown action"}, 404)
            except ValueError:
                return self._json({"error": "bad number"}, 400)
            db.log_event(cam_row["id"], f"tracking {act}", detail)
            return self._json({"ok": True, "state": t.state()})

        if len(parts) < 4 or parts[:2] != ["api", "cameras"]:
            return self._json({"error": "not found"}, 404)
        cam_row = db.camera(parts[2])
        if not cam_row or cam_row["role"] == "retired":
            return self._json({"error": "no such camera"}, 404)
        cam = ViscaCamera(cam_row["ip"])
        action = parts[3]

        if action == "stop":  # stop always works, even when locked
            t = TRACKERS.trackers.get(cam_row["id"])
            if t and t.mode == "steer":
                t.start("watch")  # STOP also ends steering (keeps watching)
            cam.stop()
            db.log_event(cam_row["id"], "stop")
            return self._json({"ok": True})

        # Stage edges ("stay on stage"). Setting one reads where the camera points now; it
        # doesn't move anything, so it needs no unlock.
        #   POST /api/cameras/<id>/edge?set=left|right|low|high   or   ?clear=1
        if action == "edge":
            if q.get("clear") == "1":
                edges = db.clear_stage_edges(cam_row["id"])
                detail = "stage edges cleared"
            else:
                which = q.get("set", "")
                if which not in db.EDGES:
                    return self._json({"error": "set must be left/right/low/high"}, 400)
                pt = cam.pan_tilt_position()
                if pt is None:
                    return self._json({"error": "The camera didn't answer. Try again."}, 503)
                value = pt[0] if which in ("left", "right") else pt[1]
                edges = db.set_stage_edge(cam_row["id"], which, value)
                detail = f"{which} stage edge set at {value}"
            t = TRACKERS.trackers.get(cam_row["id"])
            if t:
                t.edges = edges
            db.log_event(cam_row["id"], "stage edge", detail)
            return self._json({"ok": True, "edges": edges, "detail": detail})

        if not State.may_move(q):
            return self._json({"error": "Camera moves are locked. Unlock them on the dashboard first."}, 423)

        # A manual move takes over from tracking.
        t = TRACKERS.trackers.get(cam_row["id"])
        if t and t.mode == "steer":
            t.start("watch")

        try:
            if action == "nudge":
                d = q.get("dir", "")
                if d not in NUDGE:
                    return self._json({"error": "dir must be left/right/up/down"}, 400)
                speed = int(q.get("speed", 4))
                ms = min(1500, int(q.get("ms", 300)))
                px, ty = NUDGE[d]
                _timed(lambda: cam.pan_tilt(px * speed, ty * speed), lambda: cam.pan_tilt(0, 0), ms)
                detail = f"{d} speed {speed} for {ms} ms"
            elif action == "zoom":
                d = q.get("dir", "")
                if d not in ("in", "out"):
                    return self._json({"error": "dir must be in/out"}, 400)
                speed = int(q.get("speed", 2))
                ms = min(3000, int(q.get("ms", 500)))
                _timed(lambda: cam.zoom(speed if d == "in" else -speed), lambda: cam.zoom(0), ms)
                detail = f"{d} speed {speed} for {ms} ms"
            elif action == "preset" and len(parts) == 5:
                n = int(parts[4])
                cam.recall_preset(n)
                detail = f"preset {n}"
            elif action == "position":
                bits = []
                if "pan" in q and "tilt" in q:
                    ok = cam.pan_tilt_to(int(q["pan"]), int(q["tilt"]), int(q.get("speed", 8)))
                    bits.append(f"pan {q['pan']} tilt {q['tilt']}" + ("" if ok else " NOT ACCEPTED"))
                if "zoom" in q:
                    ok = cam.zoom_to(int(q["zoom"]))
                    bits.append(f"zoom {q['zoom']}" + ("" if ok else " NOT ACCEPTED"))
                if not bits:
                    return self._json({"error": "give pan+tilt and/or zoom"}, 400)
                detail = ", ".join(bits)
            elif action == "home":
                cam.home()
                detail = ""
            else:
                return self._json({"error": "unknown action"}, 404)
        except ValueError:
            return self._json({"error": "bad number"}, 400)
        db.log_event(cam_row["id"], action, detail)
        return self._json({"ok": "NOT ACCEPTED" not in detail, "detail": detail})


def serve(host: str = "127.0.0.1", port: int = 8890) -> None:
    db.con()
    transport()
    threading.Thread(target=poll_status_forever, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"New Light PTZ Tracker running at http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
    db.log_event(None, "app started")
    httpd.serve_forever()
