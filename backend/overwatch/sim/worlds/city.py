"""City world (Follow): a downtown grid of tall buildings where a drone really does lose sight of a person.

Five streets each way, 70 m apart, with blocks of two to four buildings 12 to 48 m tall. The police base (a pad and
a hangar) sits in the south-west corner; a chase starts on the pad: the drone launches, finds the described person
and follows. Buildings taller than the drone's line of sight hide the target behind corners, so the pursuit routing
(D12 on the street graph) and the operator matter here. The target can be driven from a phone (``/drive``).
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

from ..actors import MoveTo, Wait, Wander
from ..world import BoxObj, World, ZoneSpec, launchpad_objects
from .pursuit import _dispatch_eval, _pursuit_eval

BOUNDS = (-200.0, 200.0, -200.0, 200.0)
STREETS = (-140.0, -70.0, 0.0, 70.0, 140.0)  # both directions
ROAD_W, WALK_W = 10.0, 2.4
LAUNCHPAD = (-172.0, -172.0)  # the police base, outside the grid's south-west corner
ALT_M = 45.0  # chase altitude: above most roofs, so the camera looks down between the towers
AUTONOMY = {"altitude_m": ALT_M, "min_altitude_m": 36.0, "max_altitude_m": 90.0, "standoff_m": 14.0,
            "follow_max_speed_mps": 6.0, "reacquire_max_speed_mps": 8.0}


def street_graph() -> dict:
    xs = (BOUNDS[0],) + STREETS + (BOUNDS[1],)
    nodes: dict[str, tuple[float, float]] = {}
    edges: list[tuple[str, str]] = []

    def nid(x, y):
        return f"{x:+.0f},{y:+.0f}"

    for y in STREETS:
        for a, b in zip(xs, xs[1:]):
            nodes[nid(a, y)], nodes[nid(b, y)] = (a, y), (b, y)
            edges.append((nid(a, y), nid(b, y)))
    for x in STREETS:
        for a, b in zip(xs, xs[1:]):
            nodes[nid(x, a)], nodes[nid(x, b)] = (x, a), (x, b)
            edges.append((nid(x, a), nid(x, b)))
    return {"nodes": nodes, "edges": edges, "source": "map data (simulator city, stands in for OpenStreetMap)"}


def _paint(tex, img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Concrete blocks, lighter sidewalks, asphalt streets with lane dashes; the base's apron in the corner."""
    res = tex.res_m
    h, w = img.shape[:2]
    base = np.array([150, 152, 154], np.float32)
    shade = (0.9 + 0.2 * rng.random((h // 24 + 1, w // 24 + 1))).astype(np.float32)
    shade = cv2.resize(shade, (w, h), interpolation=cv2.INTER_CUBIC)[..., None]
    img = np.ascontiguousarray(base[None, None, :] * shade, dtype=np.float32)
    for s in STREETS:
        for a, b in (((BOUNDS[0], s), (BOUNDS[1], s)), ((s, BOUNDS[2]), (s, BOUNDS[3]))):
            pa, pb = tex.to_px(*a), tex.to_px(*b)
            cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (184, 186, 184),
                     max(1, int((ROAD_W + 2 * WALK_W) / res)), cv2.LINE_AA)
    for s in STREETS:
        for a, b in (((BOUNDS[0], s), (BOUNDS[1], s)), ((s, BOUNDS[2]), (s, BOUNDS[3]))):
            pa, pb = tex.to_px(*a), tex.to_px(*b)
            cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (70, 72, 74), max(1, int(ROAD_W / res)), cv2.LINE_AA)
    for s in STREETS:  # lane dashes
        for t in np.arange(BOUNDS[0] + 2.0, BOUNDS[1], 6.0):
            for p, q in ((tex.to_px(t, s), tex.to_px(t + 3.0, s)), (tex.to_px(s, t), tex.to_px(s, t + 3.0))):
                cv2.line(img, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), (205, 208, 208), max(1, int(0.15 / res)))
    p, q = tex.to_px(LAUNCHPAD[0] - 14, LAUNCHPAD[1] - 12), tex.to_px(LAUNCHPAD[0] + 12, LAUNCHPAD[1] + 12)
    cv2.rectangle(img, (int(p[0]), int(q[1])), (int(q[0]), int(p[1])), (118, 120, 122), -1)  # the base's apron
    return img


