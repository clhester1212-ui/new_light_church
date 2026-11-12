"""Finds people in a frame with YOLOX-nano (Apache-2.0), running offline on the CPU.

Model file: models/yolox_nano.onnx, from the YOLOX project's 0.1.1rc0 release on GitHub
(downloaded 2026-11-11, sha256 c789161e...a0b7d).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

from .video import CONTENT_H, SIZE

MODEL = Path(__file__).resolve().parent.parent / "models" / "yolox_nano.onnx"
PERSON = 0
# Tested on live frames 2026-11-11: this model wants BGR, 0-255, no mean/std
# (ImageNet normalization found nobody; BGR scored people higher than RGB).


def _grids() -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for s in (8, 16, 32):
        n = SIZE // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float = 0.45) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]]); yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]]); yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[1:][iou < thr]
    return keep


class PersonDetector:
    def __init__(self, threads: int = 2):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads  # leave cores free for Companion, ffmpeg, the other camera
        self.session = ort.InferenceSession(str(MODEL), opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.grid, self.stride = _grids()

    def detect(self, rgb: np.ndarray, min_score: float = 0.35) -> list[dict]:
        """rgb: 416x416x3 uint8 RGB. Returns people as boxes in 0..1 picture coordinates."""
        x = rgb[:, :, ::-1].astype(np.float32)  # RGB -> BGR
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        out = self.session.run(None, {self.input: x})[0][0]
        xy = (out[:, :2] + self.grid) * self.stride
        wh = np.exp(out[:, 2:4]) * self.stride
        score = out[:, 4] * out[:, 5 + PERSON]
        m = score > min_score
        if not m.any():
            return []
        xy, wh, score = xy[m], wh[m], score[m]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1)
        people = []
        for i in _nms(boxes, score):
            x1, y1, x2, y2 = boxes[i]
            # back to 0..1 of the real picture (top CONTENT_H rows of the padded frame)
            x1, x2 = max(0.0, x1 / SIZE), min(1.0, x2 / SIZE)
            y1, y2 = max(0.0, y1 / CONTENT_H), min(1.0, y2 / CONTENT_H)
            if x2 - x1 < 0.01 or y2 - y1 < 0.02:
                continue
            people.append({"box": [round(float(v), 4) for v in (x1, y1, x2, y2)], "score": round(float(score[i]), 3)})
        return people


def shadow_like(p: dict, people: list[dict]) -> bool:
    """True if box p looks like the shadow (or a farther-back copy) of another person.

    A shadow on the projection screen sits right beside or behind the real person, about
    the same size, but its "feet" stop higher up (the screen's bottom edge is above the
    stage floor). The real person, whose feet are lower, is the one we want to choose.
    Used only when CHOOSING whom to follow, never to drop someone already followed.
    """
    x1, y1, x2, y2 = p["box"]
    h, w, cx = y2 - y1, x2 - x1, (x1 + x2) / 2
    for q in people:
        if q is p:
            continue
        a1, b1, a2, b2 = q["box"]
        qh, qw, qcx = b2 - b1, a2 - a1, (a1 + a2) / 2
        side_by_side = abs(cx - qcx) < 0.75 * (w + qw)
        feet_higher = b2 - y2 > 0.15 * qh
        similar = 0.6 * h <= qh <= 1.8 * h
        if side_by_side and feet_higher and similar and q["score"] >= p["score"] - 0.1:
            return True
    return False
