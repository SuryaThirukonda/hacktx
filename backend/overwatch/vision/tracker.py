"""ByteTrack-style multi-object tracker with camera-motion compensation and explicit lifecycle.

States: TENTATIVE -> CONFIRMED -> LOST -> EXPIRED. Track IDs carry a tracker epoch (``drone:17:e2``) so a restart
can never be confused with a continuation. A LOST track that re-associates is flagged ``reacquired`` — the
tracker's association window, not an identity claim.
"""

from __future__ import annotations

import itertools
from collections import Counter, deque
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config import TrackerCfg
from .cmc import local_linear, warp_point
from .detectors import Detection


class KalmanBox:
    """Constant-velocity Kalman filter on [cx, cy, w, h, vcx, vcy, vw, vh] with time-aware steps (seconds)."""

    def __init__(self, box: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = box
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
        self.x = np.array([(x0 + x1) / 2, (y0 + y1) / 2, w, h, 0, 0, 0, 0], float)
        s = max(h, 8.0)
        self.P = np.diag([(s / 10) ** 2, (s / 10) ** 2, (s / 10) ** 2, (s / 10) ** 2,
                          (s * 2.0) ** 2, (s * 2.0) ** 2, (s / 2) ** 2, (s / 2) ** 2])

    def predict(self, dt: float) -> None:
        F = np.eye(8)
        F[:4, 4:] = np.eye(4) * dt
        s = max(self.x[3], 8.0)
        q_pos = (s * 0.05) ** 2 * dt * 15
        q_vel = (s * 1.6) ** 2 * dt
        Q = np.diag([q_pos, q_pos, q_pos * 0.5, q_pos * 0.5, q_vel, q_vel, q_vel * 0.1, q_vel * 0.1])
        self.x = F @ self.x
        self.x[2] = max(self.x[2], 1.0)
        self.x[3] = max(self.x[3], 1.0)
        self.P = F @ self.P @ F.T + Q

    def update(self, box: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = box
        z = np.array([(x0 + x1) / 2, (y0 + y1) / 2, max(1.0, x1 - x0), max(1.0, y1 - y0)])
        Hm = np.zeros((4, 8))
        Hm[:4, :4] = np.eye(4)
        s = max(z[3], 8.0)
        R = np.diag([(s / 14) ** 2, (s / 14) ** 2, (s / 8) ** 2, (s / 8) ** 2])
        y = z - Hm @ self.x
        S = Hm @ self.P @ Hm.T + R
        K = self.P @ Hm.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(8) - K @ Hm) @ self.P

    def apply_homography(self, H: np.ndarray) -> None:
        cx, cy = self.x[0], self.x[1]
        nx, ny = warp_point(H, cx, cy)
        J = local_linear(H, cx, cy)
        scale = float(np.sqrt(abs(np.linalg.det(J))))
        self.x[0], self.x[1] = nx, ny
        self.x[2] *= scale
        self.x[3] *= scale
        self.x[4:6] = J @ self.x[4:6]
        T = np.eye(8)
        T[:2, :2] = J
        T[4:6, 4:6] = J
        self.P = T @ self.P @ T.T

    @property
    def box(self) -> tuple[float, float, float, float]:
        cx, cy, w, h = self.x[:4]
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    @property
    def velocity(self) -> tuple[float, float]:
        return float(self.x[4]), float(self.x[5])


@dataclass
class Track:
    n: int
    epoch: int
    source: str
    kf: KalmanBox
    first_ms: float
    last_ms: float
    class_votes: Counter = field(default_factory=Counter)
    state: str = "TENTATIVE"
    hits: int = 1
    conf: float = 0.0
    measured_box: tuple[float, float, float, float] | None = None
    matched_now: bool = True
    reacquired: bool = False
    lost_since_ms: float | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=90))  # (t_ms, box)
    appearance: np.ndarray | None = None
    appearance_n: int = 0

    @property
    def track_id(self) -> str:
        return f"{self.source}:{self.n}:e{self.epoch}"

    @property
    def class_name(self) -> str:
        return self.class_votes.most_common(1)[0][0] if self.class_votes else "object"

    @property
    def box(self) -> tuple[float, float, float, float]:
        return self.kf.box

    @property
    def visibility(self) -> str:
        if self.state == "LOST":
            return "lost"
        return "visible" if self.matched_now else "occluded"