def build_world() -> World:
    w = World(bounds=BOUNDS, texture_res_m=0.12, seed=47)
    w.name = "city"
    w.painter = _paint
    rng = np.random.default_rng(47)
    half = (STREETS[1] - STREETS[0]) / 2 - ROAD_W / 2 - WALK_W  # half the block interior (about 27.6 m)
    k = 0
    for bx in range(len(STREETS) - 1):
        for by in range(len(STREETS) - 1):
            cx, cy = (STREETS[bx] + STREETS[bx + 1]) / 2, (STREETS[by] + STREETS[by + 1]) / 2
            if (bx, by) == (1, 1):  # a plaza: open ground with a low pavilion, the one place with a clear view
                w.boxes.append(BoxObj(f"pavilion-{k}", "building", (cx, cy), (14.0, 14.0, 5.0), color=(150, 140, 128)))
                k += 1
                continue
            n = int(rng.integers(2, 5))
            for j in range(n):
                # buildings fill the block corners first, then the middle, with varied footprints and heights
                ox, oy = ((-1, -1), (1, 1), (1, -1), (-1, 1))[j]
                fx, fy = float(rng.uniform(18.0, 26.0)), float(rng.uniform(18.0, 26.0))
                x, y = cx + ox * (half - fx / 2), cy + oy * (half - fy / 2)
                hgt = float(rng.choice([12.0, 18.0, 24.0, 32.0, 40.0, 48.0], p=[0.15, 0.2, 0.25, 0.2, 0.12, 0.08]))
                g = int(rng.integers(96, 150))
                tint = int(rng.integers(-8, 10))
                w.boxes.append(BoxObj(f"bldg-{k}", "building", (x, y), (fx, fy, hgt), color=(g, g + 2, g + 6 + tint)))
                k += 1
    w.launchpad = LAUNCHPAD
    w.boxes += launchpad_objects(LAUNCHPAD, "hq-pad")
    w.boxes.append(BoxObj("hq-building", "building", (LAUNCHPAD[0] - 2.0, LAUNCHPAD[1] + 16.0), (22.0, 10.0, 9.0),
                          color=(112, 118, 130)))
    w.streets = street_graph()
    w.zones = [ZoneSpec("downtown", "Downtown grid", [(-170, -170), (170, -170), (170, 170), (-170, 170)], kind="sector")]
    w.geofence = [(BOUNDS[0] + 4, BOUNDS[2] + 4), (BOUNDS[1] - 4, BOUNDS[2] + 4), (BOUNDS[1] - 4, BOUNDS[3] - 4),
                  (BOUNDS[0] + 4, BOUNDS[3] - 4)]
    return w


def _city_eval(subject: str):
    """Dispatch scoring plus the pursuit's losses and recoveries (the towers hide the target)."""
    dispatch = _dispatch_eval(subject)
    pursuit = _pursuit_eval(subject, None)

    def run(res, sc) -> dict[str, Any]:
        out = dispatch(res, sc)
        p = pursuit(res, sc)
        out.update({k: p.get(k) for k in ("subject_in_frame_fraction", "lost_at_s", "found_again", "found_again_after_s",
                                           "d12_decisions", "d12_picks")})
        losses = 0
        was_visible = None
        for st in res.steps:
            if st.autonomy.get("mode") != "FOLLOW":
                continue
            g = (st.gt.get("actors") or {}).get(subject) or {}
            vis = bool(g.get("box_xyxy")) and g.get("visibility", 0) >= 0.5
            if was_visible and not vis:
                losses += 1
            was_visible = vis
        out["times_hidden_by_buildings"] = losses
        return out
    return run


