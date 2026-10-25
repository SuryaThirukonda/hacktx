"""Pursuit routing: where a lost subject could have gone along the streets, as bounded drone routes.

Routing plans from a belief, not from a point (``coverage_candidates``): the street-graph particle belief
(``autonomy/belief.py``) says where the subject could be, one particle set per identity hypothesis. The candidate
viewpoints are every street holding more than 2% of the belief and the three intersections holding the most. Each is
scored by the belief mass the camera would cover from there when the drone arrives (the particles are moved on by the
flight time), minus a travel-time penalty and the safety supervisor's penalties; anything the supervisor rejects is
unsafe and removed before an engine is asked. The route is the shortest safe path along the streets to the chosen
viewpoint. D12 then chooses among the top three, with the argmax as the rules baseline.

``route_options`` below is the earlier point-based version (routes from the last-seen point and velocity only); the
pipeline no longer uses it. It computed the candidate routes this way (the decision engine only chose among them):

* along the street the subject was on, to where they would be by now at their last measured speed;
* each branch at the next intersection ahead (straight, left, right), to the same reach;
* an orbit of the last-seen position (hiding: a carport, trees, a doorway);
* the last sighting of a remembered person who matches the operator's "look for" description (object memory);
* holding and handing the search to the operator (always offered).

Each option carries its computed length, flight time and the share of predicted positions it covers; any route that
leaves the geofence is marked unsafe and removed before the engine is asked. The street graph is map data (in the
simulator the world's streets; for a real incident an OpenStreetMap extract). Routes are flown only in the simulator
or as advisories: real flight stays manual.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .safety import inside

WALK_MPS = 1.4
MAX_SUBJECT_MPS = 4.5


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def snap(streets: dict, xy: tuple[float, float]) -> tuple[tuple[str, str], np.ndarray, float] | None:
    """Nearest street segment to a point: ((a, b), the point on it, distance off the street)."""
    p = np.array(xy, float)
    best = None
    for a, b in streets["edges"]:
        A, B = np.array(streets["nodes"][a], float), np.array(streets["nodes"][b], float)
        ab = B - A
        t = float(np.clip(np.dot(p - A, ab) / max(float(np.dot(ab, ab)), 1e-9), 0.0, 1.0))
        q = A + t * ab
        d = float(np.linalg.norm(p - q))
        if best is None or d < best[2]:
            best = ((a, b), q, d)
    return best


def _neighbours(streets: dict, n: str) -> list[str]:
    out = []
    for a, b in streets["edges"]:
        if a == n:
            out.append(b)
        elif b == n:
            out.append(a)
    return out


def _turn_name(incoming: np.ndarray, outgoing: np.ndarray) -> str:
    ang = math.degrees(math.atan2(incoming[0] * outgoing[1] - incoming[1] * outgoing[0], float(np.dot(incoming, outgoing))))
    if abs(ang) < 35:
        return "straight on"
    return "left" if ang > 0 else "right"


def _path_len(points: list[np.ndarray]) -> float:
    return float(sum(np.linalg.norm(b - a) for a, b in zip(points, points[1:])))


def route_options(streets: dict | None, last_xy: tuple[float, float] | None, vel: tuple[float, float] | None,
                  lost_s: float, drone_xy: tuple[float, float] | None, drone_speed_mps: float = 6.0,
                  geofence: list | None = None, sightings: list[dict] | None = None,
                  prior_vel: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    """Candidate routes, each {id, kind, description, waypoints, length_m, eta_s, share, unsafe}.

    ``vel``: the subject's observed velocity just before the loss; ``prior_vel``: their last clearly moving velocity
    in the seconds before (momentum: someone who sprinted, then stopped out of sight, most likely runs on the same
    way). Speeds come from observed positions, never from a coasting filter."""
    if last_xy is None or drone_xy is None:
        return []
    drone = np.array(drone_xy, float)
    last = np.array(last_xy, float)
    v = np.array(vel or (0.0, 0.0), float)
    pv = np.array(prior_vel or (0.0, 0.0), float)
    speed = float(np.linalg.norm(v))
    moving = speed > 0.4
    heading = v if moving else (pv if float(np.linalg.norm(pv)) > 0.8 else None)
    run_speed = max(speed, float(np.linalg.norm(pv)), WALK_MPS)
    reach = min(MAX_SUBJECT_MPS, run_speed) * max(lost_s, 1.0) + 3.0
    hide = 0.45 if (not moving and heading is None) or lost_s < 4.0 else 0.25  # still, or only just gone: close by
    raw: list[dict[str, Any]] = []
    sn = snap(streets, last_xy) if streets else None
    if sn is not None and sn[2] < 25.0:
        (a, b), on, _ = sn
        ends = [a, b]
        end_w = {a: 1.0, b: 1.0}
        if heading is not None:  # the end the subject was heading towards; the other only if they had stopped
            ahead = [e for e in ends if np.dot(np.array(streets["nodes"][e]) - on, heading) > 0] or ends
            if moving:
                ends = ahead
            else:
                end_w = {e: (0.75 if e in ahead else 0.25) for e in ends}
        for e in ends:
            E = np.array(streets["nodes"][e], float)
            d_end = float(np.linalg.norm(E - on))
            incoming = _unit(E - on)
            if reach <= d_end:  # still on this street
                tgt = on + incoming * reach
                raw.append({"kind": "street", "description": f"Fly along the street towards {e}, to where the subject "
                            f"would be at {run_speed:.1f} m/s", "waypoints": [tgt], "weight": end_w[e]})
                continue
            other = b if e == a else a
            branches = [n for n in _neighbours(streets, e) if n != other]
            for n in branches:
                N = np.array(streets["nodes"][n], float)
                out = _unit(N - E)
                turn = _turn_name(incoming, out)
                rem = min(reach - d_end, float(np.linalg.norm(N - E)))
                tgt = E + out * rem
                w = (0.5 if turn == "straight on" else 0.25) * end_w[e]
                raw.append({"kind": "street", "description": f"At intersection {e}, check {turn}",
                            "waypoints": [E, tgt], "weight": w})
    raw.append({"kind": "orbit", "description": "Orbit the last-seen position (hiding nearby: carport, trees, doorway)",
                "waypoints": [last], "weight": None})
    for s in sightings or []:
        raw.append({"kind": "sighting", "description": f"Check the last sighting of {s['object_id']} "
                    f"({s['match']}, {s['since_seen_s']:.0f} s ago)", "waypoints": [np.array(s["xy"], float)],
                    "weight": None, "object_id": s["object_id"]})
    street_w = sum(r["weight"] for r in raw if r["kind"] == "street") or 1.0
    n_sight = sum(1 for r in raw if r["kind"] == "sighting")
    out = []
    for i, r in enumerate(raw):
        if r["kind"] == "street":
            share = (1.0 - hide - 0.15 * bool(n_sight)) * r["weight"] / street_w
        elif r["kind"] == "orbit":
            share = hide
        else:
            share = 0.15 / n_sight
        pts = [drone] + list(r["waypoints"])
        length = _path_len(pts)
        unsafe = None
        if geofence and any(not inside(geofence, float(p[0]), float(p[1])) for p in r["waypoints"]):
            unsafe = "leaves the geofence"
        out.append({"id": f"P{i + 1}", "kind": r["kind"], "description": r["description"],
                    "waypoints": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in r["waypoints"]],
                    "length_m": round(length, 1), "eta_s": round(length / max(drone_speed_mps, 0.5), 1),
                    "share": round(share, 3), "unsafe": unsafe, **({"object_id": r["object_id"]} if "object_id" in r else {})})
    return out


def describes(text: str, colors: tuple[str, str] | None) -> float:
    """How well clothing colours match an operator's description (words only; no faces, no identity)."""
    if not text or not colors:
        return 0.0
    words = set(text.lower().replace(",", " ").split())
    score = 0.0
    if colors[0] in words:
        score += 1.0
    if colors[1] in words:
        score += 0.5
    if "jeans" in words and colors[1] == "blue":
        score += 0.5
    return score