@dataclass
class TrackerOutput:
    tracks: list[Track]
    new: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    reacquired: list[str] = field(default_factory=list)
    expired: list[Track] = field(default_factory=list)
    matches: dict[str, int] = field(default_factory=dict)


def iou_matrix(a: np.ndarray, b: np.ndarray, buf_a: np.ndarray | float = 0.0, buf_b: float = 0.0,
               symmetric: bool = False) -> np.ndarray:
    """IoU between boxes (N,4) and (M,4); boxes optionally expanded by a fraction of their size (buffered IoU).

    ``symmetric=True`` expands each detection by the same buffer as the track it is compared with (C-BIoU style).
    Expanding only the track box shrinks the IoU of a perfect overlap as the buffer grows (a lost track 0.4 s old
    scored ~0.2 against a detection exactly on it), which split one person into a new track after every brief
    occlusion.
    """
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    a = a.astype(float)
    b = b.astype(float)
    ba = np.broadcast_to(np.asarray(buf_a, float), (len(a),))[:, None]  # (N,1)
    bb = ba if symmetric else np.full((1, 1), float(buf_b))  # (N,1) or (1,1)
    wa, ha = (a[:, 2] - a[:, 0])[:, None], (a[:, 3] - a[:, 1])[:, None]
    wb, hb = (b[:, 2] - b[:, 0])[None, :], (b[:, 3] - b[:, 1])[None, :]
    ax0, ax1 = a[:, None, 0] - wa * ba, a[:, None, 2] + wa * ba
    ay0, ay1 = a[:, None, 1] - ha * ba, a[:, None, 3] + ha * ba
    bx0, bx1 = b[None, :, 0] - wb * bb, b[None, :, 2] + wb * bb
    by0, by1 = b[None, :, 1] - hb * bb, b[None, :, 3] + hb * bb
    inter = np.clip(np.minimum(ax1, bx1) - np.maximum(ax0, bx0), 0, None) * \
        np.clip(np.minimum(ay1, by1) - np.maximum(ay0, by0), 0, None)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return inter / np.maximum(area_a + area_b - inter, 1e-9)


def _is_fragment(box: tuple[float, float, float, float], others: list[tuple[float, float, float, float]]) -> bool:
    x0, y0, x1, y1 = box
    area = max(1e-6, (x1 - x0) * (y1 - y0))
    for a0, b0, a1, b1 in others:
        w, h = a1 - a0, b1 - b0
        a0, b0, a1, b1 = a0 - 0.2 * w, b0 - 0.25 * h, a1 + 0.2 * w, b1 + 0.25 * h
        inter = max(0.0, min(x1, a1) - max(x0, a0)) * max(0.0, min(y1, b1) - max(y0, b0))
        if inter / area > 0.45:
            return True
    return False


def _assign(cost: np.ndarray, max_cost: float) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    if cost.size == 0:
        return [], set(range(cost.shape[0])), set(range(cost.shape[1]))
    r, c = linear_sum_assignment(cost)
    matches = [(i, j) for i, j in zip(r, c) if cost[i, j] <= max_cost]
    mi = {i for i, _ in matches}
    mj = {j for _, j in matches}
    return matches, set(range(cost.shape[0])) - mi, set(range(cost.shape[1])) - mj


