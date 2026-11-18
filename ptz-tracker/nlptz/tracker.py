"""Chooses one person to follow and steers the camera to keep them framed.

Modes:
  off    - nothing running
  watch  - shows who it would follow and what it would do; never moves the camera
  steer  - actually moves the camera (pan/tilt to keep the person centered, zoom for framing)

Who to follow:
- When tracking starts (or after Re-lock), it picks the person nearest the CENTER of the
  picture, preferring bigger (closer) people and confident detections.
- After that it sticks with that person, matching them frame to frame by position and size.
- If that person can't be seen for a few seconds, the camera holds still and it goes back
  to picking the center person.

Smoothness: speeds are worked out in degrees/second from how far off-center the person is
and how zoomed in the camera is, so a tight shot moves gently and a wide shot doesn't crawl.
There is a "dead zone" in the middle (with hysteresis) so small sways don't move the camera,
and speed changes are limited per frame so starts and stops are gentle.

Tilt uses the MIDDLE value of the head position over the last second or so, so raised
hands, bows and mic stands don't make the shot bob (it was too strong in the first test).

Framing (Tight / Medium / Wide): measured from the person's full-body height whenever their
whole body is visible, converted to an angle, then the zoom is driven slowly to the size
that fits. If it has never seen their whole body, it zooms out slowly until it can.

All the numbers worth adjusting live in tuning.py and can be changed from the dashboard.
"""
from __future__ import annotations

import io
import math
import threading
import time
import urllib.parse
import urllib.request
from collections import deque

import numpy as np

from .detector import PersonDetector, shadow_like
from .recorder import SessionLog
from .tuning import TUNE
from .video import CONTENT_H, FrameReader
from .visca import ViscaCamera

STALE_VIDEO = 1.0         # stop moving if no fresh frame for this long

FRAMING_LABELS = {"wide": "Wide (full body)", "medium": "Medium (waist up)", "tight": "Tight (head to mid-chest)",
                  "full": "Full wide (camera all the way out)"}
FRAMING_HEAD_Y = {"wide": 0.10, "medium": 0.12, "tight": 0.14, "full": 0.30}

# ---- Sony SRG-300SE optics (approximate) ----
HFOV_WIDE = 71.0          # degrees, fully zoomed out
VFOV_WIDE = 44.0
OPTICAL_ZOOM = 30.0       # 30x
ZOOM_OPTICAL_MAX = 0x4000 # VISCA zoom position at 30x (beyond is digital - never go there)
ZOOM_TOLERANCE = 250      # close enough to the framing target
# Zoom travel in VISCA units per second at Sony zoom speed 0 and 1 (from the 2026-11-11/13 logs)
# Camera position units per degree, measured on the SRG-300SE 2026-11-13/15 (pan from timed
# moves vs. picture shift; tilt from where the stage lip moved in the picture).
PAN_UNITS_PER_DEG = 51.2
TILT_UNITS_PER_DEG = 30.0
# zoom units per second. 3-5 measured on Left 2026-11-17 (zooming out from 12000); speed 2 is an estimate.
ZOOM_RATE = {0: 180, 1: 255, 2: 400, 3: 560, 4: 1030, 5: 1800}

_detector: PersonDetector | None = None
_detector_lock = threading.Lock()


def detector() -> PersonDetector:
    global _detector
    with _detector_lock:
        if _detector is None:
            _detector = PersonDetector()
        return _detector


def magnification(zoom_pos: int) -> float:
    """Rough zoom factor for a VISCA zoom position (log-linear between 1x and 30x)."""
    z = max(0, min(ZOOM_OPTICAL_MAX, zoom_pos))
    return OPTICAL_ZOOM ** (z / ZOOM_OPTICAL_MAX)


def zoom_for_magnification(mag: float) -> int:
    mag = max(1.0, min(OPTICAL_ZOOM, mag))
    return int(round(math.log(mag) / math.log(OPTICAL_ZOOM) * ZOOM_OPTICAL_MAX))


# Measured on the Right SRG-300SE, 2026-11-13: real pan speeds were about 0.47x the
# "1.1 to 100 deg/s" curve (speed 1 = 26 units/s = about 0.5 deg/s, speed 6 = 80 units/s,
# speed 12 = 222+ units/s; 51 units per degree, checked against the picture shift). Using
# the unscaled curve made the tracker think it had caught up too soon, so it stopped after
# a fraction of a second: the jerky stop-and-go seen in Tight.
SPEED_SCALE = 0.47
_SLOW, _FAST = 1.1 * SPEED_SCALE, 100 * SPEED_SCALE


def deg_per_sec_to_speed(v: float, top: int) -> int:
    """Sony pan/tilt speed 1..24, roughly exponential (see SPEED_SCALE)."""
    if v <= 0:
        return 0
    s = 1 + 23 * math.log(max(v, _SLOW) / _SLOW) / math.log(_FAST / _SLOW)
    return max(1, min(top, int(round(s))))


