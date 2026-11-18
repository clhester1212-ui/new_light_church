"""Tracking settings that can be changed live from the dashboard (no restart).

Each setting has a default, a range, and a plain-English description. Changes are saved
to data/tuning.json, so they survive a restart. "Reset" goes back to these defaults.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "tuning.json"

# key: (default, min, max, step, group, label, help)
SPEC: dict[str, tuple] = {
    # ---- who to follow ----
    "min_start_score": (0.60, 0.3, 0.9, 0.05, "Choosing someone", "Sureness to start following",
                        "How sure the detector must be before it starts following someone. Higher = fewer false starts on shadows or lights."),
    "confirm_frames": (5, 2, 8, 1, "Choosing someone", "Frames to confirm (of 8)",
                       "How many of the last 8 frames must show the same person before it locks on."),
    "lost_after": (2.5, 0.5, 6.0, 0.5, "Choosing someone", "Give up after (seconds)",
                   "How long it keeps waiting for a person it lost before picking the center person again."),
    "min_start_height": (0.12, 0.05, 0.4, 0.01, "Choosing someone", "Smallest person to start on",
                         "Ignore people smaller than this part of the picture height when choosing (far away, reflections)."),
    "min_rel_height": (0.5, 0.2, 0.9, 0.05, "Choosing someone", "Smallest vs. tallest person",
                       "When choosing, skip anything shorter than this part of the tallest person in the shot (stage lights, mic stands, monitors). A stage light fooled it in testing."),
    "coast": (0.5, 0.0, 1.5, 0.1, "Choosing someone", "Keep moving through blips (seconds)",
              "If the person is missed for a moment, keep easing along instead of stopping dead, for up to this long."),
    "prefer_front": (1.5, 0.0, 4.0, 0.1, "Choosing someone", "Prefer the front of the stage",
                     "How strongly to pick the person nearest the front (feet lowest in the picture) over band members behind. 0 = only nearness to center counts."),
    "shadow_filter":(1, 0, 1, 1, "Choosing someone", "Ignore screen shadows (1 = on)",
                      "When choosing whom to follow, skip a figure right beside someone whose feet are lower (their shadow on the screen, or a person farther back)."),
    # ---- left / right ----
    "dead_start_x": (0.10, 0.03, 0.25, 0.01, "Left / right (pan)", "Start moving when off center by",
                     "Part of the picture width the person can drift from center before the camera follows."),
    "dead_stop_x": (0.04, 0.01, 0.15, 0.01, "Left / right (pan)", "Stop moving when within",
                    "Once moving, the camera keeps going until the person is this close to center."),
    "catch_up_x": (1.3, 0.6, 5.0, 0.1, "Left / right (pan)", "Catch-up time (seconds)",
                   "Bigger = gentler. Roughly how long it takes to bring the person back to center."),
    "max_pan_speed": (9, 2, 18, 1, "Left / right (pan)", "Top pan speed (Sony 1-24)",
                      "Fastest it will ever pan while tracking."),
    "lead": (0.3, 0.0, 0.8, 0.05, "Left / right (pan)", "Lead walkers by",
             "How far ahead of a walking person to aim, so the shot doesn't trail behind them. 0 = no lead."),
    "speed_step": (3, 1, 24, 1, "Left / right (pan)", "Speed change per frame",
                   "Largest change in pan/tilt speed from one frame to the next. Smaller = smoother starts and stops."),
    # ---- up / down ----
    "dead_start_y": (0.12, 0.03, 0.30, 0.01, "Up / down (tilt)", "Start tilting when head is off by",
                     "Part of the picture height the head can move before the camera tilts."),
    "dead_stop_y": (0.05, 0.01, 0.15, 0.01, "Up / down (tilt)", "Stop tilting when within",
                    "Once tilting, it keeps going until the head is this close to where it should be."),
    "catch_up_y": (3.0, 0.6, 8.0, 0.1, "Up / down (tilt)", "Catch-up time (seconds)",
                   "Bigger = gentler tilting. It was too strong in the first test, so this starts gentler than pan."),
    "max_tilt_speed": (4, 1, 12, 1, "Up / down (tilt)", "Top tilt speed (Sony 1-23)",
                       "Fastest it will ever tilt while tracking."),
    "tilt_smooth": (1.0, 0.0, 3.0, 0.1, "Up / down (tilt)", "Head smoothing (seconds)",
                    "Uses the middle value of the head position over this long, so raised arms, bows and mic stands don't make it bob."),
    "head_y": (0.15, 0.05, 0.35, 0.01, "Up / down (tilt)", "Head height (no framing chosen)",
               "Where the top of the head sits, as part of the picture height from the top, when no framing is picked."),
    # ---- smoothness (added 2026-11-13 after the first live test looked jerky in Tight) ----
    "min_move": (1.0, 0.0, 3.0, 0.1, "Smoothness", "Shortest move (seconds)",
                 "Once it starts panning or tilting, it keeps gliding at least this long (unless it would pass the person). Stops quick start-stop jerks."),
    "min_rest": (1.2, 0.0, 4.0, 0.1, "Smoothness", "Rest between moves (seconds)",
                 "After it stops, it waits this long before starting again, unless the person is near the edge of the shot."),
    "tight_deadzone": (1.8, 1.0, 3.0, 0.1, "Smoothness", "Bigger still-zone when zoomed in",
                       "When zoomed in a lot, small moves look big. The start/stop zones grow up to this many times when fully zoomed in, so it moves less often."),
    "tilt_while_zooming": (0, 0, 1, 1, "Smoothness", "Tilt while zooming (1 = yes)",
                           "Zooming makes the head appear to move. 0 = don't tilt during a zoom unless the head is about to leave the shot."),
    # ---- timing ----
    "video_delay": (0.5, 0.0, 1.5, 0.05, "Timing", "Video delay to allow for (seconds)",
                    "The camera's network video arrives a little late. The tracker allows for the moves it already made that the video doesn't show yet, so it doesn't overshoot. Use 'Measure video delay' to find the real number."),
    # ---- framing ----
    # 2026-11-16: the user wants nothing closer than "a half-chest headshot" - never just a face.
    # Tight used to be 3.4 (head and shoulders) and reached zoom 15600 on 11-15.
    "tight_size": (2.4, 1.8, 3.0, 0.1, "Framing", "Tight: zoom (body heights)",
                   "Bigger = tighter. 2.4 shows about head to mid-chest - the closest the church wants."),
    "closest_zoom": (14000, 9000, 15500, 100, "Framing", "Never zoom past (any button)",
                     "Hard limit for every framing, Tight included, so a short-measuring person can never become a face close-up. On 2026-11-16 the user looked at 11 shots from 11-15: everything up to 14500 was fine, 15550+ was too close. 14000 leaves room for the zoom coasting about 500 past where it is stopped."),
    "tight_max_zoom": (14000, 9000, 15500, 100, "Framing", "Tight: never zoom past",
                       "Camera zoom number Tight never goes beyond (also held by 'Never zoom past')."),
    "medium_max_zoom": (13000, 6000, 15500, 100, "Framing", "Medium: never zoom past",
                        "Camera zoom number Medium never goes beyond. People behind keyboards, guitars and monitors look shorter than they are, so without this Medium can end up a head close-up. 13000 looked waist-up from Left and Right on 2026-11-15."),
    "wide_max_zoom": (10500, 4000, 15000, 100, "Framing", "Wide: never zoom past",
                      "Camera zoom number Wide (whole body) never goes beyond, for the same reason."),
    "long_pull_speed": (3, 1, 5, 1, "Framing", "Long pull-out: zoom speed",
                        "How fast Long pull-out zooms from a tight shot all the way out. 3 takes about 20-25 s from Tight (measured on Left 2026-11-17); 4 about 12-15 s; 2 about 35 s."),
    "medium_size": (1.8, 1.2, 3.0, 0.1, "Framing", "Medium: zoom (body heights)",
                    "Bigger = tighter. 1.8 shows about waist up."),
    "wide_size": (0.8, 0.4, 1.2, 0.05, "Framing", "Wide: zoom (body heights)",
                  "Bigger = tighter. 0.8 shows the full body with some room."),
}


class Tuning:
    def __init__(self, path: Path | None = PATH):
        self.path = path
        self._lock = threading.Lock()
        self.values = {k: v[0] for k, v in SPEC.items()}
        if path and path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for k, v in saved.items():
                    if k in SPEC:
                        self.values[k] = self._clean(k, v)
            except (OSError, ValueError):
                pass

    def __getitem__(self, key: str):
        return self.values[key]

    @staticmethod
    def _clean(key: str, value):
        default, lo, hi, step = SPEC[key][:4]
        v = max(lo, min(hi, float(value)))
        return int(round(v)) if isinstance(default, int) else round(v, 3)

    def update(self, changes: dict) -> dict:
        with self._lock:
            for k, v in changes.items():
                if k in SPEC:
                    self.values[k] = self._clean(k, v)
            self._save()
            return dict(self.values)

    def reset(self) -> dict:
        with self._lock:
            self.values = {k: v[0] for k, v in SPEC.items()}
            self._save()
            return dict(self.values)

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        changed = {k: v for k, v in self.values.items() if v != SPEC[k][0]}
        self.path.write_text(json.dumps(changed, indent=1), encoding="utf-8")

    def describe(self) -> list[dict]:
        return [{"key": k, "value": self.values[k], "default": s[0], "min": s[1], "max": s[2], "step": s[3],
                 "group": s[4], "label": s[5], "help": s[6]} for k, s in SPEC.items()]


TUNE = Tuning()