# ---------------------------------------------------------------------------------------------------------------
# Belief-based routing
# ---------------------------------------------------------------------------------------------------------------
EDGE_MIN_MASS = 0.02  # a street is a candidate when it holds more than 2% of the belief
TOP_NODES = 3
TRAVEL_PENALTY_PER_S = 0.004  # 0.4% of the belief per second of flight: equal coverage favours the closer viewpoint
HORIZONS_S = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0]
PLAN_HORIZON_S = 10.0  # every viewpoint is scored over the same window: from its arrival to 10 s from now
MIN_WATCH_S = 2.0  # (a viewpoint further than the horizon is still watched for one re-plan interval)
LOOK_PITCH_DEG = -55.0  # the gimbal while a route is flown and at its viewpoint (autonomy/controller.py)


def look_back_m(alt_m: float, pitch_deg: float = LOOK_PITCH_DEG) -> float:
    """How far behind a target the drone hovers so the centre of the camera view is on it."""
    return alt_m / math.tan(math.radians(abs(pitch_deg)))


def viewpoint_footprint(target_xy: tuple[float, float], approach_xy: tuple[float, float], alt_m: float,
                        intr: Any, pitch_deg: float = LOOK_PITCH_DEG) -> list[tuple[float, float]] | None:
    """The ground the camera would see looking at ``target_xy`` from ``alt_m``, coming from ``approach_xy`` (the
    drone stands back so the footprint is centred on the target): the existing pinhole footprint projection."""
    from ..geometry.camera import CameraPose, PinholeCamera, heading_from_vector

    t = np.asarray(target_xy, float)
    u = t - np.asarray(approach_xy, float)
    n = float(np.linalg.norm(u))
    u = u / n if n > 1e-6 else np.array([0.0, 1.0])
    pos = t - u * look_back_m(alt_m, pitch_deg)
    cam = PinholeCamera(intr, CameraPose(np.array([pos[0], pos[1], alt_m]), heading_from_vector(float(u[0]), float(u[1])),
                                         pitch_deg))
    return cam.footprint(max_range_m=60.0)


