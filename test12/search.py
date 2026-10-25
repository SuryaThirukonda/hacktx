"""Where the suspect could be, what the drone has already looked at, and where to look next.

Belief: 1,500 particles on the public streets' sidewalks, each a guess of where the suspect is and how fast they run
(2.6 to 5.4 m/s). They spread out from the reported corner along the streets as time passes (straight on at a corner
is twice as likely as each turn), and some step into hiding spots on the way, more of them when the drone is near.
After every detector pass, particles the camera could have seen (in the frame, within YOLO's range, not under a roof or
a canopy, not behind a building) and saw no red shirt near lose most of their weight: that is how a looked-at street
becomes "nobody there", while a carport roof stays "could be under it". When everything plausible has been ruled out,
the belief starts again from everywhere the suspect could have reached.

Survey log: a 2 m grid of when each patch of ground was last actually seen (same line-of-sight test). The page draws
it; the planner uses it so the drone does not fly back over ground it has just cleared.

Planner: the belief is run forward (up to 30 s) to where the suspect would be by the time the drone gets somewhere.
Candidate viewpoints look at the heaviest 24 m clusters of those predictions from 25 m back, either along the street
the cluster is on (the tilted camera then sees a long stretch of it) or straight from the drone; for people who may be
crouching behind a truck or a kiosk, a viewpoint right above them (nothing hides you from straight overhead). A
candidate scores the predicted belief weight its camera would cover on arrival plus half of what it would cover on
the way, per second of flight; viewpoints visited in the last 40 s count a third. The drone sticks with its pick until
it has looked there, the pick loses three quarters of its value, or another one is clearly (1.6 times) better: it
does not flip between two clusters or circle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from overwatch.geometry.camera import CameraPose, PinholeCamera, heading_from_vector

from .drone import CAMERA
from .los import Occluders
from .streets import Streets
from .world import BOUNDS, HideSpot

RUN, HIDE = 0, 1
YOLO_RANGE_M = 55.0  # slant range beyond which the detector is too unreliable to rule anyone out
# one detector pass finds a visible person within that range 60 to 80 % of the time, but passes five times a second
# from nearly the same place are not independent: a smaller per-pass figure keeps a few misses from clearing a street
P_DETECT = 0.4
V_MIN, V_MAX = 2.6, 5.4
OVERHEAD_FRACTION = 0.3  # within 0.3 x altitude of the camera's ground point counts as straight overhead


def in_polygon(pts: np.ndarray, poly) -> np.ndarray:
    if poly is None or len(poly) < 3 or not len(pts):
        return np.zeros(len(pts), bool)
    P = np.asarray(poly, float)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    xi, yi = P[:, 0][None, :], P[:, 1][None, :]
    xj, yj = np.roll(P[:, 0], 1)[None, :], np.roll(P[:, 1], 1)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / np.where(yj - yi == 0, 1e-12, yj - yi) + xi)
    return (cross.sum(axis=1) % 2) == 1


def view_camera(xy, alt: float, heading: float, pitch: float) -> PinholeCamera:
    return PinholeCamera(CAMERA, CameraPose(np.array([xy[0], xy[1], alt]), heading, pitch))


def in_view(cam: PinholeCamera, pts: np.ndarray, z: float = 1.1, max_range: float = YOLO_RANGE_M) -> np.ndarray:
    if not len(pts):
        return np.zeros(0, bool)
    P = np.column_stack([pts, np.full(len(pts), z)])
    uv, depth = cam.project(P)
    W, H = cam.intr.width, cam.intr.height
    rng = np.linalg.norm(P - cam.pose.position[None, :], axis=1)
    return (depth > 0.5) & (uv[:, 0] >= 0) & (uv[:, 0] < W) & (uv[:, 1] >= 0) & (uv[:, 1] < H) & (rng <= max_range)


class Belief:
    def __init__(self, streets: Streets, spots: list[HideSpot], occl: Occluders, rng: np.random.Generator,
                 n: int = 1500) -> None:
        self.st, self.spots, self.occl, self.rng, self.n = streets, spots, occl, rng, n
        seg, s_along = [], []
        for sp in spots:
            e, s, _ = streets.project(sp.approach, lane=True)
            seg.append(e // 2)
            s_along.append(s if e % 2 == 0 else streets.length[e] - s)
        self.spot_seg = np.array(seg, int)
        self.spot_s = np.array(s_along, float)
        self.spot_xy = np.array([sp.hide for sp in spots], float)
        self.spot_approach = np.array([sp.approach for sp in spots], float)
        self.spot_behind = np.array([sp.behind for sp in spots], bool)
        self.e = np.zeros(n, int)
        self.s = np.zeros(n)
        self.v = np.zeros(n)
        self.mode = np.zeros(n, int)
        self.spot = np.full(n, -1)
        self.timer = np.zeros(n)
        self.w = np.full(n, 1.0 / n)
        self.mass = 1.0  # how much of the belief the observations have left since it was (re)seeded
        self.origin: tuple[float, float] | None = None
        self.origin_t = 0.0

    def clone(self, seed: int) -> "Belief":
        """A copy to run forward for planning (its own random stream, so the real belief is untouched)."""
        b = Belief.__new__(Belief)
        b.__dict__.update(self.__dict__)
        for a in ("e", "s", "v", "mode", "spot", "timer", "w"):
            setattr(b, a, getattr(self, a).copy())
        b.rng = np.random.default_rng(seed)
        return b

    # -- seeding ------------------------------------------------------------------------------------------
    def seed_report(self, xy, t_seen: float, now: float, drone_xy=None, heading_dir=None) -> None:
        """A report: the suspect was at ``xy`` at ``t_seen``, running along ``heading_dir`` if the caller saw which
        way (taken as 75 % likely: the caller may be wrong), at an unknown speed."""
        e, s, _ = self.st.project(xy, lane=True)
        n = self.n
        if heading_dir is not None:
            p_fwd = 0.75 if float(np.dot(self.st.dir[e], heading_dir)) > 0 else 0.25
        else:
            p_fwd = 0.5
        fwd = self.rng.random(n) < p_fwd
        L = self.st.length[e]
        self.e = np.where(fwd, e, e ^ 1)
        base = np.clip(s + self.rng.uniform(-2.0, 2.0, n), 0.0, L)
        self.s = np.where(fwd, base, L - base)
        self.v = self.rng.uniform(V_MIN, V_MAX, n)
        self.mode[:] = RUN
        self.spot[:] = -1
        self.w[:] = 1.0 / n
        self.mass = 1.0
        self.origin, self.origin_t = (float(xy[0]), float(xy[1])), t_seen
        for _ in range(int(max(0.0, now - t_seen) / 0.5)):
            self.predict(0.5, drone_xy)

    def seed_sighting(self, xy, vel, t_seen: float, hide_share: float = 0.65) -> None:
        """Lost from view at ``xy`` moving at ``vel`` while being followed: most likely ducked into nearby cover
        (``hide_share``), else still running on."""
        n = self.n
        e, s, _ = self.st.project(xy, lane=True)
        sp = math.hypot(*vel) if vel is not None else 0.0
        along = float(np.dot(self.st.dir[e], vel)) if vel is not None else 0.0
        n_hide = int(hide_share * n)
        n_run = n - n_hide
        go_fwd = self.rng.random(n_run) < (0.78 if along >= 0 else 0.22) if sp > 0.8 else self.rng.random(n_run) < 0.5
        L = self.st.length[e]
        base = np.clip(s + self.rng.normal(0.0, 1.5, n_run), 0.0, L)
        self.e[:n_run] = np.where(go_fwd, e, e ^ 1)
        self.s[:n_run] = np.where(go_fwd, base, L - base)
        self.v[:n_run] = np.clip(self.rng.normal(max(sp, 2.5), 0.8, n_run), 1.5, V_MAX)
        self.mode[:n_run] = RUN
        self.spot[:n_run] = -1
        d = np.linalg.norm(self.spot_approach - np.asarray(xy, float), axis=1)
        near = np.nonzero(d < 38.0)[0]
        if len(near):
            pw = 1.0 / (1.0 + d[near] / 8.0)
            k = self.rng.choice(near, size=n_hide, p=pw / pw.sum())
            self.mode[n_run:] = HIDE
            self.spot[n_run:] = k
            self.timer[n_run:] = self.rng.uniform(2.0, 14.0, n_hide)
            self.e[n_run:] = 2 * self.spot_seg[k]
            self.s[n_run:] = self.spot_s[k]
            self.v[n_run:] = np.clip(self.rng.normal(max(sp, 3.0), 0.8, n_hide), 1.5, V_MAX)
        else:
            self.mode[n_run:] = RUN
            self.e[n_run:] = self.e[:n_hide]
            self.s[n_run:] = self.s[:n_hide]
            self.v[n_run:] = self.v[:n_hide]
            self.spot[n_run:] = -1
        self.w[:] = 1.0 / n
        self.mass = 1.0
        self.origin, self.origin_t = (float(xy[0]), float(xy[1])), t_seen

    def seed_reachable(self, now: float) -> None:
        """Everything is ruled out: the suspect could be anywhere they could have reached since last seen."""
        if self.origin is None:
            return
        reach = V_MAX * max(5.0, now - self.origin_t)
        dist = self.st.distances_from(self.origin)
        segs = [e for e in range(len(self.st.frm)) if min(dist[self.st.frm[e]], dist[self.st.to[e]]) <= reach]
        if not segs:
            return
        n = self.n
        pick = self.rng.choice(segs, size=n)
        self.e = np.asarray(pick, int)
        self.s = self.rng.uniform(0.0, 1.0, n) * self.st.length[self.e]
        self.v = self.rng.uniform(V_MIN, V_MAX, n)
        self.mode[:] = RUN
        self.spot[:] = -1
        k = self.rng.random(n) < 0.3  # some of them in cover
        if k.any():
            ds = np.linalg.norm(self.spot_approach - np.asarray(self.origin), axis=1)
            ok = np.nonzero(ds <= reach)[0]
            if len(ok):
                kk = self.rng.choice(ok, size=int(k.sum()))
                self.mode[k] = HIDE
                self.spot[k] = kk
                self.timer[k] = self.rng.uniform(1.0, 12.0, int(k.sum()))
                self.e[k] = 2 * self.spot_seg[kk]
                self.s[k] = self.spot_s[kk]
        self.w[:] = 1.0 / n
        self.mass = 1.0

    # -- motion -------------------------------------------------------------------------------------------
    def _next(self, e: np.ndarray) -> np.ndarray:
        st = self.st
        cand = st.out_arr[st.to[e]]
        valid = (cand >= 0) & (cand != (e ^ 1)[:, None])
        dots = np.einsum("ij,ikj->ik", st.dir[e], st.dir[np.maximum(cand, 0)])
        wts = np.where(valid, np.where(dots > 0.9, 2.0, 1.0), 0.0)
        dead = wts.sum(axis=1) <= 0
        cum = np.cumsum(wts, axis=1)
        r = self.rng.random(len(e)) * np.maximum(cum[:, -1], 1e-9)
        pick = np.argmax(cum > r[:, None], axis=1)
        nxt = cand[np.arange(len(e)), pick]
        return np.where(dead, e ^ 1, nxt)

    def predict(self, dt: float, drone_xy=None) -> None:
        st = self.st
        run = self.mode == RUN
        s_prev = self.s.copy()
        self.s[run] += self.v[run] * dt
        L2 = st.length[2 * self.spot_seg]
        if drone_xy is not None:
            near_drone = np.linalg.norm(self.spot_approach - np.asarray(drone_xy, float), axis=1) < 55.0
        else:
            near_drone = np.zeros(len(self.spots), bool)
        for k in range(len(self.spots)):
            u, sk = self.spot_seg[k], self.spot_s[k]
            cand = run & (((self.e == 2 * u) & (s_prev < sk) & (self.s >= sk)) |
                          ((self.e == 2 * u + 1) & (s_prev < L2[k] - sk) & (self.s >= L2[k] - sk)))
            if not cand.any():
                continue
            go = cand & (self.rng.random(self.n) < (0.45 if near_drone[k] else 0.03))
            if go.any():
                self.mode[go] = HIDE
                self.spot[go] = k
                self.timer[go] = self.rng.uniform(6.0, 16.0, int(go.sum()))
                run &= ~go
        for _ in range(4):
            over = run & (self.s > st.length[self.e])
            if not over.any():
                break
            self.s[over] -= st.length[self.e[over]]
            self.e[over] = self._next(self.e[over])
        hid = self.mode == HIDE
        self.timer[hid] -= dt
        out = hid & (self.timer <= 0)
        if out.any():
            k = self.spot[out]
            fwd = self.rng.random(int(out.sum())) < 0.5
            self.e[out] = np.where(fwd, 2 * self.spot_seg[k], 2 * self.spot_seg[k] + 1)
            self.s[out] = np.where(fwd, self.spot_s[k], st.length[2 * self.spot_seg[k]] - self.spot_s[k])
            self.mode[out] = RUN
            self.spot[out] = -1

    def positions(self) -> np.ndarray:
        st = self.st
        pos = st.lane_xy(self.e, np.clip(self.s, 0.0, st.length[self.e]))
        hid = self.mode == HIDE
        if hid.any():
            pos[hid] = self.spot_xy[self.spot[hid]]
        return pos

    # -- observation ----------------------------------------------------------------------------------------
    def observe(self, cam: PinholeCamera, red_xy: list[tuple[float, float]], now: float) -> np.ndarray:
        """Negative information: particles the camera could see, with no red shirt near them, lose weight. Positive
        information: a red-shirt sighting (even one, not yet confirmed) makes the guesses near it much more likely,
        and if the belief had already ruled that spot out, fresh guesses are put there, so the drone goes back to
        look instead of flying on."""
        if red_xy:
            self._sighted(red_xy)
        pos = self.positions()
        cand = in_view(cam, pos)
        hidden_behind = (self.mode == HIDE) & self.spot_behind[np.maximum(self.spot, 0)]
        if hidden_behind.any():  # they keep the obstacle between them and the drone, unless it is right above them
            c = cam.pose.position
            hidden_behind &= np.linalg.norm(pos - c[None, :2], axis=1) > OVERHEAD_FRACTION * c[2]
        cand &= ~hidden_behind
        vis = np.zeros(self.n, bool)
        idx = np.nonzero(cand)[0]
        if len(idx):
            vis[idx] = self.occl.visible(cam.pose.position, pos[idx])
        if red_xy and vis.any():
            R = np.array(red_xy, float)
            d = np.min(np.linalg.norm(pos[:, None, :] - R[None, :, :], axis=2), axis=1)
            vis &= d > 6.0
        if vis.any():
            before = float(self.w.sum())
            self.w[vis] *= 1.0 - P_DETECT
            after = float(self.w.sum())
            self.mass *= after / max(before, 1e-12)
            self.w /= max(after, 1e-12)
            ess = 1.0 / float(np.sum(self.w ** 2))
            if ess < 0.5 * self.n:
                self._resample()
        if self.mass < 2e-3:
            self.seed_reachable(now)
        return vis

    def _sighted(self, red_xy: list[tuple[float, float]]) -> None:
        pos = self.positions()
        R = np.array(red_xy, float)
        d = np.min(np.linalg.norm(pos[:, None, :] - R[None, :, :], axis=2), axis=1)
        near = d < 5.0
        self.w[near] *= 6.0
        share = float(self.w[near].sum() / max(self.w.sum(), 1e-12))
        if share < 0.3:  # the belief had (wrongly) ruled this spot out: put a quarter of the guesses there
            k = self.n // 4
            idx = np.argsort(self.w)[:k]
            which = self.rng.integers(len(R), size=k)
            for j, (x, y) in enumerate(R):
                sel = idx[which == j]
                if not len(sel):
                    continue
                e, s, _ = self.st.project((x, y), lane=True)
                fwd = self.rng.random(len(sel)) < 0.5
                L = self.st.length[e]
                base = np.clip(s + self.rng.normal(0.0, 1.5, len(sel)), 0.0, L)
                self.e[sel] = np.where(fwd, e, e ^ 1)
                self.s[sel] = np.where(fwd, base, L - base)
                self.v[sel] = self.rng.uniform(V_MIN, V_MAX, len(sel))
                self.mode[sel] = RUN
                self.spot[sel] = -1
            self.w[idx] = float(np.mean(self.w)) * 2.0
        self.w /= self.w.sum()
        self.mass = max(self.mass, 0.5)  # fresh evidence: the belief is not exhausted

    def _resample(self) -> None:
        n = self.n
        c = np.cumsum(self.w)
        c[-1] = 1.0
        idx = np.searchsorted(c, (self.rng.random() + np.arange(n)) / n)
        for a in ("e", "s", "v", "mode", "spot", "timer"):
            setattr(self, a, getattr(self, a)[idx].copy())
        run = self.mode == RUN
        self.s[run] = np.clip(self.s[run] + self.rng.normal(0.0, 0.8, int(run.sum())), 0.0,
                              self.st.length[self.e[run]])
        self.v = np.clip(self.v + self.rng.normal(0.0, 0.15, n), V_MIN * 0.6, V_MAX)
        self.w[:] = 1.0 / n

    def view(self, k: int = 450) -> list[list[float]]:
        """A weighted subsample for the page: [x, y, hidden]. Its own random stream, so drawing the page never
        changes a run."""
        pos = self.positions()
        idx = np.random.default_rng(len(pos)).choice(self.n, size=min(k, self.n), replace=False, p=self.w / self.w.sum())
        return [[round(float(pos[i, 0]), 1), round(float(pos[i, 1]), 1), int(self.mode[i] == HIDE)] for i in idx]


class SurveyLog:
    CELL = 2.0

    def __init__(self, occl: Occluders, streets: Streets) -> None:
        x0, x1, y0, y1 = BOUNDS
        self.nx, self.ny = int(math.ceil((x1 - x0) / self.CELL)), int(math.ceil((y1 - y0) / self.CELL))
        self.x0, self.y0 = x0, y0
        self.last = np.full((self.ny, self.nx), -1e9, np.float32)
        self.occl = occl
        xs = x0 + (np.arange(self.nx) + 0.5) * self.CELL
        ys = y0 + (np.arange(self.ny) + 0.5) * self.CELL
        self.cx, self.cy = np.meshgrid(xs, ys)
        walk = np.zeros((self.ny, self.nx), np.uint8)  # the public sidewalks: where the suspect can be
        for e in range(0, len(streets.frm)):
            a = streets.lane_xy(e, 0.0)
            b = streets.lane_xy(e, float(streets.length[e]))
            pa, pb = self._px(a), self._px(b)
            cv2.line(walk, pa, pb, 1, 2)
        self.walk = walk.astype(bool)
        self.walk_area_m2 = float(self.walk.sum()) * self.CELL ** 2

    def _px(self, xy) -> tuple[int, int]:
        return int((xy[0] - self.x0) / self.CELL), int((xy[1] - self.y0) / self.CELL)

    def update(self, t: float, cam: PinholeCamera) -> tuple[int, int]:
        """Marks the cells the camera saw. Returns sidewalk cells that came into view (not seen in the last 8 s) and,
        of those, the ones the drone had already seen 8 to 90 s ago: it came back to ground it had cleared."""
        fp = cam.footprint(YOLO_RANGE_M)
        if fp is None:
            return 0, 0
        P = np.array(fp, float)
        i0, j0 = self._px(P.min(axis=0))
        i1, j1 = self._px(P.max(axis=0))
        i0, j0 = max(0, i0), max(0, j0)
        i1, j1 = min(self.nx - 1, i1), min(self.ny - 1, j1)
        if i1 < i0 or j1 < j0:
            return 0, 0
        cx = self.cx[j0:j1 + 1, i0:i1 + 1].ravel()
        cy = self.cy[j0:j1 + 1, i0:i1 + 1].ravel()
        pts = np.column_stack([cx, cy])
        inside = in_view(cam, pts)
        if not inside.any():
            return 0, 0
        jj, ii = np.unravel_index(np.nonzero(inside)[0], (j1 - j0 + 1, i1 - i0 + 1))
        vis = self.occl.visible(cam.pose.position, pts[inside])
        jj, ii = jj[vis] + j0, ii[vis] + i0
        last = self.last[jj, ii]
        walk = self.walk[jj, ii]
        entering = walk & (last < t - 8.0)
        revisit = entering & (last >= t - 90.0)
        self.last[jj, ii] = t
        return int(entering.sum()), int(revisit.sum())

    def seen_walk_m2(self) -> float:
        return float(np.sum((self.last > -1e8) & self.walk)) * self.CELL ** 2

    def encode(self, t: float, cell_div: int = 2) -> dict:
        """Age of the last look per 4 m cell for the page: 0 never, else 1 + seconds ago (capped at 254)."""
        k = cell_div
        ny, nx = self.ny // k, self.nx // k
        last = self.last[:ny * k, :nx * k].reshape(ny, k, nx, k).max(axis=(1, 3))
        age = np.where(last > -1e8, np.clip(t - last, 0, 253) + 1, 0).astype(np.uint8)
        import base64
        return {"nx": nx, "ny": ny, "cell": self.CELL * k, "x0": self.x0, "y0": self.y0,
                "data": base64.b64encode(age[::-1].tobytes()).decode()}


@dataclass
class Pick:
    target: np.ndarray  # what to look at
    viewpoint: np.ndarray  # where to fly
    value: float
    score: float
    t_set: float
    label: str
    pitch: float = -50.0
    kind: str = "street"  # street | direct | overhead


class Planner:
    BACK_M = 25.0
    HORIZONS = (0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 21.0, 27.0)  # seconds ahead, run forward in 3 s steps
    STEP_S = 3.0

    def __init__(self, alt: float, pitch: float, speed: float = 13.0) -> None:
        self.alt, self.pitch, self.speed = alt, pitch, speed
        self.pick: Pick | None = None
        self.visited: list[tuple[np.ndarray, float]] = []
        self.arrived_at: float | None = None
        self.picks = 0
        self._n = 0

    def _travel(self, D: np.ndarray, V: np.ndarray) -> float:
        return float(np.linalg.norm(V - D)) / self.speed + 2.5

    def _snapshots(self, belief: Belief, drone_xy) -> list[tuple[np.ndarray, np.ndarray]]:
        """Belief positions (running particles only; hidden ones are not visible) at each planning horizon."""
        self._n += 1
        b = belief.clone(int(belief.rng.integers(1 << 31)))
        out, t = [], 0.0
        for h in self.HORIZONS:
            while t < h - 1e-9:
                b.predict(self.STEP_S, drone_xy)
                t += self.STEP_S
            out.append((b.positions(), b.w * (b.mode == RUN)))
        return out

    def _snap(self, snaps, tau: float):
        i = int(np.argmin([abs(h - tau) for h in self.HORIZONS]))
        return snaps[i]

    def _cover(self, pos: np.ndarray, w: np.ndarray, V: np.ndarray, T: np.ndarray, pitch: float) -> float:
        hd = heading_from_vector(*(T - V)) if float(np.linalg.norm(T - V)) > 1.0 else 0.0
        fp = view_camera(V, self.alt, hd, pitch).footprint(YOLO_RANGE_M)
        return float(np.sum(w[in_polygon(pos, fp)]))

    def _evaluate(self, snaps, D: np.ndarray, V: np.ndarray, T: np.ndarray, pitch: float) -> tuple[float, float]:
        travel = self._travel(D, V)
        pos, w = self._snap(snaps, travel)
        val = self._cover(pos, w, V, T, pitch)
        if float(np.linalg.norm(V - D)) > 20.0:  # what the camera sweeps on the way there
            mid = (D + V) / 2
            pos2, w2 = self._snap(snaps, travel / 2)
            val += 0.5 * self._cover(pos2, w2, mid, V + (V - D) / max(float(np.linalg.norm(V - D)), 1e-6) * 30, self.pitch)
        if any(float(np.linalg.norm(V - v)) < 25.0 for v, _ in self.visited):
            val *= 0.35
        return val, val / (travel + 3.0)

    def _candidates(self, belief: Belief, snaps, D: np.ndarray, place_label) -> list[Pick]:
        st = belief.st
        out: list[Pick] = []
        seen: set[tuple[int, int]] = set()
        for h in (8.0, 16.0, 25.0):
            pos, w = self._snap(snaps, h)
            if w.sum() <= 0:
                continue
            keys = np.floor(pos / 24.0).astype(int)
            uniq, inv = np.unique(keys, axis=0, return_inverse=True)
            inv = inv.ravel()
            mass = np.bincount(inv, weights=w, minlength=len(uniq))
            for c in np.argsort(-mass)[:5]:
                key = (int(uniq[c][0]), int(uniq[c][1]))
                if mass[c] < 0.004 or key in seen:
                    continue
                seen.add(key)
                m = inv == c
                T = np.average(pos[m], axis=0, weights=w[m])
                e, _, _ = st.project(T, lane=False)
                along = st.dir[e]
                for V, kind in ((T - along * self.BACK_M, "street"), (T + along * self.BACK_M, "street"),
                                (self._direct(D, T), "direct")):
                    out.append(Pick(T, V, 0.0, 0.0, 0.0, place_label(T), self.pitch, kind))
        # people who may be crouching behind a truck or a kiosk: look from straight above
        hid = (belief.mode == HIDE) & belief.spot_behind[np.maximum(belief.spot, 0)]
        if hid.any():
            mass = np.bincount(belief.spot[hid], weights=belief.w[hid], minlength=len(belief.spots))
            for k in np.argsort(-mass)[:3]:
                if mass[k] < 0.02:
                    continue
                T = belief.spot_xy[k]
                out.append(Pick(T.copy(), T.copy(), float(mass[k]), 0.0, 0.0, belief.spots[k].label, -88.0, "overhead"))
        return out

    def _direct(self, D: np.ndarray, T: np.ndarray) -> np.ndarray:
        d = T - D
        n = float(np.linalg.norm(d))
        return D.copy() if n <= self.BACK_M else T - d / n * self.BACK_M

    def update(self, t: float, drone_xy: np.ndarray, belief: Belief, place_label) -> Pick | None:
        D = np.asarray(drone_xy, float)
        snaps = self._snapshots(belief, D)
        self.visited = [(v, tv) for v, tv in self.visited if t - tv < 40.0]
        best: Pick | None = None
        for c in self._candidates(belief, snaps, D, place_label):
            if c.kind == "overhead":
                travel = self._travel(D, c.viewpoint)
                c.score = 0.9 * c.value / (travel + 3.0)
            else:
                c.value, c.score = self._evaluate(snaps, D, c.viewpoint, c.target, c.pitch)
            c.t_set = t
            if best is None or c.score > best.score:
                best = c
        cur = self.pick
        if cur is not None:
            if cur.kind == "overhead":
                hid = (belief.mode == HIDE) & (belief.spot >= 0)
                at = hid & (np.linalg.norm(belief.spot_xy[np.maximum(belief.spot, 0)] - cur.target, axis=1) < 1.0)
                cur_val = float(belief.w[at].sum())
                cur_score = 0.9 * cur_val / (self._travel(D, cur.viewpoint) + 3.0)
            else:
                cur_val, cur_score = self._evaluate(snaps, D, cur.viewpoint, cur.target, cur.pitch)
            reached = float(np.linalg.norm(cur.viewpoint - D)) < 6.0
            if reached and self.arrived_at is None:
                self.arrived_at = t
            looked = reached and self.arrived_at is not None and t - self.arrived_at > 2.0
            keep = not looked and cur_val >= 0.25 * cur.value and (best is None or best.score < 1.6 * cur_score)
            if keep:
                return cur
            if reached:
                self.visited.append((cur.viewpoint.copy(), t))
        if best is not None and best.value <= 1e-6:
            best = None
        if best is not None:
            self.picks += 1
        self.pick = best
        self.arrived_at = None
        return best