def scenarios():
    from ..scenarios import BLUE, DARK, JEANS, KHAKI, MUSTARD, PURPLE, RED, DroneInit, MetricLimit, Scenario, ScriptedAction, person, responder

    officer = responder((LAUNCHPAD[0] + 10.0, LAUNCHPAD[1] + 2.0), [Wait(999)], heading=45.0)
    start = (-140.0, -150.0)  # on the westernmost street, just north of the base
    dispatch = {"description": "red shirt, jeans", "last_seen_xy": start, "radius_m": 36.0, "altitude_m": ALT_M}
    auto = [ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(2.0, "dispatch", dispatch),
            ScriptedAction(2.5, "operator_watch", "subject")]
    bystanders = [person("ped-b", "bystander", (-70.0, -150.0), BLUE, [Wander((-90.0, -50.0, -156.0, -144.0), 999.0, 0.9)], pants=KHAKI),
                  person("ped-m", "bystander", (-140.0, -40.0), MUSTARD, [Wander((-146.0, -134.0, -60.0, -20.0), 999.0, 0.8)], pants=DARK),
                  person("ped-p", "bystander", (-60.0, -70.0), PURPLE, [Wander((-80.0, -40.0, -76.0, -64.0), 999.0, 0.8)])]
    out = []
    # CT1: phone-controlled. The target stands near the base until someone drives them from a phone (/drive); without a
    # phone they walk a loop round two blocks after a minute so the demo still plays by itself.
    out.append(Scenario(
        "CT1", "City chase (phone-controlled): launch from the base, find and follow",
        "Downtown. The drone starts on the base's pad; the operator describes the person (red shirt, jeans) and where "
        "they were last seen. The drone launches, finds them among the people in the street and follows. Drive the "
        "target from a phone (/drive, through the Cloudflare tunnel) and lead the drone round the towers.",
        duration_s=420.0, drone=DroneInit(LAUNCHPAD[0], LAUNCHPAD[1], 0.0, 45.0, -60.0),
        actors=[officer, person("subject", "subject", start, RED,
                                [Wait(60.0), MoveTo(-140.0, -70.0, 2.5), MoveTo(-70.0, -70.0, 2.5), MoveTo(-70.0, 0.0, 2.5),
                                 MoveTo(-140.0, 0.0, 2.5), MoveTo(-140.0, -150.0, 2.5), Wait(999)], pants=JEANS, heading=0)] + bystanders,
        actions=list(auto), autonomy=dict(AUTONOMY),
        limits=[MetricLimit("world.wrong_match", max=0), MetricLimit("world.matched", min=1)],
        tags=["world:city", "follow", "autonomy", "dispatch", "phone", "demo"]))
    out[-1].dispatch = dispatch
    out[-1].world_eval = _city_eval("subject")
    # CT2: scripted. The target runs north, cuts east behind the towers at the first corner and keeps turning: the
    # drone loses sight behind the buildings and has to route on the street map to find them again.
    out.append(Scenario(
        "CT2", "City chase: the target cuts behind the towers",
        "The target runs north from the base, turns east at the first corner behind a 40 m tower and keeps turning "
        "corners. The drone launches, matches them and follows; each corner hides them, and D12 picks a street to "
        "search when they are gone.",
        duration_s=200.0, drone=DroneInit(LAUNCHPAD[0], LAUNCHPAD[1], 0.0, 45.0, -60.0),
        actors=[officer, person("subject", "subject", start, RED,
                                [Wait(48.0), MoveTo(-140.0, -70.0, 3.5), MoveTo(-70.0, -70.0, 3.5), MoveTo(-70.0, 0.0, 3.5),
                                 MoveTo(0.0, 0.0, 3.5), MoveTo(0.0, 70.0, 3.0), MoveTo(70.0, 70.0, 3.0), Wait(999)],
                                pants=JEANS, heading=0)] + bystanders,
        actions=list(auto), autonomy=dict(AUTONOMY),
        limits=[MetricLimit("world.wrong_match", max=0), MetricLimit("world.matched", min=1),
                MetricLimit("subject_lock.wrong_target", max=0)],
        tags=["world:city", "follow", "autonomy", "dispatch", "occlusion"]))
    out[-1].dispatch = dispatch
    out[-1].world_eval = _city_eval("subject")
    for s in out:
        s.world = build_world
    return out
