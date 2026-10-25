"""Object memory: every person keeps a persistent object id and a world position while it cannot be seen.

Ideas from Permanence (Knight Hacks IX, github.com/billpquach/permanence), reimplemented here: that repository has no
licence, so none of its code is used. Overwatch's image tracker ends a track 4 s after it stops matching and a person
coming back gets a new track id. The memory sits above the tracker, in the incident's ground frame:

* Each object is VISIBLE, OCCLUDED (its remembered position is in view but covered, for example by tree canopy),
  OUT_OF_VIEW (outside the camera footprint) or LOST (in view, uncovered, and still not detected after 0.6 s).
  A state changes only after 3 frames agree. Confidence fades at a different rate in each hidden state.
* A new track is attached to a hidden object only when it is the single plausible match: inside the object's gate
  (its position uncertainty grown by walking speed and time unseen), with matching appearance, and clearly better
  than any other object. Attachment after an explained absence (occluded, out of view) within the uncertainty is
  ``continued``; anything wider is only ``possible`` and is never treated as the same person by Search or Follow.
* Positions exist only with a valid ground calibration: without one the memory holds nothing (it never guesses).

The memory never changes the tracker or the locks; it is a read model for Search, the map, the video overlay and
pursuit routing. People only: no identity beyond "the object this track continues", no faces.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..vision.appearance import similarity

VISIBLE, OCCLUDED, OUT_OF_VIEW, LOST = "VISIBLE", "OCCLUDED", "OUT_OF_VIEW", "LOST"
HIDDEN = (OCCLUDED, OUT_OF_VIEW, LOST)
FADE_S = {OCCLUDED: 30.0, OUT_OF_VIEW: 60.0, LOST: 8.0}  # confidence time constants per hidden state
LOST_AFTER_S = 0.6
WALK_MPS = 1.5  # how fast a hidden person's possible area grows
FORGET_CONFIDENCE = 0.03
SIM_MIN = 0.55  # appearance similarity needed to attach a new track to a remembered person
SIM_MARGIN = 0.08
CROSSING_M = 2.5  # another person this close to a new track makes any attachment a guess
RUNNER_MPS = 2.0  # a person last seen this fast is not attached to a new track well behind where they were heading


@dataclass
class MemObject:
    oid: str
    label: str
    xy: np.ndarray
    err_m: float
    t_ms: float
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))
    state: str = VISIBLE
    last_seen_ms: float = 0.0
    ok_ms: float = 0.0  # last time it was seen or its absence was explained
    track_id: str | None = None
    track_ids: deque = field(default_factory=lambda: deque(maxlen=12))
    hist: np.ndarray | None = None
    box: tuple[float, float, float, float] | None = None
    link: str = "first_seen"  # first_seen | continued | possible (how the current track was attached)
    colors: tuple[str, str] | None = None
    pending: str | None = None
    pending_n: int = 0
    events: list[dict] = field(default_factory=list)
    first_ms: float = 0.0  # when this person was first seen (a person first seen after a loss may be the lost one)
    first_xy: tuple[float, float] | None = None
    seen_vel: np.ndarray = field(default_factory=lambda: np.zeros(2))  # the velocity while last observed (kept hidden)

    def since_s(self, now: float) -> float:
        return 0.0 if self.state == VISIBLE else max(0.0, (now - self.last_seen_ms) / 1000.0)

    def confidence(self, now: float) -> float:
        return 1.0 if self.state == VISIBLE else math.exp(-self.since_s(now) / FADE_S[self.state])

    def radius_m(self, now: float) -> float:
        """The area a hidden person could be in now: position uncertainty plus walking reach."""
        return self.err_m + WALK_MPS * self.since_s(now)

    def change(self, want: str, frames: int = 3) -> bool:
        if want == self.state:
            self.pending, self.pending_n = None, 0
            return False
        if want == self.pending:
            self.pending_n += 1
        else:
            self.pending, self.pending_n = want, 1
        if self.pending_n >= frames:
            self.state, self.pending, self.pending_n = want, None, 0
            return True
        return False


@dataclass
class TrackView:
    """What the memory needs about one tracker track this frame."""

    track_id: str
    label: str
    matched_now: bool
    confirmed: bool
    xy: tuple[float, float] | None
    err_m: float | None
    box: tuple[float, float, float, float] | None
    hist: np.ndarray | None
    colors: tuple[str, str] | None = None  # (torso, lower) colour names, for the operator's "look for"


class ObjectMemory:
    def __init__(self) -> None:
        self.objects: dict[str, MemObject] = {}
        self.by_track: dict[str, str] = {}
        self.next_id = 1
        self.events: deque[dict] = deque(maxlen=400)
        self.seq = 0  # increments per event, so readers can take what is new
        self.forgotten = 0

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    # -- per frame -------------------------------------------------------------------------------------------
    def update(self, now: float, tracks: list[TrackView], in_view: Callable[[tuple[float, float]], bool] | None,
               covered: Callable[[MemObject], bool] | None) -> None:
        """``in_view(xy)``: is a ground point inside the current image; ``covered(obj)``: is its remembered spot
        covered in the image (canopy, roof). Both None without a valid calibration (nothing is explained)."""
        seen: set[str] = set()
        fresh: list[TrackView] = []
        for tv in tracks:
            if tv.xy is None or not tv.matched_now:
                continue
            oid = self.by_track.get(tv.track_id)
            if oid is not None and oid in self.objects:
                self._seen(self.objects[oid], tv, now)
                seen.add(oid)
            elif tv.confirmed:
                fresh.append(tv)
        for tv, obj, link in self._attach(fresh, seen, now):
            if obj is None:
                obj = MemObject(f"obj-{self.next_id}", tv.label, np.array(tv.xy, float), tv.err_m or 1.0, now,
                                last_seen_ms=now, ok_ms=now, first_ms=now, first_xy=(float(tv.xy[0]), float(tv.xy[1])))
                self.next_id += 1
                self.objects[obj.oid] = obj
                self._event(now, "object_new", obj, track_id=tv.track_id)
            else:
                self._event(now, "object_reattached", obj, track_id=tv.track_id, link=link,
                            previous_track_id=obj.track_id, hidden_s=round(obj.since_s(now), 1), was=obj.state)
                obj.link = link
                obj.state, obj.pending, obj.pending_n = VISIBLE, None, 0
            self.by_track[tv.track_id] = obj.oid
            self._seen(obj, tv, now)
            seen.add(obj.oid)
        for obj in list(self.objects.values()):
            if obj.oid in seen:
                continue
            self._missed(obj, now, in_view, covered)
            if obj.state in HIDDEN and obj.confidence(now) < FORGET_CONFIDENCE:
                self._event(now, "object_forgotten", obj)
                del self.objects[obj.oid]
                self.forgotten += 1

    def _seen(self, obj: MemObject, tv: TrackView, now: float) -> None:
        dt = max(1e-3, (now - obj.t_ms) / 1000.0)
        z = np.array(tv.xy, float)
        if obj.state == VISIBLE and obj.track_id == tv.track_id:
            pred = obj.xy + obj.vel * dt
            r = z - pred
            moving = float(np.hypot(*r)) > 0.15 or float(np.hypot(*obj.vel)) > 0.3
            a, b = (0.6, 0.25) if moving else (0.25, 0.05)  # alpha-beta: steady when still, quick when moving
            obj.xy = pred + a * r
            obj.vel = obj.vel + b * r / dt
            sp = float(np.hypot(*obj.vel))
            if sp > 8.0:
                obj.vel *= 8.0 / sp
        else:
            obj.xy, obj.vel = z, np.zeros(2)
        obj.seen_vel = obj.vel.copy()
        if obj.state != VISIBLE:
            self._event(now, "object_visible", obj, track_id=tv.track_id, hidden_s=round(obj.since_s(now), 1))
        obj.state, obj.pending, obj.pending_n = VISIBLE, None, 0
        obj.err_m = float(tv.err_m or obj.err_m)
        obj.t_ms = obj.last_seen_ms = obj.ok_ms = now
        if obj.track_id != tv.track_id:
            obj.track_ids.append(tv.track_id)
        obj.track_id = tv.track_id
        obj.box = tv.box
        if tv.colors is not None:
            obj.colors = tv.colors
        if tv.hist is not None:
            obj.hist = tv.hist if obj.hist is None else 0.85 * obj.hist + 0.15 * tv.hist

    def _missed(self, obj: MemObject, now: float, in_view, covered) -> None:
        obj.vel *= 0.9 if obj.state != VISIBLE else 0.7
        obj.t_ms = now
        if in_view is None:
            why = None
        elif not in_view((float(obj.xy[0]), float(obj.xy[1]))):
            why = "away"
        elif covered is not None and covered(obj):
            why = "covered"
        else:
            why = None
        if why == "away":
            want = OUT_OF_VIEW
        elif why == "covered":
            want = OCCLUDED
        else:
            want = LOST if (now - obj.ok_ms) / 1000.0 > LOST_AFTER_S else obj.state
        if why is not None and obj.state == want:
            obj.ok_ms = now
        before = obj.state
        if obj.change(want):
            self._event(now, f"object_{obj.state.lower()}", obj, was=before)

    def _attach(self, fresh: list[TrackView], seen: set[str], now: float) -> list[tuple[TrackView, MemObject | None, str]]:
        """Attach new tracks to hidden objects only when unambiguous; otherwise they are new objects."""
        out: list[tuple[TrackView, MemObject | None, str]] = []
        # every object not matched this frame: hidden ones, and ones whose track just ended (a tracker id switch
        # while the object is still VISIBLE during the 3-frame hysteresis)
        hidden = [o for o in self.objects.values() if o.oid not in seen]
        taken: set[str] = set()
        scored = []
        seen_xy = [self.objects[k].xy for k in seen if k in self.objects]
        for tv in fresh:
            cands = []
            # another person right next to the new track (a crossing): two people fit, so never attach
            near = [float(np.hypot(q[0] - tv.xy[0], q[1] - tv.xy[1])) for q in seen_xy]  # type: ignore[index]
            if near and min(near) < CROSSING_M + (tv.err_m or 1.0):
                scored.append((tv, []))
                continue
            for o in hidden:
                if o.label != tv.label or o.oid in taken:
                    continue
                d = float(np.hypot(o.xy[0] - tv.xy[0], o.xy[1] - tv.xy[1]))  # type: ignore[index]
                gone_s = (now - o.last_seen_ms) / 1000.0
                gate = min(25.0, (tv.err_m or 1.0) + o.err_m + WALK_MPS * gone_s + 1.0)
                if d > gate:
                    continue
                sim = similarity(tv.hist, o.hist) if tv.hist is not None and o.hist is not None else None
                if sim is not None and sim < SIM_MIN:
                    continue
                spd = float(np.hypot(*o.seen_vel))
                if spd >= RUNNER_MPS:
                    # someone last seen running one way does not come back into view well behind where they were
                    # heading: a different person (in PP6, the subject behind a look-alike who ran on) is likelier
                    along = float(((tv.xy[0] - o.xy[0]) * o.seen_vel[0] + (tv.xy[1] - o.xy[1]) * o.seen_vel[1]) / spd)
                    if along < -((tv.err_m or 1.0) + o.err_m + 2.0):
                        self._event(now, "object_attach_refused", o, track_id=tv.track_id, behind_m=round(-along, 1),
                                    why=f"last seen running at {spd:.1f} m/s the other way")
                        continue
                cands.append((d / gate - 0.5 * (sim or 0.0), o, d, sim))
            cands.sort(key=lambda c: c[0])
            scored.append((tv, cands))
        for tv, cands in scored:
            cands = [c for c in cands if c[1].oid not in taken]
            if not cands:
                out.append((tv, None, "first_seen"))
                continue
            best = cands[0]
            if len(cands) > 1 and cands[1][0] - best[0] < SIM_MARGIN:
                out.append((tv, None, "first_seen"))  # two plausible people: do not guess
                continue
            o, d, sim = best[1], best[2], best[3]
            gone_s = (now - o.last_seen_ms) / 1000.0
            tight = d <= (tv.err_m or 1.0) + o.err_m + 1.0 + WALK_MPS * min(gone_s, 2.0)
            explained = o.state in (OCCLUDED, OUT_OF_VIEW) or (o.state == VISIBLE and gone_s < 1.5)
            link = "continued" if (explained and tight and (sim is None or sim >= 0.7)) else "possible"
            taken.add(o.oid)
            out.append((tv, o, link))
        return out

    def _event(self, now: float, kind: str, obj: MemObject, **data: Any) -> None:
        self.seq += 1
        e = {"seq": self.seq, "ts_ms": now, "kind": kind, "object_id": obj.oid, "label": obj.label, "state": obj.state,
             "xy": [round(float(obj.xy[0]), 2), round(float(obj.xy[1]), 2)], **data}
        self.events.append(e)
        obj.events.append(e)
        del obj.events[:-20]

    # -- read model ------------------------------------------------------------------------------------------
    def object_for_track(self, track_id: str) -> MemObject | None:
        oid = self.by_track.get(track_id)
        return self.objects.get(oid) if oid else None

    def snapshot(self, now: float, project: Callable[[tuple[float, float]], tuple[float, float] | None] | None = None
                 ) -> list[dict[str, Any]]:
        out = []
        for o in self.objects.values():
            d = {"object_id": o.oid, "label": o.label, "state": o.state, "track_id": o.track_id,
                 "xy": [round(float(o.xy[0]), 2), round(float(o.xy[1]), 2)],
                 "velocity": [round(float(o.vel[0]), 2), round(float(o.vel[1]), 2)],
                 "error_m": round(o.err_m, 2), "radius_m": round(o.radius_m(now), 2),
                 "since_seen_s": round(o.since_s(now), 1), "confidence": round(o.confidence(now), 3),
                 "link": o.link, "track_ids": list(o.track_ids), "colors": list(o.colors) if o.colors else None}
            if o.state != VISIBLE and project is not None and o.box is not None:
                uv = project((float(o.xy[0]), float(o.xy[1])))
                if uv is not None:  # where the remembered object would be in this frame (feet at its ground point)
                    w, h = o.box[2] - o.box[0], o.box[3] - o.box[1]
                    d["ghost_box"] = [round(uv[0] - w / 2, 1), round(uv[1] - h, 1), round(uv[0] + w / 2, 1), round(uv[1], 1)]
            out.append(d)
        return out
