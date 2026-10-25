"""Human-selected locks on tracks: the *subject* and the *responder* (Overwatch §5.3 lock layer).

* A human selects the track; the system never auto-targets.
* While the locked track is visible the lock follows it and grows a session-scoped colour-histogram gallery.
* When it disappears: LOCKED -> COAST (lost > 0.7 s, TRACK_LOST) -> REACQUIRE (> 2 s) -> HOLD (> 20 s).
* Rebinding to a *new* track requires distance gating around the Kalman prediction AND gallery similarity with a
  margin over the runner-up. Otherwise the lock goes UNCERTAIN and asks for confirmation (decision D2 or operator).
  Rebinds are labelled unverified until confirmed. Never switch identity when unsure.
"""

from __future__ import annotations

import copy
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import LocksCfg
from ..geometry.kalman import CAFilter
from ..schemas import LockCandidate, LockState
from ..vision.appearance import similarity


@dataclass
class TrackInfo:
    track_id: str
    state: str
    matched_now: bool
    box: tuple[float, float, float, float]
    conf: float
    world_xy: tuple[float, float] | None = None
    world_err: float | None = None
    hist: np.ndarray | None = None
    hits: int = 0
    tracker_reacquired: bool = False
    velocity: tuple[float, float] | None = None  # ground velocity once the track has enough updates


@dataclass
class LockEvent:
    kind: str
    role: str
    track_id: str | None
    text: str
    data: dict[str, Any] = field(default_factory=dict)


