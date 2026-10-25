"""Follow mission: an operator-designated person, tracked with a timestamped trail.

* Only a person designates or re-designates the target (``designate``). The pipeline's subject lock keeps
  tracking on its own (it may re-bind to a new track after an occlusion, by gating and appearance), but the
  incident treats a track as the designated person only if an operator designated or confirmed that track.
  Any other binding shows as "unverified: confirm or re-designate" and is logged; identity never switches silently.
* The trail stores every observation of the bound track with its time, image box and, only when calibrated, its
  ground position. Points recorded while the binding is unverified are marked as such.
* Motion statistics (distance, speed, heading) come only from trail points with a verified position and a
  confirmed identity. No facial recognition, no identity beyond "the track the operator designated", no intent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable

TRAIL_MAX = 3000
STATS_WINDOW_S = 10.0


@dataclass
class TrailPoint:
    t_ms: float
    track_id: str
    xy: tuple[float, float] | None
    err_m: float | None
    box: tuple[float, float, float, float] | None
    identity: str  # "confirmed" (operator-designated track) or "unverified" (bound without a person's confirmation)


@dataclass
class FollowState:
    designation: int = 0  # increments on every designation by a person
    confirmed_tracks: set[str] = field(default_factory=set)
    designated_by: str | None = None
    track_id: str | None = None  # the track the pipeline lock is bound to now
    status: str = "none"  # none | tracking | lost | unverified | released
    lost_since_ms: float | None = None
    last_seen_ms: float | None = None
    last_seen_xy: tuple[float, float] | None = None
    trail: list[TrailPoint] = field(default_factory=list)


class FollowTracker:
    """Pure logic; ``emit(kind, payload)`` records an incident event."""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self.s = FollowState()
        self.emit = emit

    # -- operator actions ------------------------------------------------------------------------------------
    def designate(self, track_id: str, by: str, now_ms: float) -> None:
        first = self.s.designation == 0 or self.s.status == "released"
        prev = self.s.track_id
        self.s.designation += 1
        self.s.confirmed_tracks = {track_id}  # a new designation replaces the old identity, never merges it
        self.s.designated_by = by
        self.s.track_id = track_id
        self.s.status = "tracking"
        self.s.lost_since_ms = None
        self.emit("follow_designated" if first else "follow_redesignated",
                  {"track_id": track_id, "previous_track_id": prev, "by": by, "designation": self.s.designation,
                   "t_ms": now_ms})

    def confirm_binding(self, by: str, now_ms: float) -> None:
        """A person confirms that the track the lock re-bound to is the designated person."""
        if self.s.track_id is None or self.s.status != "unverified":
            raise ValueError("no unverified binding to confirm")
        self.s.confirmed_tracks.add(self.s.track_id)
        self.s.status = "tracking"
        self.emit("follow_redesignated", {"track_id": self.s.track_id, "by": by, "designation": self.s.designation,
                                          "how": "confirmed the re-bound track", "t_ms": now_ms})

    def release(self, by: str, now_ms: float) -> None:
        self.s.status = "released"
        self.emit("follow_released", {"track_id": self.s.track_id, "by": by, "t_ms": now_ms})
        self.s.track_id = None

    # -- per cycle -------------------------------------------------------------------------------------------
    def update(self, lock: Any, tracks: dict[str, dict[str, Any]], now_ms: float) -> None:
        """``lock``: the pipeline's subject LockState. ``tracks``: track_id -> {box, xy, err, visible}."""
        s = self.s
        if s.designation == 0 or s.status == "released":
            return
        if lock.state == "UNLOCKED" or lock.track_id is None:
            if s.status != "lost":
                s.status, s.lost_since_ms = "lost", now_ms
                self.emit("follow_lost", {"track_id": s.track_id, "reason": "the subject lock was released",
                                          "last_seen_xy": s.last_seen_xy, "t_ms": now_ms})
            return
        bound_changed = lock.track_id != s.track_id
        if bound_changed:
            s.track_id = lock.track_id
            if lock.track_id not in s.confirmed_tracks:
                s.status = "unverified"
                self.emit("follow_rebind_unverified",
                          {"track_id": lock.track_id, "how": "re-bound by the lock (gating and appearance)",
                           "lock_verified": bool(getattr(lock, "verified", False)),
                           "text": "Follow target re-bound to a new track without a person's confirmation: "
                                   "confirm or re-designate.", "t_ms": now_ms})
        visible = lock.state == "LOCKED" and tracks.get(lock.track_id, {}).get("visible", False)
        if not visible:
            if s.status in ("tracking", "unverified") and s.lost_since_ms is None:
                s.lost_since_ms = now_ms
                s.status = "lost"
                self.emit("follow_lost", {"track_id": s.track_id, "lock_state": lock.state, "reason": "not visible",
                                          "last_seen_xy": s.last_seen_xy, "t_ms": now_ms})
            return
        if s.status == "lost":
            s.status = "tracking" if s.track_id in s.confirmed_tracks else "unverified"
            self.emit("follow_reacquired", {"track_id": s.track_id, "identity": "confirmed" if s.status == "tracking"
                                            else "unverified", "lost_for_s": round((now_ms - (s.lost_since_ms or now_ms)) / 1000, 1),
                                            "t_ms": now_ms})
            s.lost_since_ms = None
        t = tracks[lock.track_id]
        p = TrailPoint(now_ms, lock.track_id, t.get("xy"), t.get("err"), t.get("box"),
                       "confirmed" if lock.track_id in s.confirmed_tracks else "unverified")
        s.trail.append(p)
        if len(s.trail) > TRAIL_MAX:
            del s.trail[: len(s.trail) - TRAIL_MAX]
        s.last_seen_ms = now_ms
        if p.xy is not None:
            s.last_seen_xy = p.xy

    # -- read model ------------------------------------------------------------------------------------------
    def motion(self, now_ms: float) -> dict[str, Any]:
        pts = [p for p in self.s.trail if p.identity == "confirmed" and p.xy is not None and p.err_m is not None
               and p.track_id in self.s.confirmed_tracks]
        if len(pts) < 2:
            return {"valid": False, "reason": "no verified positions of the confirmed target yet"}
        unc = float(median(p.err_m for p in pts))  # type: ignore[misc]
        # distance counts only moves larger than twice the position uncertainty, so jitter is not "travel"
        dist, anchor = 0.0, pts[0]
        for p in pts[1:]:
            d = math.hypot(p.xy[0] - anchor.xy[0], p.xy[1] - anchor.xy[1])  # type: ignore[index]
            if d > 2 * unc:
                dist += d
                anchor = p
        recent = [p for p in pts if now_ms - p.t_ms <= STATS_WINDOW_S * 1000]
        out: dict[str, Any] = {"valid": True, "distance_m": round(dist, 1), "points": len(pts),
                               "source": "trail points with a calibrated ground position and a confirmed identity",
                               "position_uncertainty_m": round(unc, 2)}
        if len(recent) >= 2 and recent[-1].t_ms - recent[0].t_ms >= 2000:
            a, b = recent[0], recent[-1]
            dt = (b.t_ms - a.t_ms) / 1000
            dx, dy = b.xy[0] - a.xy[0], b.xy[1] - a.xy[1]  # type: ignore[index]
            d = math.hypot(dx, dy)
            # displacement below the position uncertainty is not reported as movement
            if d <= 2 * out["position_uncertainty_m"]:
                out.update(speed_mps=0.0, heading_deg=None, note="displacement within the position uncertainty")
            else:
                out.update(speed_mps=round(d / dt, 2), heading_deg=round((math.degrees(math.atan2(dx, dy)) + 360) % 360))
            out["window_s"] = round(dt, 1)
        return out

    def view(self, now_ms: float) -> dict[str, Any]:
        s = self.s
        text = {"none": "No target designated. Select a box and designate it.",
                "tracking": "Tracking the designated target.",
                "lost": "Target lost from view" + (f" {round((now_ms - s.lost_since_ms) / 1000)} s ago" if s.lost_since_ms else "")
                        + ". Last seen position is not a current position.",
                "unverified": "The lock re-bound to another track. Not confirmed as the designated target: "
                              "confirm or re-designate.",
                "released": "Follow released."}[s.status]
        return {"status": s.status, "text": text, "track_id": s.track_id, "designation": s.designation,
                "designated_by": s.designated_by, "confirmed_tracks": sorted(s.confirmed_tracks),
                "last_seen_ms": s.last_seen_ms, "last_seen_xy": s.last_seen_xy,
                "trail": [{"t_ms": p.t_ms, "track_id": p.track_id, "xy": p.xy, "err_m": p.err_m, "identity": p.identity}
                          for p in s.trail[-600:]],
                "motion": self.motion(now_ms)}