def speed_to_deg_per_sec(s: int) -> float:
    """Inverse of deg_per_sec_to_speed (also used by the simulator)."""
    if s == 0:
        return 0.0
    return math.copysign(_SLOW * (_FAST / _SLOW) ** ((abs(s) - 1) / 23), s)


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _ramp(new: int, old: int, step: int) -> int:
    """Limit how much a speed changes in one go. Stopping (0) and reversing pass through
    slow speeds first, so the camera eases in and out instead of jerking."""
    if new == old:
        return new
    if old != 0 and (new == 0 or (new > 0) != (old > 0)):
        mag = abs(old) - step
        return int(math.copysign(mag, old)) if mag > 0 else 0
    lo, hi = old - step, old + step
    out = max(lo, min(hi, new))
    if out != 0 and old == 0:
        out = int(math.copysign(min(abs(new), step), new))
    return out


class Tracker:
    def __init__(self, cam_id: str, rtsp_url: str, ip: str, cam=None, reader_factory=None, log: bool = True):
        self.cam_id = cam_id
        self.url = rtsp_url
        self.cam = cam if cam is not None else ViscaCamera(ip)
        self._reader_factory = reader_factory or (lambda url: FrameReader(url).start())
        self.mode = "off"
        self.reader = None
        self.thread: threading.Thread | None = None
        self.people: list[dict] = []
        self.target: list[float] | None = None
        self.velocity = (0.0, 0.0)
        self.last_seen = 0.0
        self.locked_since = 0.0
        self.status = "off"
        self.would = ""
        self.would_pan = 0
        self.would_tilt = 0
        self.fps = 0.0
        self.detect_ms = 0.0
        self.frame: np.ndarray | None = None
        # steering state
        self.moving_x = False
        self.moving_y = False
        self.sent_pt = (0, 0)
        self.planned_pt = (0, 0)
        self.zoom_pos: int | None = None
        self.zoom_checked = 0.0
        self.zoom_dir = 0            # what we last told the zoom to do
        self.framing: str | None = None
        self.zoom_speed = 1          # Sony 0 (slowest) .. 7
        self.body_deg: float | None = None   # person's full height in degrees
        self.paused_until = 0.0      # e.g. while a preset move finishes
        self.head_hist: deque = deque()      # (time, top of target box)
        self.cmd_hist: deque = deque([(0.0, 0.0, 0.0)])  # (time, pan deg/s, tilt deg/s) we told the camera
        self.dropped = 0             # detections ignored as shadows (for the dashboard)
        # "Stay on stage": camera pan/tilt numbers it must not steer past (None = no limit)
        self.edges: dict = {"left": None, "right": None, "low": None, "high": None}
        self.pt_pos: tuple[int, int] | None = None
        self.pt_checked = 0.0
        self.at_edge = ""
        self._ax: dict = {}          # per-axis move timing (see _axis)
        self._glides: dict = {}      # planned pan/tilt glides during a zoom (see _glide)
        self._candidates: list = []
        self._relock = False
        self._lock = threading.Lock()
        self._times: list[float] = []
        self.log = SessionLog(cam_id) if log else None

    # ---- control ----
    def _tell_companion(self) -> None:
        """Let Companion show this camera's mode (custom variable ptz_<cam>_mode = steer/watch/off),
        so the Stream Deck Track buttons light up. Best effort: never slows or breaks tracking."""
        url = (f"http://127.0.0.1:8888/api/custom-variable/ptz_{self.cam_id}_mode/value?"
               + urllib.parse.urlencode({"value": self.mode}))

        def send() -> None:
            try:
                urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=2).close()
            except Exception:
                pass
        threading.Thread(target=send, daemon=True).start()

    def start(self, mode: str = "watch") -> None:
        prev, self.mode = self.mode, mode   # change mode first so the loop stops steering
        if prev != mode:
            self._tell_companion()
        if prev == "steer" and mode != "steer":
            time.sleep(0.15)
            self._halt()
        if mode == "steer" and prev != "steer":
            self.sent_pt, self.zoom_dir = (0, 0), 0
        if self.log and prev != mode:
            self.log.event(f"mode {mode}")
        if self.thread and self.thread.is_alive():
            return
        self.reader = self._reader_factory(self.url)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        was_steering = self.mode == "steer"
        self.mode = "off"
        self._tell_companion()
        if was_steering:
            time.sleep(0.15)
            self._halt()
        if self.reader:
            self.reader.stop()
        self.reader = None
        if self.log:
            self.log.event("mode off")
            self.log.close()
        with self._lock:
            self.people, self.target, self.status, self.would = [], None, "off", ""
            self.would_pan = self.would_tilt = 0
            self.framing = None

    def relock(self) -> None:
        self._relock = True
        self.body_deg = None
        self._candidates = []
        if self.log:
            self.log.event("relock")

    def next_person(self, direction: str) -> bool:
        """Switch straight to the nearest person to the left or right of the one we follow
        (or of the center, if following no one). Keeps the measured height, since people
        on stage are about the same size, so the framing carries on without re-measuring."""
        with self._lock:
            ref = _center(self.target)[0] if self.target else 0.5
            sign = -1 if direction == "left" else 1
            options = [p for p in self.people if p["score"] >= 0.45 and not p.get("shadow")
                       and (_center(p["box"])[0] - ref) * sign > 0.03]
            if not options:
                return False
            p = min(options, key=lambda q: abs(_center(q["box"])[0] - ref))
            now = time.time()
            self.target, self.velocity = list(p["box"]), (0.0, 0.0)
            self.last_seen = self.locked_since = now
            self.status = "following"
            self._candidates = []
            self.head_hist.clear()
            self._remember_head(p["box"][1], now)
            self._ax = {}
        if self.log:
            self.log.event(f"next person {direction}")
        return True

    def set_framing(self, name: str | None, zoom_speed: int = 1, out_only: bool = False) -> None:
        self.framing = name if name in FRAMING_LABELS else None
        self.zoom_speed = max(0, min(7, zoom_speed))
        # Slow pull-out only ever widens the shot, never zooms in.
        self.zoom_out_only = out_only
        self._glides = {}            # a new framing gets freshly planned glides
        if self.log:
            self.log.event(f"framing {self.framing} speed {self.zoom_speed}{' out-only' if out_only else ''}")

    def pause(self, seconds: float) -> None:
        """Hold still for a moment (e.g. while a preset move finishes), then carry on."""
        self.paused_until = time.time() + seconds
        if self.mode == "steer":
            self._halt()

    def _halt(self) -> None:
        self.cam.pan_tilt(0, 0)
        time.sleep(0.05)
        if self.zoom_dir:
            self.cam.zoom(0)
        self.sent_pt, self.zoom_dir = (0, 0), 0
        self.planned_pt = (0, 0)
        self.cmd_hist.append((getattr(self, "_now", time.time()), 0.0, 0.0))
        self.moving_x = self.moving_y = False
        self._ax = {}

    # ---- main loop ----
    def _loop(self) -> None:
        seen = 0
        last_reader = None
        while self.mode != "off" and self.reader:
            reader = self.reader
            if reader is not last_reader:
                seen, last_reader = 0, reader
            if not reader.wait_new(seen, timeout=STALE_VIDEO):
                if self.mode == "steer" and (self.sent_pt != (0, 0) or self.zoom_dir):
                    self._halt()  # never keep moving blind
                with self._lock:
                    self.status = "no video" + (f" ({reader.error})" if reader.error else "")
                continue
            seen = reader.frame_count
            self.step(reader.frame, time.time())

    def step(self, frame: np.ndarray, now: float) -> None:
        """One frame: find people, update who we follow, plan and (if steering) move."""
        self._now = now
        t0 = time.perf_counter()
        people = detector().detect(frame, 0.25)  # low bar: helps keep hold of the person; choosing still needs min_start_score
        self._mark_shadows(people)
        self.detect_ms = (time.perf_counter() - t0) * 1000
        self._times = [t for t in self._times if now - t < 3] + [now]
        self.fps = len(self._times) / 3
        if now - self.zoom_checked > (0.4 if self.zoom_dir else 1.5):
            z = self.cam.zoom_position()
            if z is not None:
                self.zoom_pos = z
            self.zoom_checked = now
        has_edges = any(v is not None for v in self.edges.values())
        # (also while only watching: it's needed to tell people on stage from the audience)
        if (self.mode != "off" and has_edges and hasattr(self.cam, "pan_tilt_position")
                and now - self.pt_checked > (0.25 if self.sent_pt != (0, 0) else 1.0)):
            pt = self.cam.pan_tilt_position()
            if pt is not None:
                self.pt_pos = pt
            self.pt_checked = now
        with self._lock:
            self.frame = frame
            self.people = people
            self._update_target(people, now)
            pan, tilt, zdir = self._plan_move(now)
        if self.mode == "steer":
            self._apply(pan, tilt, zdir)
        if self.log:
            self.log.frame(now, self)

    # ---- false detections ----
    def _mark_shadows(self, people: list[dict]) -> None:
        """Flag boxes that look like a shadow of someone else on the screen behind them.
        Flagged boxes are never CHOSEN as the person to follow (but are not hidden, so a
        person already being followed is never dropped by this)."""
        on = bool(TUNE["shadow_filter"])
        n = 0
        for p in people:
            p["shadow"] = on and shadow_like(p, people)
            n += p["shadow"]
        self.dropped = n

    def _update_target(self, people: list[dict], now: float) -> None:
        if self._relock:
            self._relock = False
            self.target = None
            self.head_hist.clear()
        if self.target is not None:
            dt = max(0.05, now - self.last_seen)
            pred = [self.target[0] + self.velocity[0] * dt, self.target[1] + self.velocity[1] * dt,
                    self.target[2] + self.velocity[0] * dt, self.target[3] + self.velocity[1] * dt]
            pc = _center(pred)
            h = max(0.05, pred[3] - pred[1])
            best, best_cost = None, 9.0
            for p in people:
                c = _center(p["box"])
                dist = ((c[0] - pc[0]) ** 2 + (c[1] - pc[1]) ** 2) ** 0.5 / h
                ph = p["box"][3] - p["box"][1]
                size = abs(ph - h) / h
                cost = dist + 0.5 * size - _iou(pred, p["box"])
                # while zooming, size changes fast - be more forgiving
                if dist < 0.9 and size < (0.9 if self.zoom_dir else 0.6) and cost < best_cost:
                    best, best_cost = p, cost
            if best and not self._on_stage(best["box"]):
                # They've left the stage (or it was someone in the seats): after a moment, let go.
                since = getattr(self, "_offstage_since", None) or now
                self._offstage_since = since
                if now - since > 1.0:
                    best = None
                    self.last_seen = 0.0     # give up now and choose again
                    self._offstage_since = None
            else:
                self._offstage_since = None
            if best:
                nb = best["box"]
                a = 0.6
                sm = [a * n + (1 - a) * o for n, o in zip(nb, self.target)]
                oc, ncn = _center(self.target), _center(sm)
                dtv = max(0.05, now - self.last_seen)
                self.velocity = ((ncn[0] - oc[0]) / dtv * 0.5 + self.velocity[0] * 0.5,
                                 (ncn[1] - oc[1]) / dtv * 0.5 + self.velocity[1] * 0.5)
                self.target, self.last_seen = sm, now
                self.status = "following"
                self._remember_head(nb[1], now)
                self._measure_body(nb)
                return
            if now - self.last_seen < TUNE["lost_after"]:
                self.status = "lost sight, holding"
                return
            self.target = None
            # Keep their measured height for a while: when the same person is found again
            # (usually seconds later) it can go straight back to the framing, with no big
            # "zoom out to measure" cycle (seen 2026-11-13 in Tight).
            self.lost_at = now
            self.head_hist.clear()
            if self.log:
                self.log.event("lost")
        best, best_score = None, -9.0
        tallest = max((p["box"][3] - p["box"][1] for p in people if p["score"] >= 0.5), default=0.0)
        for p in people:
            b = p["box"]
            h = b[3] - b[1]
            if p["score"] < TUNE["min_start_score"] or h < TUNE["min_start_height"] or p.get("shadow"):
                continue
            if h < TUNE["min_rel_height"] * tallest:
                continue  # much smaller than the people on stage: a light, mic stand or monitor
            if not self._on_stage(b):
                continue  # someone in the seats or off to the side (2026-11-15: it picked audience members)
            cx = _center(b)[0]
            # Prefer the front of the stage: lower feet in the picture = closer to the camera.
            # (2026-11-15, full band: it picked a guitarist standing behind the singers.)
            s = -abs(cx - 0.5) * 1.5 + h * 1.2 + p["score"] * 0.3 + b[3] * TUNE["prefer_front"]
            if s > best_score:
                best, best_score = p, s
        # confirm: the same candidate (overlapping box) must show up steadily
        self._candidates = (self._candidates + [best["box"] if best else None])[-8:]
        if best:
            hits = sum(1 for c in self._candidates if c and _iou(c, best["box"]) > 0.3)
            if hits >= TUNE["confirm_frames"]:
                if now - getattr(self, "lost_at", -1e9) > 20:
                    self.body_deg = None   # long gap: maybe someone else now, measure again
                self.target, self.velocity = list(best["box"]), (0.0, 0.0)
                self.last_seen = self.locked_since = now
                self.status = "following"
                self._candidates = []
                self.head_hist.clear()
                self._remember_head(best["box"][1], now)
                self._measure_body(best["box"])
                if self.log:
                    self.log.event("locked on")
                return
            self.status = f"checking someone near center ({hits}/{TUNE['confirm_frames']})"
        else:
            self.status = "looking for someone near center"

    def _remember_head(self, top: float, now: float) -> None:
        self.head_hist.append((now, top))
        keep = max(0.0, TUNE["tilt_smooth"])
        while len(self.head_hist) > 1 and now - self.head_hist[0][0] > keep:
            self.head_hist.popleft()

    def _head_top(self) -> float:
        """Middle value of recent head positions (ignores brief arm raises and bows)."""
        if not self.head_hist:
            return self.target[1]
        tops = sorted(t for _, t in self.head_hist)
        return tops[len(tops) // 2]

    def _measure_body(self, box) -> None:
        """If the whole body is in the picture, remember how tall the person is in degrees."""
        # While zooming fast the zoom reading is too stale to trust. A slow zoom (speed 0-2, e.g.
        # "zoom out to measure" during a pull-out) is close enough, and must be allowed or a
        # pull-out that's waiting to measure would never stop.
        if self.zoom_pos is None or (self.zoom_dir and self.zoom_speed > 2):
            return
        # Only a standing person counts: someone sitting, or with legs hidden behind a table or
        # pulpit, makes a squat box and would be taken as a short person (wrong zoom).
        w, h = box[2] - box[0], box[3] - box[1]
        standing = w > 0 and (h / w) * (9 / 16) >= 1.9
        if box[1] > 0.02 and box[3] < 0.97 and standing:
            deg = (box[3] - box[1]) * VFOV_WIDE / magnification(self.zoom_pos)
            # Legs hidden behind monitors, a mic stand or another singer only ever make someone
            # look SHORTER, so use one of the tallest recent readings, not the average.
            # (2026-11-15 worship: Left measured a singer at about half height, so its "Medium"
            # zoomed to about 26x - really a tight shot - and it kept losing them.)
            hist = getattr(self, "_body_hist", None)
            if self.body_deg is None or hist is None:
                hist = self._body_hist = deque()      # a fresh measurement after a reset
            now = time.time()
            hist.append((now, deg))
            while hist and now - hist[0][0] > 45:
                hist.popleft()
            vals = sorted(d for _, d in hist)
            self.body_deg = vals[min(len(vals) - 1, int(len(vals) * 0.85))]

    def _plan_move(self, now: float) -> tuple[int, int, int]:
        """Work out pan/tilt speeds and zoom direction. Returns (pan, tilt, zoom_dir)."""
        pan, tilt, zdir = self._plan_raw(now)
        # ease speed changes
        step = TUNE["speed_step"]
        coasting = self.status == "lost sight, holding" and now - self.last_seen < TUNE["coast"]
        if coasting and now >= self.paused_until:
            # slow down gently while coasting
            pan = _ramp(0, self.planned_pt[0], 1)
            tilt = _ramp(0, self.planned_pt[1], 1)
        elif self.status == "following" and now >= self.paused_until:
            pan = _ramp(pan, self.planned_pt[0], step)
            tilt = _ramp(tilt, self.planned_pt[1], step)
        pan, tilt = self._keep_on_stage(pan, tilt)
        self.planned_pt = (pan, tilt)
        self.would_pan, self.would_tilt = pan, tilt
        bits = []
        if pan:
            bits.append(f"pan {'right' if pan > 0 else 'left'} {abs(pan)}")
        if tilt:
            bits.append(f"tilt {'up' if tilt > 0 else 'down'} {abs(tilt)}")
        if self._ztxt:
            bits.append(self._ztxt)
        if self.at_edge:
            bits.append(f"stopped at stage edge ({self.at_edge})")
        if self.status == "following" and now >= self.paused_until:
            self.would = ", ".join(bits) or "hold still (person centered)"
        return pan, tilt, zdir

    def _axis(self, ax: str, err: float, now: float, start: float, stop: float, urgent: float,
              hold_start: bool = False) -> bool:
        """Decide whether one axis (pan 'x' or tilt 'y') should be moving this frame."""
        st = self._ax.setdefault(ax, {"moving": False, "since": 0.0, "dir": 0})
        sign = 1 if err > 0 else -1
        if st["moving"]:
            in_min_move = now - st["since"] < TUNE["min_move"] and sign == st["dir"] and abs(err) > 0.01
            keep = abs(err) > stop or in_min_move
            if not keep:
                st.update(moving=False, since=now)
            return keep
        rested = now - st["since"] >= TUNE["min_rest"]
        go = abs(err) > start and ((rested and not hold_start) or abs(err) > urgent)
        if go:
            st.update(moving=True, since=now, dir=sign)
        return go

    def _glide(self, ax: str, deg: float, frac: float, t_left: float, now: float, top: int) -> int:
        """A single smooth pan or tilt move during a zoom: returns the Sony speed to use now."""
        g = self._glides.get(ax)
        if g and now < g["until"]:
            if np.sign(deg) == g["dir"] or abs(frac) < 0.10:
                return g["speed"]          # keep gliding at the planned speed
        if abs(frac) <= 0.07:              # close enough: nothing to fix
            self._glides.pop(ax, None)
            return 0
        s = deg_per_sec_to_speed(abs(deg) / t_left, top)
        v = speed_to_deg_per_sec(s)
        speed = int(np.sign(deg)) * s
        self._glides[ax] = {"speed": speed, "dir": np.sign(deg), "until": now + abs(deg) / v}
        return speed

    def _on_stage(self, box) -> bool:
        """Is this person inside the stage edges? Uses where the camera points and the zoom to
        turn the box into camera pan/tilt numbers. Their head must be above the bottom edge
        (the stage lip: seated audience heads are below it) and their middle between the side
        edges. True when edges aren't set or the position isn't known yet."""
        e = self.edges
        if self.pt_pos is None or self.zoom_pos is None or all(v is None for v in e.values()):
            return True
        mag = magnification(self.zoom_pos)
        hfov, vfov = HFOV_WIDE / mag, VFOV_WIDE / mag
        p0, t0 = self.pt_pos
        pan_mid = p0 + (_center(box)[0] - 0.5) * hfov * PAN_UNITS_PER_DEG
        head_tilt = t0 + (0.5 - box[1]) * vfov * TILT_UNITS_PER_DEG
        side = 3 * PAN_UNITS_PER_DEG     # a little leeway at the sides
        if e["left"] is not None and pan_mid < e["left"] - side:
            return False
        if e["right"] is not None and pan_mid > e["right"] + side:
            return False
        if e["low"] is not None and head_tilt < e["low"]:
            return False
        return True

    def _keep_on_stage(self, pan: int, tilt: int) -> tuple[int, int]:
        """Never steer further out past a stage edge. Moving back toward the stage is fine.
        Pan numbers grow to the right; tilt numbers grow upward."""
        self.at_edge = ""
        if self.pt_pos is None:
            return pan, tilt
        p, t = self.pt_pos
        e = self.edges
        hit = []
        if pan < 0 and e["left"] is not None and p <= e["left"]:
            pan, _ = 0, hit.append("left")
        if pan > 0 and e["right"] is not None and p >= e["right"]:
            pan, _ = 0, hit.append("right")
        if tilt < 0 and e["low"] is not None and t <= e["low"]:
            tilt, _ = 0, hit.append("bottom")
        if tilt > 0 and e["high"] is not None and t >= e["high"]:
            tilt, _ = 0, hit.append("top")
        self.at_edge = ", ".join(hit)
        return pan, tilt

    def _plan_raw(self, now: float) -> tuple[int, int, int]:
        self._ztxt = ""
        if now < self.paused_until:
            self.would = "waiting for preset move to finish"
            self.moving_x = self.moving_y = False
            self._ax = {}
            return 0, 0, 0
        if (self.status == "lost sight, holding" and self.planned_pt != (0, 0)
                and now - self.last_seen < TUNE["coast"]):
            # missed them for a moment: keep easing along (the ramp slows it) instead of a dead stop
            self.would = "missed them for a moment - easing along"
            return self.planned_pt[0], self.planned_pt[1], self.zoom_dir
        if self.target is None or self.status != "following":
            self.moving_x = self.moving_y = False
            self._ax = {}
            # A pull-out to wide keeps easing out even with nobody in the shot.
            if self.framing in ("wide", "full") and self.target is None and (self.zoom_pos or 0) > ZOOM_TOLERANCE:
                self.would = "no one found - still easing out to wide"
                return 0, 0, -1
            # Lost them in a Tight or Medium shot: ease out a little to find them, like an
            # operator would, instead of sitting on an empty close-up.
            gone = now - self.last_seen
            if (self.framing in ("tight", "medium") and 1.0 < gone < 8.0
                    and (self.zoom_pos or 0) > 0.45 * ZOOM_OPTICAL_MAX):
                self.would = "lost them - easing out to find them"
                return 0, 0, -1
            self.would = "hold still"
            return 0, 0, 0
        mag = magnification(self.zoom_pos or 0)
        hfov, vfov = HFOV_WIDE / mag, VFOV_WIDE / mag
        head_y = FRAMING_HEAD_Y.get(self.framing or "", TUNE["head_y"])
        # a small lead in the direction they're walking, so we don't trail behind
        lead = TUNE["lead"]
        cx = _center(self.target)[0] + max(-0.05, min(0.05, self.velocity[0] * lead))
        ex = cx - 0.5
        top = self._head_top()
        ey = head_y - top                        # >0: head too high in picture -> tilt up
        if self.mode == "steer":
            # The picture is a little old: allow for camera moves it doesn't show yet.
            d_pan, d_tilt = self._unseen_motion(now)
            ex -= d_pan / hfov
            ey -= d_tilt / vfov
        if self.target[1] < 0.01:                # head cut off at top right now: tilt up a bit
            ey = max(ey, 0.06)

        # framing -> zoom direction (worked out first: a zoom changes how pan/tilt are planned)
        zdir, z_target = 0, None
        if self.framing in FRAMING_LABELS and self.zoom_pos is not None:
            label = FRAMING_LABELS[self.framing].lower()
            if self.framing == "full":
                # Long pull-out (2026-11-17): tight -> camera all the way out, slowly, staying
                # centered on the person. No body measurement needed: the target is zoom 0.
                z_target = 0
                if self.zoom_pos > ZOOM_TOLERANCE:
                    zdir = -1
                    self._ztxt = f"zoom out to {label}"
                elif self.zoom_dir == 0:
                    self._ztxt = f"{label} reached"
            elif self.body_deg is None:
                if self.zoom_pos > 0:
                    zdir = -1  # need to see the whole body once to measure them
                    self._ztxt = "zoom out to measure"
            else:
                want_vfov = self.body_deg / TUNE[f"{self.framing}_size"]
                # Stay well inside optical zoom: the zoom reading is a little behind, and on
                # 2026-11-13 a Tight zoom-in coasted on to 16960 (digital zoom, blurry).
                z_target = min(self.cam_zoom_max() - 800, zoom_for_magnification(VFOV_WIDE / want_vfov))
                ceiling = TUNE.values.get(f"{self.framing}_max_zoom") if hasattr(TUNE, "values") else None
                if ceiling:
                    z_target = min(z_target, int(ceiling))
                diff = z_target - self.zoom_pos
                if getattr(self, "zoom_out_only", False) and diff > 0:
                    diff = 0  # pull-out: already at least this wide, so stay
                if abs(diff) > (ZOOM_TOLERANCE if not self.zoom_dir else ZOOM_TOLERANCE / 2):
                    zdir = 1 if diff > 0 else -1
                    self._ztxt = f"zoom {'in' if zdir > 0 else 'out'} to {label}"
                elif self.zoom_dir == 0:
                    self._ztxt = f"{label} reached"
        if zdir > 0 and self.zoom_pos is not None and self.zoom_pos >= self.cam_zoom_max() - 800:
            zdir = 0  # hard stop: never creep toward digital zoom

        pan = tilt = 0
        if zdir:
            # ---- Coordinated move (2026-11-15: "zoom out a little, then step and center it
            # back up" - make it one fluid motion). While zooming, aim for where the person
            # should sit in the FINAL shot and spread the pan/tilt over the time the zoom
            # still needs, so pan, tilt and zoom glide together and finish together.
            rate = ZOOM_RATE.get(self.zoom_speed, 255 * (self.zoom_speed + 1) / 2)
            deg_x = ex * hfov                                          # already allows for unseen moves
            if z_target is None:
                # Zooming out only to measure them (or a lost-person ease-out): there's no final
                # shot to aim for, so just keep them centered in the current picture, head a bit
                # high. (2026-11-15: aiming for a full-wide head position here tilted it to the floor.)
                hfov_e, vfov_e, t_left = hfov, vfov, 2.0
                deg_y = (0.25 - top) * vfov - (head_y - top - ey) * vfov
            else:
                t_left = min(8.0, max(1.2, abs(z_target - self.zoom_pos) / rate))
                mag_end = magnification(z_target)
                hfov_e, vfov_e = HFOV_WIDE / mag_end, VFOV_WIDE / mag_end
                deg_y = ey * vfov + (head_y - 0.5) * (vfov_e - vfov)    # head where it belongs at the end
            # errors as a part of the final picture: small ones are left alone
            # One planned glide per axis (2026-11-15: reacting frame by frame during a zoom
            # still blinked pan on and off, because the slowest Sony speed is faster than the
            # small drift needs). At the start, work out the whole correction, pick the speed
            # that covers it within the zoom's remaining time, and run it for exactly that
            # long. Only re-plan if they move enough to need a new glide.
            pan = self._glide("x", deg_x, deg_x / hfov_e, t_left, now, TUNE["max_pan_speed"])
            tilt = self._glide("y", deg_y, deg_y / vfov_e, t_left, now, TUNE["max_tilt_speed"])
            self.moving_x, self.moving_y = pan != 0, tilt != 0
            return pan, tilt, zdir

        self._glides = {}
        # Hysteresis dead zones, made smoother (2026-11-13: Tight looked jerky, with 0.3-0.6 s
        # bursts about once a second). The zones grow when zoomed in, a move lasts at least
        # min_move, and after a stop it rests min_rest before starting again unless urgent.
        zfrac = min(1.0, max(0.0, np.log(mag) / np.log(OPTICAL_ZOOM)))
        grow = 1 + (TUNE["tight_deadzone"] - 1) * zfrac
        self.moving_x = self._axis("x", ex, now, TUNE["dead_start_x"] * grow, TUNE["dead_stop_x"] * grow, 0.30)
        # The tilt still-zone may grow, but never past most of the gap above the head, or a head
        # leaving the top of a tight shot would never trigger a tilt.
        # (only when the head is heading UP out of the shot; below the head there's lots of room,
        # and capping both ways made Left bob with a dancing singer on 2026-11-15)
        y_start = TUNE["dead_start_y"] * grow
        if ey > 0:
            y_start = min(y_start, 0.7 * head_y)
        y_stop = min(TUNE["dead_stop_y"] * grow, 0.4 * y_start)
        self.moving_y = self._axis("y", ey, now, y_start, y_stop, 0.25)
        if self.moving_x:
            v = abs(ex) * hfov / TUNE["catch_up_x"]
            pan = int(np.sign(ex)) * deg_per_sec_to_speed(v, TUNE["max_pan_speed"])
        if self.moving_y:
            v = abs(ey) * vfov / TUNE["catch_up_y"]
            tilt = int(np.sign(ey)) * deg_per_sec_to_speed(v, TUNE["max_tilt_speed"])
        return pan, tilt, zdir

    def _unseen_motion(self, now: float) -> tuple[float, float]:
        """Degrees panned/tilted in the last `video_delay` seconds (from what we commanded)."""
        delay = TUNE["video_delay"]
        if delay <= 0:
            return 0.0, 0.0
        start = now - delay
        while len(self.cmd_hist) > 1 and self.cmd_hist[1][0] <= start:
            self.cmd_hist.popleft()
        dp = dt_ = 0.0
        items = list(self.cmd_hist)
        for i, (t, vp, vt) in enumerate(items):
            t_end = items[i + 1][0] if i + 1 < len(items) else now
            span = max(0.0, t_end - max(t, start))
            dp += vp * span
            dt_ += vt * span
        return dp, dt_

    def cam_zoom_max(self) -> int:
        """Furthest the tracker may zoom in, plus the 800 safety margin the callers subtract.
        Capped by the 'closest_zoom' tuning (2026-11-16: never closer than head to mid-chest)."""
        cam_max = getattr(self.cam, "zoom_max", ZOOM_OPTICAL_MAX)
        closest = TUNE.values.get("closest_zoom") if hasattr(TUNE, "values") else None
        return min(cam_max, int(closest) + 800) if closest else cam_max

    def _apply(self, pan: int, tilt: int, zdir: int) -> None:
        """Send only what changed, so the camera gets a calm stream of commands."""
        if (pan, tilt) != self.sent_pt:
            self.cam.pan_tilt(pan, tilt)
            self.sent_pt = (pan, tilt)
            self.cmd_hist.append((self._now, speed_to_deg_per_sec(pan), speed_to_deg_per_sec(tilt)))
        if zdir != self.zoom_dir:
            time.sleep(0.04)
            if zdir == 0:
                self.cam.zoom(0)
            else:
                self.cam.zoom_drive(zdir, self.zoom_speed)
            self.zoom_dir = zdir

    # ---- outputs ----
    def state(self) -> dict:
        with self._lock:
            return {
                "camera": self.cam_id, "mode": self.mode, "status": self.status,
                "people": self.people, "target": self.target,
                "following_for": round(time.time() - self.locked_since, 1) if self.target else 0,
                "would": self.would, "would_pan": self.would_pan, "would_tilt": self.would_tilt,
                "framing": self.framing, "zoom": self.zoom_pos,
                "body_measured": self.body_deg is not None, "shadows_ignored": self.dropped,
                "edges": self.edges, "at_edge": self.at_edge, "pan_tilt": self.pt_pos,
                "pullout": bool(getattr(self, "zoom_out_only", False)) and self.framing == "wide",
                "longpull": self.framing == "full",
                "recording": bool(self.log and self.log.video_on),
                "fps": round(self.fps, 1), "detect_ms": round(self.detect_ms, 1),
            }

    def view_image(self, width: int = 832):
        """Latest frame with boxes drawn: grey = people seen, green = who it's following."""
        from PIL import Image, ImageDraw
        with self._lock:
            if self.frame is None:
                return None
            img = Image.fromarray(self.frame[:CONTENT_H]).resize((width, width * 9 // 16))
            people, target, would, mode = list(self.people), self.target, self.would, self.mode
            head = self._head_top() if target else None
        W, H = img.size
        d = ImageDraw.Draw(img)
        dx = TUNE["dead_start_x"]
        d.rectangle([W * (0.5 - dx), 0, W * (0.5 + dx), H], outline=(90, 90, 90))
        for p in people:
            x1, y1, x2, y2 = p["box"]
            col = (200, 90, 200) if p.get("shadow") else (170, 170, 170)
            d.rectangle([x1 * W, y1 * H, x2 * W, y2 * H], outline=col, width=2)
            d.text((x1 * W + 4, y2 * H - 14), f"{p['score']:.2f}" + (" shadow?" if p.get("shadow") else ""), fill=col)
        if target:
            x1, y1, x2, y2 = target
            d.rectangle([x1 * W, y1 * H, x2 * W, y2 * H], outline=(40, 220, 90), width=4)
            d.text((x1 * W + 6, y1 * H + 4), "FOLLOWING", fill=(40, 220, 90))
            hy = head * H
            d.line([x1 * W - 10, hy, x2 * W + 10, hy], fill=(40, 220, 90), width=1)
            want = FRAMING_HEAD_Y.get(self.framing or "", TUNE["head_y"]) * H
            d.line([W * 0.5 - 30, want, W * 0.5 + 30, want], fill=(255, 210, 80), width=1)
        d.rectangle([0, H - 22, W, H], fill=(0, 0, 0))
        label = "STEERING" if mode == "steer" else "Watch-only: would"
        d.text((8, H - 18), f"{label}: {would}", fill=(255, 210, 80) if mode == "steer" else (255, 255, 255))
        return img

    def view_jpeg(self, width: int = 832) -> bytes | None:
        img = self.view_image(width)
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=75)
        return buf.getvalue()


class TrackerManager:
    def __init__(self):
        self.trackers: dict[str, Tracker] = {}

    def get(self, cam_id: str, rtsp_url: str, ip: str) -> Tracker:
        if cam_id not in self.trackers:
            t = Tracker(cam_id, rtsp_url, ip)
            from . import db
            t.edges = db.stage_edges(cam_id)
            self.trackers[cam_id] = t
        return self.trackers[cam_id]