class Lock:
    def __init__(self, role: str, cfg: LocksCfg) -> None:
        self.role = role
        self.cfg = cfg
        # the responder's own track flickers more (vest, dense surroundings); only report sustained loss
        self.report_after_ms = cfg.coast_after_ms if role == "subject" else 2000.0
        self._loss_reported = True
        self.reset()

    def reset(self) -> None:
        self.state = "UNLOCKED"
        self.track_id: str | None = None
        self.gallery: deque[np.ndarray] = deque(maxlen=self.cfg.gallery_size)
        self.kf: CAFilter | None = None
        self.kf_err = 0.5
        self.since_ms: float | None = None
        self.last_seen_ms: float | None = None
        self.last_seen_xy: tuple[float, float] | None = None
        self.rebinds = 0
        self.verified = True
        self.candidates: list[LockCandidate] = []
        self.needs_confirm = False
        self.description: str | None = None
        self.locked_by = "operator"
        self.history: deque[tuple[float, float, float]] = deque(maxlen=600)
        self.last_neighbor_ms = -1e12  # last time another person was within 1.5 m (crossing / merge risk)
        self.settle_from_ms: float | None = None  # kinematics are only claimed once the bound track has settled
        self.rejected: set[str] = set()  # tracks a person said are not this subject: never re-bound to again
        self._pre_rebind: dict[str, Any] | None = None  # the lock as it was before an unverified re-binding

    # ---------------------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state != "UNLOCKED"

    def engage(self, tr: TrackInfo, now_ms: float, locked_by: str = "operator", description: str | None = None) -> list[LockEvent]:
        prev = self.track_id
        self.reset()
        self.state = "LOCKED"
        self.track_id = tr.track_id
        self.since_ms = now_ms
        self.last_seen_ms = now_ms
        self.locked_by = locked_by
        self.description = description
        self.settle_from_ms = now_ms
        if tr.hist is not None:
            self.gallery.append(tr.hist)
        if tr.world_xy is not None:
            self.kf = CAFilter(tr.world_xy, now_ms, max(0.15, tr.world_err or 0.5))
            self.last_seen_xy = tr.world_xy
        who = "Subject" if self.role == "subject" else "Responder"
        return [LockEvent("LOCK_ENGAGED", self.role, tr.track_id, f"{who} locked on track {short(tr.track_id)} by {locked_by}",
                          {"previous": prev, "locked_by": locked_by})]

    def reject_rebind(self, now_ms: float, by: str = "operator") -> list[LockEvent]:
        """A person says the unverified re-binding is the wrong person: back to searching from where the subject was
        last verified, and that track is never re-bound to again."""
        if not (self.active and self.state == "LOCKED" and not self.verified and self._pre_rebind is not None):
            return []
        wrong = self.track_id
        pre = self._pre_rebind
        self._pre_rebind = None
        if wrong:
            self.rejected.add(wrong)
        self.track_id = pre["track_id"]
        self.kf, self.kf_err = pre["kf"], pre["kf_err"]
        self.last_seen_ms, self.last_seen_xy = pre["last_seen_ms"], pre["last_seen_xy"]
        self.verified = True
        self.state = "REACQUIRE"
        self.candidates = []
        self.needs_confirm = False
        return [LockEvent("REBIND_REJECTED", self.role, wrong, f"{self.role.title()} re-binding to track {short(wrong)} "
                          f"rejected by {by}: searching again", {"rejected": wrong, "by": by})]

    def release(self, now_ms: float, reason: str = "operator") -> list[LockEvent]:
        if not self.active:
            return []
        tid = self.track_id
        self.reset()
        return [LockEvent("LOCK_RELEASED", self.role, tid, f"{self.role.title()} lock released ({reason})", {"reason": reason})]

    def _rebind_filter(self, tr: TrackInfo, now_ms: float) -> None:
        """A new track for the same person can sit metres from the old prediction; feeding that jump into the
        filter would read as a fast approach. Re-seed the filter at the new position (keeping a decayed velocity)
        and restart the settle window so no kinematic claim is made until the new track has history."""
        self.settle_from_ms = now_ms
        if tr.world_xy is None:
            return
        if self.kf is None:
            self.kf = CAFilter(tr.world_xy, now_ms, max(0.15, tr.world_err or 0.5))
            return
        self.kf.predict(now_ms)
        px, py = self.kf.position
        if math.hypot(tr.world_xy[0] - px, tr.world_xy[1] - py) > 1.0:
            vx, vy = self.kf.velocity
            fresh = CAFilter(tr.world_xy, now_ms, max(0.15, tr.world_err or 0.5))
            fresh.x[2], fresh.x[3] = 0.5 * vx, 0.5 * vy
            self.kf = fresh

    def settled(self, now_ms: float, min_ms: float = 900.0) -> bool:
        return self.settle_from_ms is not None and now_ms - self.settle_from_ms >= min_ms and \
            (self.kf is not None and self.kf.updates >= 8)

    def gallery_similarity(self, hist: np.ndarray | None) -> float:
        if hist is None or not self.gallery:
            return 0.0
        sims = sorted((similarity(hist, g) for g in self.gallery), reverse=True)
        return float(np.mean(sims[:3]))

    def confirm_candidate(self, tr: TrackInfo, now_ms: float, source: str, confidence: float | None = None) -> list[LockEvent]:
        self.track_id = tr.track_id
        self.state = "LOCKED"
        self.verified = True
        self.needs_confirm = False
        self.candidates = []
        self.last_seen_ms = now_ms
        self.rebinds += 1
        self._rebind_filter(tr, now_ms)
        if tr.world_xy is not None:
            self.last_seen_xy = tr.world_xy
        conf_txt = f" ({confidence:.2f})" if confidence is not None else ""
        return [LockEvent("REACQUIRED", self.role, tr.track_id,
                          f"{self.role.title()} reacquired on track {short(tr.track_id)}, confirmed by {source}{conf_txt}",
                          {"method": source, "verified": True, "confidence": confidence})]

    # ---------------------------------------------------------------------------------------------------------
    def update(self, tracks: dict[str, TrackInfo], exclude: set[str], now_ms: float) -> list[LockEvent]:
        if not self.active:
            return []
        cfg = self.cfg
        ev: list[LockEvent] = []
        tr = tracks.get(self.track_id) if self.track_id else None
        if tr is not None and tr.matched_now and tr.state != "LOST":
            if self.state in ("COAST", "REACQUIRE", "UNCERTAIN", "HOLD"):
                sim = self.gallery_similarity(tr.hist)
                verified = sim >= cfg.rebind_similarity or not self.gallery
                self.verified = verified
                ev.append(LockEvent("REACQUIRED", self.role, tr.track_id,
                                    f"{self.role.title()} reacquired (same track, tracker association"
                                    f"{'' if verified else ', appearance unverified'})",
                                    {"method": "tracker", "verified": verified, "similarity": round(sim, 3),
                                     "lost_ms": round(now_ms - (self.last_seen_ms or now_ms))}))
                self.needs_confirm = False
                self.candidates = []
            self.state = "LOCKED"
            self._observe(tr, tracks, now_ms)
            return ev

        # --- not visible -------------------------------------------------------------------------------------
        lost_ms = now_ms - (self.last_seen_ms or now_ms)
        if self.kf is not None:
            self.kf.predict(now_ms)
        if self.state == "LOCKED" and lost_ms < cfg.coast_after_ms:
            handover = self._handover(tracks, exclude, now_ms, lost_ms)
            if handover is not None:
                return [handover]
        if self.state == "LOCKED" and lost_ms >= cfg.coast_after_ms:
            self.state = "COAST"
            self._loss_reported = False
        if self.state in ("COAST", "REACQUIRE") and not self._loss_reported and lost_ms >= self.report_after_ms:
            self._loss_reported = True
            ev.append(LockEvent("TRACK_LOST", self.role, self.track_id,
                                f"{self.role.title()} lost: not observed for {lost_ms / 1000:.1f} s",
                                {"lost_ms": round(lost_ms), "last_seen_xy": self.last_seen_xy}))
        if self.state == "COAST" and lost_ms >= cfg.reacquire_after_ms:
            self.state = "REACQUIRE"
        if self.state in ("COAST", "REACQUIRE", "UNCERTAIN") and lost_ms >= cfg.hold_after_s * 1000.0:
            self.state = "HOLD"
            ev.append(LockEvent("LOCK_HOLD", self.role, self.track_id,
                                f"{self.role.title()} search timed out after {cfg.hold_after_s:.0f} s; operator needed",
                                {"lost_ms": round(lost_ms)}))
        if self.state in ("COAST", "REACQUIRE", "UNCERTAIN", "HOLD"):
            ev.extend(self._search(tracks, exclude, now_ms, lost_ms))
        return ev

    def _observe(self, tr: TrackInfo, tracks: dict[str, TrackInfo], now_ms: float) -> None:
        self.last_seen_ms = now_ms
        if tr.world_xy is not None:
            err = max(0.15, tr.world_err or 0.5)
            if self.kf is None:
                self.kf = CAFilter(tr.world_xy, now_ms, err)
            else:
                self.kf.update(tr.world_xy, now_ms, err)
            self.kf_err = err
            self.last_seen_xy = tr.world_xy
            self.history.append((now_ms, tr.world_xy[0], tr.world_xy[1]))
        # grow the gallery only from clean views (confident, nobody else within 1.5 m)
        crowded = False
        if tr.world_xy is not None:
            for o in tracks.values():
                # only established tracks are people; tentative fragments of the same person are not neighbours
                if (o.track_id != tr.track_id and o.matched_now and o.world_xy is not None and o.state == "CONFIRMED"
                        and o.hits >= 5):
                    if math.dist(o.world_xy, tr.world_xy) < 1.5:
                        crowded = True
                        self.last_neighbor_ms = now_ms
                        break
        if tr.hist is not None and tr.conf >= 0.5 and not crowded:
            if not self.gallery or len(self.gallery) < 5 or self.gallery_similarity(tr.hist) > 0.5:
                self.gallery.append(tr.hist)

    def crossing(self, now_ms: float) -> bool:
        """Lost right after being close to another person: the classic ID-swap trap."""
        return self.last_seen_ms is not None and self.last_seen_ms - self.last_neighbor_ms < 1500.0

    def motion_consistent(self, t: TrackInfo) -> bool | None:
        """True/False when both velocities are known and moving; None when undecidable."""
        if self.kf is None or t.velocity is None:
            return None
        vx, vy = self.kf.velocity
        s1, s2 = math.hypot(vx, vy), math.hypot(*t.velocity)
        if s1 < 0.4 or s2 < 0.4:
            return None
        cos = (vx * t.velocity[0] + vy * t.velocity[1]) / (s1 * s2)
        return cos > math.cos(math.radians(50))

    def _handover(self, tracks: dict[str, TrackInfo], exclude: set[str], now_ms: float, lost_ms: float) -> LockEvent | None:
        """Repair a tracker ID switch inside the association window: exactly one visible track close to the
        prediction with matching appearance. Ambiguity (two plausible candidates) never hands over."""
        pred = self.kf.position if self.kf is not None else None
        if pred is None:
            return None
        gate = 1.5 + 2.5 * lost_ms / 1000.0
        thr = self.cfg.rebind_similarity - 0.08
        plausible = []
        for tid, t in tracks.items():
            if tid == self.track_id or tid in exclude or tid in self.rejected or not t.matched_now or \
                    t.world_xy is None or t.state == "LOST":
                continue
            d = math.dist(t.world_xy, pred)
            if d > gate:
                continue
            sim = self.gallery_similarity(t.hist) if self.gallery else 1.0
            if sim < thr:
                continue
            motion = self.motion_consistent(t)
            if motion is False:
                continue  # moving the wrong way: not our subject
            plausible.append((tid, sim, d, motion))
        if len(plausible) != 1:
            return None
        tid, sim, d, motion = plausible[0]
        if motion is None and self.crossing(now_ms) and d > 0.6:
            return None  # crossing with unknown motion and not at the same spot: wait for evidence instead of guessing
        old = self.track_id
        self.track_id = tid
        self.rebinds += 1
        self._rebind_filter(tracks[tid], now_ms)
        self._observe(tracks[tid], tracks, now_ms)
        return LockEvent("LOCK_HANDOVER", self.role, tid,
                         f"{self.role.title()} track handover {short(old)} -> {short(tid)} (ID switch repaired: "
                         f"{d:.1f} m from prediction, appearance {sim:.2f})",
                         {"previous": old, "similarity": round(sim, 3), "distance_m": round(d, 2), "lost_ms": round(lost_ms)})

    def _search(self, tracks: dict[str, TrackInfo], exclude: set[str], now_ms: float, lost_ms: float) -> list[LockEvent]:
        cfg = self.cfg
        pred = self.kf.position if self.kf is not None else None
        radius = cfg.search_radius_m + cfg.search_growth_mps * lost_ms / 1000.0
        cands: list[tuple[str, float, float | None]] = []
        for tid, t in tracks.items():
            if tid == self.track_id or tid in exclude or tid in self.rejected or not t.matched_now or t.state == "LOST":
                continue
            dist = math.dist(t.world_xy, pred) if (t.world_xy is not None and pred is not None) else None
            if dist is not None and dist > radius:
                continue
            if dist is None and pred is not None:
                continue  # cannot gate this candidate geometrically
            sim = self.gallery_similarity(t.hist)
            motion = self.motion_consistent(t)
            if motion is False:
                sim *= 0.5  # wrong direction of travel: strong evidence against
            cands.append((tid, sim, dist))
        cands.sort(key=lambda c: -c[1])
        self.candidates = [LockCandidate(track_id=c[0], similarity=round(c[1], 3),
                                         distance_m=round(c[2], 2) if c[2] is not None else None) for c in cands[:4]]
        if not cands:
            return []
        top, second = cands[0], (cands[1][1] if len(cands) > 1 else 0.0)
        same_spot = top[2] is not None and top[2] <= 0.6 and len(cands) == 1
        crossing_unresolved = (self.crossing(now_ms) and self.motion_consistent(tracks[top[0]]) is not True
                               and not same_spot)
        if top[1] >= cfg.rebind_similarity and top[1] - second >= cfg.rebind_margin and not crossing_unresolved:
            old = self.track_id
            self._pre_rebind = {"track_id": old, "kf": copy.deepcopy(self.kf), "kf_err": self.kf_err,
                                "last_seen_ms": self.last_seen_ms, "last_seen_xy": self.last_seen_xy}
            self.track_id = top[0]
            self.state = "LOCKED"
            self.verified = False
            self.needs_confirm = False
            self.rebinds += 1
            t = tracks[top[0]]
            self._rebind_filter(t, now_ms)
            self._observe(t, tracks, now_ms)
            self.candidates = []
            return [LockEvent("REACQUIRED", self.role, top[0],
                              f"{self.role.title()} reacquired (UNVERIFIED): track {short(old)} -> {short(top[0])}, "
                              f"appearance {top[1]:.2f}, {top[2]:.1f} m from prediction" if top[2] is not None else
                              f"{self.role.title()} reacquired (UNVERIFIED) on track {short(top[0])}",
                              {"method": "gallery", "verified": False, "similarity": round(top[1], 3),
                               "distance_m": top[2], "previous": old, "lost_ms": round(lost_ms)})]
        if top[1] >= 0.40 and self.state != "UNCERTAIN" and self.state != "HOLD":
            self.state = "UNCERTAIN"
            self.needs_confirm = True
            return [LockEvent("LOCK_UNCERTAIN", self.role, self.track_id,
                              f"{self.role.title()} identity uncertain: {len(cands)} candidate(s), best {top[1]:.2f} "
                              f"(margin {top[1] - second:.2f}); confirmation needed",
                              {"candidates": [c.model_dump() for c in self.candidates]})]
        return []

    # ---------------------------------------------------------------------------------------------------------
    def snapshot(self, now_ms: float) -> LockState:
        lost = (now_ms - self.last_seen_ms) if (self.active and self.state != "LOCKED" and self.last_seen_ms) else None
        pred = self.kf.position if (self.kf is not None and self.active) else None
        radius = None
        if lost is not None:
            radius = round(self.cfg.search_radius_m + self.cfg.search_growth_mps * lost / 1000.0, 2)
        return LockState(role=self.role, state=self.state, track_id=self.track_id, since_ms=self.since_ms,
                         lost_ms=round(lost) if lost is not None else None,
                         predicted_xy_m=(round(pred[0], 2), round(pred[1], 2)) if pred else None,
                         search_radius_m=radius, last_seen_xy_m=self.last_seen_xy, last_seen_ts_ms=self.last_seen_ms,
                         candidates=self.candidates, needs_operator_confirm=self.needs_confirm,
                         verified=self.verified, rebinds=self.rebinds, description=self.description)


def short(track_id: str | None) -> str:
    if not track_id:
        return "?"
    parts = track_id.split(":")
    return f"#{parts[1]}" if len(parts) >= 2 else track_id