COMMIT_BONUS = 0.03  # the viewpoint already being flown keeps a small edge, so re-plans do not chase noise
STREET_LOOK_POINTS = (0.12, 0.3, 0.5, 0.7, 0.88)  # look points tried along a candidate street (fractions of it)


def coverage_candidates(belief: Any, drone_xy: tuple[float, float], alt_m: float, intr: Any, speed_mps: float,
                        supervisor: Any, people_xy: list[tuple[float, float]], fence: list | None = None,
                        subject_edge: str | None = None, current_key: str | None = None) -> list[dict[str, Any]]:
    """Candidate viewpoints from the belief, best first: {id, kind, key, label, target_xy, hover_xy, waypoints,
    length_m, eta_s, coverage, by_hypothesis, penalty, commitment, score, unsafe, why}.

    ``coverage`` is the share of the whole belief the camera would see from the viewpoint: the mass inside its
    footprint from arrival to the planning horizon (the particles moved on by the flight time; a far viewpoint gets
    less watching time), each particle counted by the chance of seeing the subject there (low under trees or a roof).
    ``by_hypothesis`` splits it. For a street, the look point and direction along it with the best score is used."""
    from .belief import points_in_poly
    from .safety import fence_margin

    g = belief.g
    mass_e = belief.mass_by_edge()
    mass_n = belief.mass_by_node()
    hyps = belief.hyps
    raw: list[tuple[str, str, float]] = []  # (kind, key, mass now)
    for key, m in sorted(mass_e.items(), key=lambda kv: -kv[1]):
        if m > EDGE_MIN_MASS:
            raw.append(("street", key, m))
    for name, m in sorted(mass_n.items(), key=lambda kv: -kv[1])[:TOP_NODES]:
        if m > 0.01:
            raw.append(("intersection", name, m))
    if not raw:
        return []
    pred = belief.predict(HORIZONS_S)
    node_ok = None
    if fence:
        node_ok = np.array([fence_margin(fence, float(x), float(y)) >= 2.0 for x, y in g.xy])
    back = look_back_m(alt_m)
    pw0, kk0 = pred[0.0][1], pred[0.0][3]

    def evaluate(t: tuple[float, float], u: np.ndarray) -> dict[str, Any]:
        """Look at ``t`` along the street direction ``u``: hover ``back`` metres behind it, camera facing ``u``."""
        hover = (t[0] - float(u[0]) * back, t[1] - float(u[1]) * back)
        r = g.route(drone_xy, hover, node_ok)
        path, length = r if r is not None else ([hover], float(math.dist(drone_xy, hover)))
        eta = length / max(speed_mps, 0.5)
        fp = viewpoint_footprint(t, hover, alt_m, intr)
        seen = np.zeros(len(pw0))
        for tw in np.arange(eta, max(eta + MIN_WATCH_S, PLAN_HORIZON_S) + 1e-6, 2.0):
            xy, _, _, _, pd = pred[min(pred, key=lambda h: abs(h - tw))]
            seen = np.maximum(seen, points_in_poly(xy, fp) * pd)
        cov = float((pw0 * seen).sum())
        ok, penalty, why = supervisor.check_viewpoint(hover, path, alt_m, people_xy)
        return {"t": t, "u": u, "hover": hover, "path": path, "length": length, "eta": eta, "fp": fp, "seen": seen,
                "cov": cov, "ok": ok, "penalty": penalty, "why": why,
                "score": cov - TRAVEL_PENALTY_PER_S * eta - (penalty if ok else 0.0) if ok else None}

    def best(evs: list[dict[str, Any]]) -> dict[str, Any]:
        return max(evs, key=lambda x: (x["score"] is not None, x["score"] if x["score"] is not None else -9.0))

    out: list[dict[str, Any]] = []
    for kind, key, m_now in raw:
        if kind == "street":
            k = g.und_of_key[key]
            a, b = g.und[k]
            d_ab = g.dir[2 * k]
            evs = [evaluate((float(p[0]), float(p[1])), u) for f in STREET_LOOK_POINTS
                   for p in [g.xy[a] + (g.xy[b] - g.xy[a]) * f] for u in (d_ab, -d_ab)]
            ev = best(evs)
            label = g.label_edge(k)
            if key == subject_edge:
                label = f"street ahead ({label})"
        else:
            i = g.idx[key]
            t = (float(g.xy[i][0]), float(g.xy[i][1]))
            # look at the intersection along each street that meets it
            dirs = [-g.dir[e] for e in range(g.E) if int(g.frm[e]) == i]
            ev = best([evaluate(t, u) for u in dirs] or [evaluate(t, np.array([0.0, 1.0]))])
            label = g.label_node(key)
        commit = COMMIT_BONUS if f"{kind}:{key}" == current_key and ev["ok"] else 0.0
        by_h = {h.hid: round(float((pw0 * ev["seen"])[kk0 == i].sum()), 3) for i, h in enumerate(hyps)}
        t, hover, fp = ev["t"], ev["hover"], ev["fp"]
        out.append({"id": belief.candidate_id(f"{kind}:{key}"), "kind": kind, "key": f"{kind}:{key}", "label": label,
                    "target_xy": (round(t[0], 1), round(t[1], 1)), "hover_xy": (round(hover[0], 1), round(hover[1], 1)),
                    "look_deg": round(math.degrees(math.atan2(float(ev["u"][0]), float(ev["u"][1]))) % 360.0, 0),
                    "waypoints": [(round(p[0], 1), round(p[1], 1)) for p in ev["path"]],
                    "length_m": round(ev["length"], 1), "eta_s": round(ev["eta"], 1), "mass_now": round(m_now, 3),
                    "coverage": round(ev["cov"], 3), "by_hypothesis": by_h,
                    "penalty": round(ev["penalty"] if ev["ok"] else 0.0, 3), "commitment": commit,
                    "score": round(ev["score"] + commit, 3) if ev["score"] is not None else None,
                    "unsafe": None if ev["ok"] else ev["why"], "why": ev["why"],
                    "footprint": [(round(p[0], 1), round(p[1], 1)) for p in fp] if fp else None})
    out.sort(key=lambda c: (c["unsafe"] is not None, -(c["score"] if c["score"] is not None else -9.0)))
    return out
