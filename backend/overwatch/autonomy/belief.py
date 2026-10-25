"""Street-graph particle belief: where a lost subject could be, as particles moving along the street map.

When the followed subject is lost, particles are spawned on the street graph at the last observed position, with
speeds drawn around the last observed speed and headings along the street the subject was moving on. Each belief
tick moves every particle along its street; at an intersection it picks the next street with turn probabilities
(straight 0.6, left 0.18, right 0.18, reverse 0.04) multiplied by a street weight: cover (street trees, roofs) raises
it, a street heading towards the drone lowers it. Where the drone's camera looks and sees nobody, particles lose
weight (negative information); under cover they keep most of it, because the camera could not have seen them there.

There is one particle set per identity hypothesis held by the object memory: the subject continuing unseen, and any
look-alike seen after the loss who could be the subject. The mixture weights come from the memory's evidence
(appearance similarity, reachability), from where the camera has looked, and from the operator's clothing
description (``apply_description``). Operator text reaches routing only this way, as a likelihood over hypotheses;
it is never passed to a model.

numpy only. This is a read model for routing: it never commands the aircraft.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

TURN_P = {"straight": 0.60, "left": 0.18, "right": 0.18, "reverse": 0.04}
N_PER_HYPOTHESIS = 500
MAX_SUBJECT_MPS = 5.0
COVER_GAIN = 1.5  # a fully covered street is (1 + gain) times as likely as an open one
TOWARD_DRONE = 0.5  # a street heading towards the drone (an evading person avoids it)
TOWARD_DRONE_RANGE_M = 90.0
# Detection while a particle is inside the camera footprint, as a rate (repeated frames are not independent looks):
# on an open street the camera sees a person within about a second; under trees or a roof it rarely does.
DETECT_RATE_OPEN = 1.6  # per second in view: 80% within 1 s
DETECT_RATE_COVERED = 0.025  # per second in view: about 10% over a 4 s look (from above, under a roof)
DWELL_LOOK_S = 4.0  # how long a viewpoint is watched (the re-plan interval and a little more)
NEAR_PERSON_M = 3.0  # particles this close to a detected person are not "seen empty"
START_MOVING_PER_S = 0.15  # a stopped (hiding) particle moves on at this rate (mean stop about 7 s)
STOP_PER_S_OPEN = 0.05  # a moving particle stops at this rate on an open street ...
STOP_PER_S_COVERED = 0.08  # ... and more often under cover, where a person evading a drone would hide
COVER_STEP_M = 2.0
MIN_HYPOTHESIS_WEIGHT = 0.005  # below this a hypothesis is dropped (ruled out by search or evidence)


def turn_type(d_in: np.ndarray, d_out: np.ndarray, is_reverse: bool) -> str:
    if is_reverse:
        return "reverse"
    ang = math.degrees(math.atan2(d_in[0] * d_out[1] - d_in[1] * d_out[0], float(np.dot(d_in, d_out))))
    if abs(ang) < 35.0:
        return "straight"
    if abs(ang) > 150.0:
        return "reverse"
    return "left" if ang > 0 else "right"  # x east, y north: counter-clockwise is a left turn


def points_in_poly(pts: np.ndarray, poly: list[tuple[float, float]] | np.ndarray | None) -> np.ndarray:
    """Even-odd rule for many points at once (N x 2) against one polygon."""
    if poly is None or len(poly) < 3 or len(pts) == 0:
        return np.zeros(len(pts), bool)
    P = np.asarray(poly, float)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    xi, yi = P[:, 0][None, :], P[:, 1][None, :]
    xj, yj = np.roll(P[:, 0], 1)[None, :], np.roll(P[:, 1], 1)[None, :]
    cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / np.where(yj - yi == 0, 1e-12, yj - yi) + xi)
    return (cross.sum(axis=1) % 2) == 1


class StreetGraph:
    """Directed-edge arrays over street map data: ``{nodes: {id: (x, y)}, edges: [(a, b)], names?, cover?}``.

    Directed edge ``2k`` runs a->b of undirected edge ``k``, ``2k + 1`` runs b->a."""

    def __init__(self, streets: dict) -> None:
        self.source = streets.get("source", "map data")
        self.names = list(streets["nodes"])
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.xy = np.array([streets["nodes"][n] for n in self.names], float)
        und = [(self.idx[a], self.idx[b]) for a, b in streets["edges"] if a in self.idx and b in self.idx and a != b]
        self.und = und
        self.und_key = [f"{self.names[a]}|{self.names[b]}" for a, b in und]
        self.und_of_key = {k: i for i, k in enumerate(self.und_key)}
        frm, to = [], []
        for a, b in und:
            frm += [a, b]
            to += [b, a]
        self.frm = np.array(frm, int)
        self.to = np.array(to, int)
        d = self.xy[self.to] - self.xy[self.frm]
        self.length = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
        self.dir = d / self.length[:, None]
        self.E = len(frm)
        out_of: dict[int, list[int]] = defaultdict(list)
        for e in range(self.E):
            out_of[int(self.frm[e])].append(e)
        self.succ = [np.array(out_of[int(self.to[e])], int) for e in range(self.E)]
        self.turn = [[turn_type(self.dir[e], self.dir[e2], e2 == (e ^ 1)) for e2 in self.succ[e]] for e in range(self.E)]
        base = []
        for e in range(self.E):
            p = np.array([TURN_P[t] for t in self.turn[e]], float)
            base.append(p / p.sum() if p.sum() > 0 else p)
        self.base_p = base
        # cover profile along each directed edge (fraction of the street under trees or roofs), sampled every 2 m
        cover = streets.get("cover") or {}
        K = int(np.ceil(self.length.max() / COVER_STEP_M)) + 1 if self.E else 1
        prof = np.zeros((max(self.E, 1), K), float)
        for k, key in enumerate(self.und_key):
            c = np.asarray(cover.get(key) or [], float)
            if c.size:
                n = min(K, c.size)
                prof[2 * k, :n] = c[:n]
                # the reverse direction reads the same profile backwards from its own start
                L = self.length[2 * k]
                s = np.arange(K) * COVER_STEP_M
                back = np.clip(((L - s) / COVER_STEP_M).astype(int), 0, c.size - 1)
                prof[2 * k + 1] = np.where(s <= L, c[back], 0.0)
        self.cover_prof = prof
        self.cover_mean = np.array([float(prof[e, :max(1, int(self.length[e] / COVER_STEP_M) + 1)].mean())
                                    for e in range(self.E)]) if self.E else np.zeros(0)
        self.edge_names = streets.get("names") or {}
        self.node_labels = streets.get("node_labels") or {}

    # -- geometry ---------------------------------------------------------------------------------------------
    def positions(self, e: np.ndarray, s: np.ndarray) -> np.ndarray:
        return self.xy[self.frm[e]] + self.dir[e] * s[:, None]

    def cover_at(self, e: np.ndarray, s: np.ndarray) -> np.ndarray:
        k = np.clip((s / COVER_STEP_M).astype(int), 0, self.cover_prof.shape[1] - 1)
        return self.cover_prof[e, k]

    def snap(self, xy: tuple[float, float]) -> tuple[int, float, float]:
        """Nearest point on any street: (undirected edge index, offset from its a end, distance off the street)."""
        p = np.asarray(xy, float)
        A = self.xy[self.frm[0::2]]
        d = self.dir[0::2]
        L = self.length[0::2]
        t = np.clip(((p - A) * d).sum(axis=1), 0.0, L)
        q = A + d * t[:, None]
        dist = np.hypot(q[:, 0] - p[0], q[:, 1] - p[1])
        k = int(np.argmin(dist))
        return k, float(t[k]), float(dist[k])

    def label_node(self, n: int | str) -> str:
        name = self.names[n] if isinstance(n, (int, np.integer)) else n
        return self.node_labels.get(name, f"intersection {name}")

    def label_edge(self, k: int) -> str:
        key = self.und_key[k]
        if key in self.edge_names:
            return self.edge_names[key]
        a, b = key.split("|")
        return f"street {a} to {b}"

    # -- routing ----------------------------------------------------------------------------------------------
    def route(self, start_xy: tuple[float, float], goal_xy: tuple[float, float], node_ok: np.ndarray | None = None,
              join_m: float = 15.0) -> tuple[list[tuple[float, float]], float] | None:
        """Shortest path along the streets from a point to a point (both snapped to their nearest street). Nodes with
        ``node_ok`` False (outside the geofence) are never used. Returns (waypoints, length) or None."""
        import heapq

        ks, ts, ds = self.snap(start_xy)
        kg, tg, _ = self.snap(goal_xy)
        n = len(self.names)
        ok = np.ones(n, bool) if node_ok is None else node_ok
        a_s, b_s = self.und[ks]
        a_g, b_g = self.und[kg]
        Ls, Lg = float(self.length[2 * ks]), float(self.length[2 * kg])
        S, G = n, n + 1  # virtual start and goal nodes on their streets
        adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for k, (a, b) in enumerate(self.und):
            if ok[a] and ok[b]:
                L = float(self.length[2 * k])
                adj[a].append((b, L))
                adj[b].append((a, L))
        for v, w in ((a_s, ts), (b_s, Ls - ts)):
            if ok[v]:
                adj[S].append((v, w))
        for v, w in ((a_g, tg), (b_g, Lg - tg)):
            if ok[v]:
                adj[v].append((G, w))
        if ks == kg:
            adj[S].append((G, abs(tg - ts)))
        dist = {S: 0.0}
        prev: dict[int, int] = {}
        pq = [(0.0, S)]
        while pq:
            du, u = heapq.heappop(pq)
            if u == G:
                break
            if du > dist.get(u, math.inf):
                continue
            for v, w in adj.get(u, []):
                nd = du + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
        if G not in dist:
            return None
        chain, u = [], G
        while u != S:
            u = prev[u]
            if u != S:
                chain.append(u)
        chain.reverse()
        pts: list[tuple[float, float]] = []
        start_pt = self.xy[a_s] + self.dir[2 * ks] * ts
        if ds > join_m:
            pts.append((float(start_pt[0]), float(start_pt[1])))
        pts += [(float(self.xy[v][0]), float(self.xy[v][1])) for v in chain]
        goal_pt = self.xy[a_g] + self.dir[2 * kg] * tg
        pts.append((float(goal_pt[0]), float(goal_pt[1])))
        return pts, float(dist[G]) + ds


@dataclass
class Hypothesis:
    """One identity hypothesis: "the subject is this remembered person" (or "the subject continued unseen")."""

    hid: str
    label: str
    object_id: str | None
    track_id: str | None
    xy: tuple[float, float]
    vel: tuple[float, float]
    prior: float
    source: str  # where the prior came from (memory evidence, D2 probability)
    colors: tuple[str, str] | None = None
    hist: np.ndarray | None = None
    err_m: float = 1.5
    momentum: tuple[float, float] | None = None  # the last clearly moving velocity, when they had just stopped
    created_ms: float = 0.0
    logw: float = 0.0
    visible: bool = False
    evidence: list[dict[str, Any]] = field(default_factory=list)


class StreetBelief:
    """Particles on the street graph, one set per identity hypothesis, with mixture weights over hypotheses."""

    def __init__(self, graph: StreetGraph, started_ms: float, lost_ms: float, n_per: int = N_PER_HYPOTHESIS,
                 seed: int = 0) -> None:
        self.g = graph
        self.started_ms = started_ms
        self.lost_ms = lost_ms  # when the subject was last observed
        self.n_per = n_per
        self.rng = np.random.default_rng(seed)
        self.hyps: list[Hypothesis] = []
        self.e = np.zeros(0, int)
        self.s = np.zeros(0, float)
        self.v = np.zeros(0, float)
        self.vr = np.zeros(0, float)  # each particle's running speed, kept while it is stopped
        self.k = np.zeros(0, int)
        self.w = np.zeros(0, float)  # within-hypothesis weights (each set sums to 1)
        self.drone_xy: tuple[float, float] | None = None
        self.ticks = 0
        self.description: dict[str, Any] | None = None
        self._cand_ids: dict[str, str] = {}
        self.dropped: list[dict[str, Any]] = []
        self.rejected: set[str] = set()  # objects a person said are not the subject: never a hypothesis again here

    # -- hypotheses -------------------------------------------------------------------------------------------
    def weights(self) -> np.ndarray:
        if not self.hyps:
            return np.zeros(0)
        lw = np.array([h.logw for h in self.hyps])
        p = np.exp(lw - lw.max())
        return p / p.sum()

    def hyp_weights(self) -> dict[str, float]:
        return {h.hid: float(p) for h, p in zip(self.hyps, self.weights())}

    def add(self, h: Hypothesis, now_ms: float) -> Hypothesis:
        """Add a hypothesis with its prior relative to the subject-continued prior (1.0); the description in force,
        if any, applies to it too."""
        h.created_ms = now_ms
        h.logw = math.log(max(h.prior, 1e-6))
        h.evidence.append({"kind": "prior", "value": round(h.prior, 3), "why": h.source, "ts_ms": now_ms})
        if self.description is not None:
            from ..vision.appearance import descriptor_likelihood

            lk = descriptor_likelihood(self.description["descriptor"], h.hist, h.colors)
            h.logw += math.log(lk)
            h.evidence.append({"kind": "description", "likelihood": round(lk, 3), "provenance": "manual", "ts_ms": now_ms})
        self.hyps.append(h)
        self._spawn(len(self.hyps) - 1)
        return h

    def find(self, object_id: str | None) -> Hypothesis | None:
        return next((h for h in self.hyps if object_id is not None and h.object_id == object_id), None)

    def apply_description(self, descriptor: Any, text: str, now_ms: float, by: str = "operator") -> dict[str, float]:
        """The operator's clothing description as evidence: each hypothesis is weighted by how well its clothing
        matches. A new description replaces the previous one (its likelihoods are taken back first)."""
        from ..vision.appearance import descriptor_likelihood

        out = {}
        for h in self.hyps:
            old = next((ev for ev in reversed(h.evidence) if ev["kind"] == "description" and ev.get("active", True)), None)
            if old is not None:
                h.logw -= math.log(old["likelihood"])
                old["active"] = False
            if descriptor is None:
                continue
            lk = descriptor_likelihood(descriptor, h.hist, h.colors)
            h.logw += math.log(lk)
            h.evidence.append({"kind": "description", "likelihood": round(lk, 3), "provenance": "manual",
                               "text": text, "by": by, "ts_ms": now_ms})
            out[h.hid] = round(lk, 3)
        self.description = None if descriptor is None else {"descriptor": descriptor, "text": text, "by": by,
                                                             "ts_ms": now_ms, "likelihoods": out}
        return out

    def drop(self, hid: str, why: str, now_ms: float) -> None:
        i = next((j for j, h in enumerate(self.hyps) if h.hid == hid), None)
        if i is None or len(self.hyps) == 1:
            return
        self.dropped.append({"hid": hid, "label": self.hyps[i].label, "object_id": self.hyps[i].object_id, "why": why,
                             "ts_ms": now_ms})
        keep = self.k != i
        self.e, self.s, self.v, self.vr = self.e[keep], self.s[keep], self.v[keep], self.vr[keep]
        self.w, self.k = self.w[keep], self.k[keep]
        self.k[self.k > i] -= 1
        del self.hyps[i]

    # -- particles --------------------------------------------------------------------------------------------
    def _spawn(self, i: int, n: int | None = None) -> None:
        """(Re)place hypothesis i's particles at its position with speeds around its velocity."""
        h = self.hyps[i]
        n = n or self.n_per
        g = self.g
        kq, t0, _ = g.snap(h.xy)
        vx, vy = h.vel
        speed = math.hypot(vx, vy)
        mom = h.momentum
        rng = self.rng
        u = g.dir[2 * kq]
        along = float(np.dot((vx, vy), u))
        if speed >= 0.4:  # moving: along the street in the direction of travel
            fwd = np.full(n, along >= 0)
            run = np.clip(rng.normal(speed, 0.2 * speed + 0.25, n), 0.3, MAX_SUBJECT_MPS)
            still = np.zeros(n, bool)
        elif mom is not None and math.hypot(*mom) >= 0.8:  # had just stopped (hiding?) after running: momentum
            m_along = float(np.dot(mom, u))
            ms = math.hypot(*mom)
            fwd = np.where(rng.random(n) < 0.85, m_along >= 0, m_along < 0)
            run = np.clip(rng.normal(ms, 0.2 * ms + 0.25, n), 0.3, MAX_SUBJECT_MPS)
            still = rng.random(n) < 0.3
        else:  # no motion to go on: half still, half walking either way
            fwd = rng.random(n) < 0.5
            run = np.clip(rng.normal(1.4, 0.4, n), 0.3, MAX_SUBJECT_MPS)
            still = rng.random(n) < 0.5
        v = np.where(still, 0.0, run)
        e = np.where(fwd, 2 * kq, 2 * kq + 1)
        L = g.length[2 * kq]
        s_ab = np.clip(t0 + rng.normal(0.0, max(0.5, h.err_m), n), 0.0, L)
        s = np.where(fwd, s_ab, L - s_ab)
        vr = run
        keep = self.k != i
        self.e = np.concatenate([self.e[keep], e])
        self.s = np.concatenate([self.s[keep], s])
        self.v = np.concatenate([self.v[keep], v])
        self.vr = np.concatenate([self.vr[keep], vr])
        self.w = np.concatenate([self.w[keep], np.full(n, 1.0 / n)])
        self.k = np.concatenate([self.k[keep], np.full(n, i)])

    def _dyn_weight(self, e2: np.ndarray, node: int) -> np.ndarray:
        w = 1.0 + COVER_GAIN * self.g.cover_mean[e2]
        if self.drone_xy is not None:
            to_drone = np.asarray(self.drone_xy) - self.g.xy[node]
            dd = float(np.hypot(*to_drone))
            if 1.0 < dd < TOWARD_DRONE_RANGE_M:
                cos = (self.g.dir[e2] @ (to_drone / dd))
                w = np.where(cos > math.cos(math.radians(45)), w * TOWARD_DRONE, w)
        return w

    def _advance(self, e: np.ndarray, s: np.ndarray, v: np.ndarray, dt: float, rng: np.random.Generator,
                 vr: np.ndarray | None = None) -> None:
        """Move particles ``dt`` seconds along the streets: speed jitter, stops (more likely under cover) and
        restarts at the particle's own running speed, and a turn choice at every intersection reached."""
        g = self.g
        vr = self.vr if vr is None else vr
        moving = v > 0
        vr[moving] = np.clip(vr[moving] + rng.normal(0.0, 0.18 * math.sqrt(dt), int(moving.sum())), 0.2, MAX_SUBJECT_MPS)
        cov = g.cover_at(e, s)
        p_stop = 1.0 - np.exp(-(STOP_PER_S_OPEN * (1.0 - cov) + STOP_PER_S_COVERED * cov) * dt)
        stop = moving & (rng.random(len(v)) < p_stop)
        start = (~moving) & (rng.random(len(v)) < 1.0 - math.exp(-START_MOVING_PER_S * dt))
        v[:] = np.where(stop, 0.0, np.where(moving | start, vr, 0.0))
        s += v * dt
        for _ in range(12):
            over = np.nonzero(s >= g.length[e])[0]
            if over.size == 0:
                break
            s[over] -= g.length[e[over]]
            for ed in np.unique(e[over]):
                rows = over[e[over] == ed]
                nxt = g.succ[ed]
                if nxt.size == 0:  # dead end: turn back
                    e[rows] = ed ^ 1
                    continue
                p = g.base_p[ed] * self._dyn_weight(nxt, int(g.to[ed]))
                p = p / p.sum()
                e[rows] = nxt[rng.choice(nxt.size, size=rows.size, p=p)]
        np.minimum(s, g.length[e], out=s)

    def step(self, dt: float, drone_xy: tuple[float, float] | None, seen_poly: list | None,
             people_xy: list[tuple[float, float]], visible: dict[str, tuple[tuple[float, float], tuple[float, float], float]],
             now_ms: float, explained: set[str] | None = None) -> dict[str, float]:
        """Advance ``dt`` seconds. ``visible``: hypothesis id -> (xy, velocity, error) of its object seen right now
        (its particles are re-placed there). Particles of the other hypotheses inside ``seen_poly`` (the camera's
        current footprint) and away from every detected person lose weight by the chance the camera would have
        seen the subject there. ``explained``: hypotheses whose person just walked out of view (the object memory
        explains the absence): that person exists whichever hypothesis is true, so their leaving the view moves
        their cloud but does not change the hypothesis's weight. Returns how much of each hypothesis's mass
        survived the look."""
        self.ticks += 1
        self.drone_xy = drone_xy
        if not self.hyps:
            return {}
        self._advance(self.e, self.s, self.v, dt, self.rng)
        for i, h in enumerate(self.hyps):
            vis = visible.get(h.hid)
            h.visible = vis is not None
            if vis is not None:
                h.xy, h.vel, h.err_m = vis[0], vis[1], max(0.5, vis[2])
                h.momentum = None
                self._spawn(i)
        survived: dict[str, float] = {}
        if seen_poly is not None and len(seen_poly) >= 3:
            xy = self.g.positions(self.e, self.s)
            inside = points_in_poly(xy, seen_poly)
            if people_xy:
                P = np.asarray(people_xy, float)
                near = (np.hypot(xy[:, None, 0] - P[None, :, 0], xy[:, None, 1] - P[None, :, 1]) < NEAR_PERSON_M).any(axis=1)
                inside &= ~near
            hidden_hyp = np.array([not h.visible for h in self.hyps])[self.k]
            inside &= hidden_hyp
            if inside.any():
                cov = self.g.cover_at(self.e, self.s)
                rate = DETECT_RATE_OPEN * (1.0 - cov) + DETECT_RATE_COVERED * cov
                lik = np.where(inside, np.exp(-rate * dt), 1.0)
                for i, h in enumerate(self.hyps):
                    m = self.k == i
                    z = float((self.w[m] * lik[m]).sum())
                    if z < 0.999999 and h.hid not in (explained or ()):
                        survived[h.hid] = round(z, 4)
                        h.logw += math.log(max(z, 1e-9))
                    if z > 0:
                        self.w[m] = self.w[m] * lik[m] / z
        self._prune(now_ms)
        self._resample()
        return survived

    def _prune(self, now_ms: float) -> None:
        if len(self.hyps) < 2:
            return
        for h, p in list(zip(self.hyps, self.weights())):
            if p < MIN_HYPOTHESIS_WEIGHT:
                self.drop(h.hid, "ruled out (searched, or does not fit the evidence)", now_ms)

    def _resample(self) -> None:
        for i in range(len(self.hyps)):
            m = np.nonzero(self.k == i)[0]
            if m.size == 0:
                continue
            w = self.w[m]
            ess = 1.0 / float((w ** 2).sum()) if w.sum() > 0 else 0.0
            if ess >= 0.5 * m.size:
                continue
            u = (self.rng.random() + np.arange(m.size)) / m.size
            pick = m[np.minimum(np.searchsorted(np.cumsum(w), u), m.size - 1)]
            self.e[m] = self.e[pick]
            self.s[m] = np.clip(self.s[pick] + self.rng.normal(0.0, 0.4, m.size), 0.0, self.g.length[self.e[pick]])
            self.vr[m] = np.clip(self.vr[pick] + self.rng.normal(0.0, 0.1, m.size), 0.2, MAX_SUBJECT_MPS)
            self.v[m] = np.where(self.v[pick] > 0, self.vr[m], 0.0)
            self.w[m] = 1.0 / m.size

    # -- read model ---------------------------------------------------------------------------------------------
    def particle_weights(self) -> np.ndarray:
        """Absolute weight of every particle in the mixture (sums to 1)."""
        pi = self.weights()
        return pi[self.k] * self.w if len(pi) else np.zeros(0)

    def positions(self) -> np.ndarray:
        return self.g.positions(self.e, self.s)

    def mass_by_edge(self) -> dict[str, float]:
        pw = self.particle_weights()
        m = np.bincount(self.e // 2, weights=pw, minlength=len(self.g.und))
        return {self.g.und_key[k]: float(v) for k, v in enumerate(m) if v > 0}

    def mass_by_node(self, radius_m: float = 20.0) -> dict[str, float]:
        xy = self.positions()
        pw = self.particle_weights()
        d = np.hypot(xy[:, None, 0] - self.g.xy[None, :, 0], xy[:, None, 1] - self.g.xy[None, :, 1])
        m = ((d < radius_m) * pw[:, None]).sum(axis=0)
        return {self.g.names[i]: float(v) for i, v in enumerate(m) if v > 0}

    def detect_p(self, e: np.ndarray, s: np.ndarray, look_s: float = DWELL_LOOK_S) -> np.ndarray:
        """Chance the camera would see the subject at these particles while watching them for ``look_s``."""
        cov = self.g.cover_at(e, s)
        p_open = 1.0 - math.exp(-DETECT_RATE_OPEN * min(look_s, 1.0))  # once in the open, seen within the second
        return p_open * (1.0 - cov) + (1.0 - math.exp(-DETECT_RATE_COVERED * look_s)) * cov

    def predict(self, horizons_s: list[float], seed: int | None = None) -> dict[float, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """Where the particles would be after each horizon (no new observations): {t: (xy, absolute weights,
        directed edge, hypothesis index, detection chance)}. Deterministic for a given seed."""
        rng = np.random.default_rng(self.ticks * 7919 + 17 if seed is None else seed)
        e, s, v, vr = self.e.copy(), self.s.copy(), self.v.copy(), self.vr.copy()
        pw = self.particle_weights()
        out: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        t = 0.0
        for hz in sorted(horizons_s):
            while t < hz - 1e-6:
                step = min(0.5, hz - t)
                self._advance(e, s, v, step, rng, vr)
                t += step
            out[hz] = (self.g.positions(e, s), pw, e.copy(), self.k, self.detect_p(e, s))
        return out

    def candidate_id(self, key: str) -> str:
        """Stable ids for candidate viewpoints within one pursuit (P1 is the first one ever offered)."""
        if key not in self._cand_ids:
            self._cand_ids[key] = f"P{len(self._cand_ids) + 1}"
        return self._cand_ids[key]

    def sample(self, per_hypothesis: int = 160) -> list[tuple[float, float, int]]:
        """A weighted subsample of the particles for display: [(x, y, hypothesis index)]."""
        xy = self.positions()
        out: list[tuple[float, float, int]] = []
        rng = np.random.default_rng(self.ticks)
        for i in range(len(self.hyps)):
            m = np.nonzero(self.k == i)[0]
            if m.size == 0:
                continue
            w = self.w[m] / max(self.w[m].sum(), 1e-12)
            n = min(per_hypothesis, m.size)
            pick = m[rng.choice(m.size, size=n, replace=True, p=w)]
            out += [(round(float(xy[j, 0]), 1), round(float(xy[j, 1]), 1), i) for j in pick]
        return out