class ByteTracker:
    def __init__(self, cfg: TrackerCfg, source: str = "drone", epoch: int = 1) -> None:
        self.cfg = cfg
        self.source = source
        self.epoch = epoch
        self.tracks: list[Track] = []
        self.last_ms: float | None = None
        self._ids = itertools.count(1)  # per tracker: identical inputs -> identical IDs (replay determinism)

    def reset(self) -> None:
        self.epoch += 1  # IDs carry the epoch, so restarting the numbering can never alias an old track
        self.tracks = []
        self.last_ms = None
        self._ids = itertools.count(1)

    def update(self, dets: list[Detection], t_ms: float, H_cmc: np.ndarray | None = None) -> TrackerOutput:
        cfg = self.cfg
        dt = 1 / 15 if self.last_ms is None else max(1e-3, (t_ms - self.last_ms) / 1000.0)
        self.last_ms = t_ms
        out = TrackerOutput(tracks=[])
        for tr in self.tracks:
            tr.kf.predict(dt)
            if H_cmc is not None:
                tr.kf.apply_homography(H_cmc)
            tr.matched_now = False
            tr.reacquired = False

        boxes = np.array([d.box for d in dets], float).reshape(-1, 4)
        confs = np.array([d.confidence for d in dets], float)
        high = [i for i in range(len(dets)) if confs[i] >= cfg.high_thresh]
        low = [i for i in range(len(dets)) if cfg.low_thresh <= confs[i] < cfg.high_thresh]
        pool = [t for t in self.tracks if t.state in ("CONFIRMED", "LOST")]
        tentative = [t for t in self.tracks if t.state == "TENTATIVE"]

        def match(tracks: list[Track], det_idx: list[int], buf_base: float, min_iou: float):
            if not tracks or not det_idx:
                return [], list(range(len(tracks))), list(det_idx)
            tb = np.array([t.box for t in tracks])
            gap_s = np.array([(t_ms - t.last_ms) / 1000.0 for t in tracks])
            buf = buf_base + np.clip(gap_s * 1.5, 0, 1.2)
            iou = iou_matrix(tb, boxes[det_idx], buf, symmetric=True)
            cost = 1.0 - iou
            m, ut, ud = _assign(cost, 1.0 - min_iou)
            return ([(tracks[i], det_idx[j]) for i, j in m], sorted(ut), [det_idx[j] for j in sorted(ud)])

        m1, ut1, ud_high = match(pool, high, 0.35, cfg.match_iou)
        remaining_pool = [pool[i] for i in ut1 if pool[i].state == "CONFIRMED"]
        m2, _, _ = match(remaining_pool, low, 0.15, 0.3)
        m3, ut3, ud_high2 = match(tentative, ud_high, 0.35, cfg.match_iou)

        for tr, j in m1 + m2 + m3:
            d = dets[j]
            tr.kf.update(d.box)
            if tr.state == "LOST":
                tr.reacquired = True
                tr.lost_since_ms = None
                tr.state = "CONFIRMED"
                out.reacquired.append(tr.track_id)
            tr.hits += 1
            tr.last_ms = t_ms
            tr.matched_now = True
            tr.measured_box = d.box
            tr.conf = 0.7 * tr.conf + 0.3 * d.confidence
            tr.class_votes[d.class_name] += 1
            tr.history.append((t_ms, d.box))
            out.matches[tr.track_id] = j
            if tr.state == "TENTATIVE" and tr.hits >= cfg.n_init:
                tr.state = "CONFIRMED"
                out.confirmed.append(tr.track_id)

        dead: list[Track] = [tentative[i] for i in ut3]
        matched_boxes = [t.box for t in self.tracks if t.matched_now and t.state == "CONFIRMED"]
        for j in ud_high2:
            d = dets[j]
            if _is_fragment(d.box, matched_boxes):
                continue  # a piece of a person already tracked this frame: do not spawn a competing track
            tr = Track(next(self._ids), self.epoch, self.source, KalmanBox(d.box), t_ms, t_ms, conf=d.confidence,
                       measured_box=d.box)
            tr.class_votes[d.class_name] += 1
            tr.history.append((t_ms, d.box))
            if cfg.n_init <= 1:
                tr.state = "CONFIRMED"
                out.confirmed.append(tr.track_id)
            self.tracks.append(tr)
            out.new.append(tr.track_id)
            out.matches[tr.track_id] = j

        for tr in self.tracks:
            if tr.matched_now or tr in dead:
                continue
            gap = t_ms - tr.last_ms
            if tr.state == "CONFIRMED" and gap > cfg.lost_after_ms:
                tr.state = "LOST"
                tr.lost_since_ms = tr.last_ms
                out.lost.append(tr.track_id)
            elif tr.state == "LOST" and gap > cfg.expire_after_ms:
                tr.state = "EXPIRED"
                out.expired.append(tr)
        self.tracks = [t for t in self.tracks if t not in dead and t.state != "EXPIRED"]
        out.tracks = list(self.tracks)
        return out
