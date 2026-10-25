"""Police pursuit world (Follow): a suburban block with houses, carports as cover, fences, parked cars and street
trees; a consenting volunteer playing an evasive subject, a look-alike, pedestrians and an officer.

Scale: 400 by 300 m, the plan's size: a grid of streets around the original hand-placed block (which the PP
scenarios use), filled procedurally with houses, carports, parked cars and street trees. The street graph is map
data (the role an OpenStreetMap extract plays for a real incident) and is what pursuit routing plans on.
Everything is simulated and labelled SIMULATED.
Static objects are kept low-saturation so the simulated blob detector reacts to people's clothing, not to roofs.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from ..actors import MoveTo, Wait, Wander
from ..world import BoxObj, TreeObj, World, ZoneSpec, launchpad_objects

LAUNCHPAD = (-138.0, -128.0)
BOUNDS = (-160.0, 240.0, -150.0, 150.0)
STREETS_X = (-110.0, 30.0, 150.0)  # north-south streets
STREETS_Y = (-95.0, 0.0, 95.0)  # east-west streets
CORE = (-60.0, 90.0, -50.0, 60.0)  # the original hand-placed block (PP1 to PP4)
ROADS = [((BOUNDS[0], y), (BOUNDS[1], y), 8.0) for y in STREETS_Y] + \
    [((x, BOUNDS[2]), (x, BOUNDS[3]), 7.0) for x in STREETS_X]  # (a, b, width m)
SIDEWALKS = [((BOUNDS[0], y + d), (BOUNDS[1], y + d), 2.2) for y in STREETS_Y for d in (6.0, -6.0)] + \
    [((x + d, BOUNDS[2]), (x + d, BOUNDS[3]), 2.2) for x in STREETS_X for d in (5.5, -5.5)]


NAMES_X = {-110.0: "Ash Street", 30.0: "Oak Street", 150.0: "Pine Street"}  # north-south
NAMES_Y = {-95.0: "South Road", 0.0: "Main Street", 95.0: "North Road"}  # east-west


def _short(name: str) -> str:
    return name.replace(" Street", " St").replace(" Road", " Rd")


def street_graph() -> dict:
    """Map data: intersections and street segments (local metres), with street names. Pursuit routing plans on this."""
    xs = (BOUNDS[0],) + STREETS_X + (BOUNDS[1],)
    ys = (BOUNDS[2],) + STREETS_Y + (BOUNDS[3],)
    nodes: dict[str, tuple[float, float]] = {}
    edges: list[tuple[str, str]] = []
    names: dict[str, str] = {}
    labels: dict[str, str] = {}
    def nid(x, y):
        return f"{x:+.0f},{y:+.0f}"
    def cross_x(x):
        return _short(NAMES_X[x]) if x in NAMES_X else ("the west map edge" if x == BOUNDS[0] else "the east map edge")
    def cross_y(y):
        return _short(NAMES_Y[y]) if y in NAMES_Y else ("the south map edge" if y == BOUNDS[2] else "the north map edge")
    for y in STREETS_Y:  # east-west streets: every crossing x along them
        row = [x for x in xs]
        for a, b in zip(row, row[1:]):
            nodes[nid(a, y)], nodes[nid(b, y)] = (a, y), (b, y)
            edges.append((nid(a, y), nid(b, y)))
            names[f"{nid(a, y)}|{nid(b, y)}"] = f"{NAMES_Y[y]} between {cross_x(a)} and {cross_x(b)}"
    for x in STREETS_X:
        col = [y for y in ys]
        for a, b in zip(col, col[1:]):
            nodes[nid(x, a)], nodes[nid(x, b)] = (x, a), (x, b)
            edges.append((nid(x, a), nid(x, b)))
            names[f"{nid(x, a)}|{nid(x, b)}"] = f"{NAMES_X[x]} between {cross_y(a)} and {cross_y(b)}"
    for n, (x, y) in nodes.items():
        if x in NAMES_X and y in NAMES_Y:
            labels[n] = f"{_short(NAMES_X[x])} & {_short(NAMES_Y[y])}"
        elif x in NAMES_X:
            labels[n] = f"{_short(NAMES_X[x])} at {cross_y(y)}"
        else:
            labels[n] = f"{_short(NAMES_Y[y])} at {cross_x(x)}"
    return {"nodes": nodes, "edges": edges, "names": names, "node_labels": labels,
            "source": "map data (simulator world, stands in for OpenStreetMap)"}


def _paint(tex, img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    res = tex.res_m
    img = np.ascontiguousarray(img, dtype=np.float32)
    for (a, b, width), col in [(r, (170, 172, 170)) for r in SIDEWALKS] + [(r, (78, 80, 82)) for r in ROADS]:
        pa, pb = tex.to_px(*a), tex.to_px(*b)  # drawn straight into the texture: sidewalks first, roads on top
        cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), col, max(1, int(width / res)), cv2.LINE_AA)
    for y in STREETS_Y:  # lane dashes
        for x in np.arange(BOUNDS[0] + 2.0, BOUNDS[1], 6.0):
            p, q = tex.to_px(x, y), tex.to_px(x + 3.0, y)
            cv2.line(img, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), (200, 205, 205), max(1, int(0.15 / res)))
    for x0, y0, x1, y1 in [(-52, 9, -28, 30), (-22, 9, 2, 30), (36, 9, 62, 30), (-52, -32, -28, -9), (-22, -32, 2, -9),
                           (36, -32, 62, -9)]:  # driveways and yards are already grass; mark driveways
        p, q = tex.to_px(x0 + 9, y0), tex.to_px(x0 + 12, y0 + 6)
        cv2.rectangle(img, (int(p[0]), int(q[1])), (int(q[0]), int(p[1])), (120, 124, 126), -1)
    return img


def _house(oid: str, x: float, y: float, heading: float = 0.0) -> list[BoxObj]:
    return [BoxObj(oid, "house", (x, y), (10.0, 8.0, 6.0), heading_deg=heading, color=(118, 120, 126))]


WALKWAY_Y = (22.0, 78.0)  # the covered walkway over Oak Street's east sidewalk (PP5 to PP7)
LOOK_FOR_AT_S = 44.0  # PP7: the operator's description, once both hypotheses are up


def covered_walkway(x: float, y0: float, y1: float, seg_m: float = 2.0) -> list[BoxObj]:
    """A roof on posts over a sidewalk, built from short roof slabs so the renderer's painter's order keeps a person
    under it hidden from above. Cover from above for map data (street_cover) and for the belief."""
    out = [BoxObj(f"walkway-{i}", "walkway", (x, y0 + (i + 0.5) * seg_m), (seg_m, 3.6, 0.25), z0=2.5,
                  color=(104, 108, 112)) for i in range(int(round((y1 - y0) / seg_m)))]
    for j, y in enumerate(np.arange(y0 + 1.0, y1, 6.0)):
        for side, dx in (("w", -1.7), ("e", 1.7)):
            out.append(BoxObj(f"walkway-post-{side}{j}", "post", (x + dx, float(y)), (0.16, 0.16, 2.5), color=(90, 92, 96)))
    return out


def build_world() -> World:
    w = World(bounds=BOUNDS, texture_res_m=0.08, seed=21)
    w.name = "pursuit"  # type: ignore[attr-defined]
    w.painter = _paint  # type: ignore[attr-defined]
    w.boxes = []
    for i, (x, y) in enumerate([(-44, 20), (-14, 20), (48, 20), (-44, -21), (-14, -21), (48, -21), (72, 21)]):
        w.boxes += _house(f"house-{i + 1}", x, y)
    # carports (roof slab over a driveway: cover from above), fences, parked cars (low saturation)
    w.boxes += [
        BoxObj("carport-1", "carport", (-32.5, 12.0), (4.0, 5.5, 2.7), color=(104, 108, 112)),
        BoxObj("carport-2", "carport", (-2.5, -12.0), (4.0, 5.5, 2.7), color=(104, 108, 112)),
        BoxObj("fence-1", "fence", (-22.0, 30.0), (60.0, 0.12, 1.6), heading_deg=90.0, color=(92, 104, 118)),
        BoxObj("fence-2", "fence", (49.0, -32.0), (26.0, 0.12, 1.6), heading_deg=90.0, color=(92, 104, 118)),
        BoxObj("car-p1", "vehicle", (-8.0, 3.2), (4.5, 1.9, 1.5), heading_deg=90.0, color=(60, 62, 64), reflective=True),
        BoxObj("car-p2", "vehicle", (14.0, -3.2), (4.4, 1.8, 1.45), heading_deg=90.0, color=(196, 196, 192), reflective=True),
        BoxObj("car-p3", "vehicle", (40.0, 3.2), (4.6, 1.9, 1.5), heading_deg=90.0, color=(110, 112, 116), reflective=True),
    ]
    w.trees = [TreeObj(f"street-tree-{i}", (x, 8.6), canopy_r=2.8, color=(40, 92, 50))
               for i, x in enumerate((-36.0, -20.0, 6.0, 22.0, 46.0))]
    _fill_suburb(w)
    w.streets = street_graph()
    w.launchpad = LAUNCHPAD  # the station's pad in the south-west corner, where a dispatched drone launches from
    w.boxes += launchpad_objects(LAUNCHPAD, "station-pad")
    w.zones = [ZoneSpec("pp-block", "Block under watch", [(-55, -35), (85, -35), (85, 35), (-55, 35)], kind="sector")]
    w.geofence = [(BOUNDS[0] + 4, BOUNDS[2] + 4), (BOUNDS[1] - 4, BOUNDS[2] + 4), (BOUNDS[1] - 4, BOUNDS[3] - 4),
                  (BOUNDS[0] + 4, BOUNDS[3] - 4)]
    return w


def _in_core(x: float, y: float, m: float = 0.0) -> bool:
    return CORE[0] - m <= x <= CORE[1] + m and CORE[2] - m <= y <= CORE[3] + m


def _fill_suburb(w: World) -> None:
    """Houses along every street outside the original block, with carports, parked cars and street trees
    (all low-saturation, so the simulated detector reacts to clothing, not roofs)."""
    rng = np.random.default_rng(21)
    k = 0
    def near_cross(x: float, y: float) -> bool:
        return any(abs(x - sx) < 16 for sx in STREETS_X) and any(abs(y - sy) < 16 for sy in STREETS_Y)
    lots = []
    for sy in STREETS_Y:  # houses facing east-west streets
        for x in np.arange(BOUNDS[0] + 12, BOUNDS[1] - 12, 22.0):
            for side in (1, -1):
                lots.append((float(x), sy + side * 21.0, 0.0, sy + side * 9.0))
    for sx in STREETS_X:  # houses facing north-south streets
        for y in np.arange(BOUNDS[2] + 12, BOUNDS[3] - 12, 22.0):
            for side in (1, -1):
                lots.append((sx + side * 21.0, float(y), 90.0, None))
    taken: list[tuple[float, float]] = []
    for x, y, hd, curb_y in lots:
        if _in_core(x, y, 12.0) or near_cross(x, y) or not (BOUNDS[0] + 8 < x < BOUNDS[1] - 8) or \
                not (BOUNDS[2] + 8 < y < BOUNDS[3] - 8) or any(abs(x - a) < 13 and abs(y - b) < 13 for a, b in taken):
            continue
        if any(abs(y - sy) < 13 for sy in STREETS_Y) or any(abs(x - sx) < 13 for sx in STREETS_X):
            continue
        taken.append((x, y))
        k += 1
        shade = int(rng.integers(104, 134))
        w.boxes.append(BoxObj(f"house-g{k}", "house", (x, y), (10.0, 8.0, float(rng.uniform(5.0, 7.0))), heading_deg=hd,
                              color=(shade, shade + 2, shade + 7)))
        r = rng.random()
        if r < 0.25:  # carport beside the house
            w.boxes.append(BoxObj(f"carport-g{k}", "carport", (x + 8.0, y), (4.0, 5.5, 2.7), heading_deg=hd,
                                  color=(104, 108, 112)))
        elif r < 0.55 and curb_y is not None:  # a car parked at the curb
            g = int(rng.integers(55, 200))
            w.boxes.append(BoxObj(f"car-g{k}", "vehicle", (x + float(rng.uniform(-4, 4)), curb_y - np.sign(curb_y - y) * 5.6),
                                  (4.5, 1.9, 1.5), heading_deg=90.0, color=(g, g + 2, g + 4), reflective=True))
        if rng.random() < 0.4 and curb_y is not None:
            w.trees.append(TreeObj(f"street-tree-g{k}", (x - 6.0, curb_y + np.sign(y - curb_y) * 2.6),
                                   canopy_r=float(rng.uniform(2.4, 3.2)), color=(40, int(rng.integers(84, 98)), 50)))


def scenarios():
    from ..scenarios import (BLUE, DARK, JEANS, KHAKI, MUSTARD, PURPLE, RED, DroneInit, ExpectedEvent, MetricLimit,
                             Scenario, ScriptedAction, person, responder)

    officer = lambda start, script=None: responder(start, script or [Wait(999)], heading=90.0)  # noqa: E731
    common_limits = [MetricLimit("subject_lock.wrong_target", max=0)]
    out = []

    # PP1: designated subject walks the sidewalk; the pilot keeps up
    out.append(Scenario(
        "PP1", "Pursuit: designated subject on the sidewalk",
        "A consenting volunteer walks the north sidewalk; the operator designates them; the pilot flies along.",
        duration_s=40.0, drone=DroneInit(-42.0, -8.0, 24.0, 0.0, -60.0),
        actors=[officer((-44.0, -6.0)),
                person("subject", "subject", (-44.0, 6.0), RED, [Wait(2.0), MoveTo(20.0, 6.0, 1.7), Wait(99)], heading=90),
                person("ped-1", "bystander", (10.0, -6.0), BLUE, [Wander((0.0, 20.0, -7.0, -5.0), 99.0, 0.9)], pants=KHAKI)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
        expected=[ExpectedEvent("TRACK_LOST", 3.0, 40.0, "subject", present=False)],
        limits=common_limits + [MetricLimit("detection.recall", min=0.85),
                                MetricLimit("subject_lock.correct_fraction", min=0.95)],
        tags=["world:pursuit", "follow"]))
    out[-1].pilot_path = [(-42.0, -8.0, 24.0, 0.0), (-42.0, -8.0, 24.0, 0.1), (20.0, -8.0, 24.0, 1.7)]  # type: ignore[attr-defined]

    # PP2: the subject ducks under a carport for about 5 s and comes back out
    out.append(Scenario(
        "PP2", "Pursuit: subject hides under a carport",
        "The subject turns into a driveway, stands under carport 1 for about 5 s, then walks on.",
        duration_s=36.0, drone=DroneInit(-34.0, -6.0, 24.0, 0.0, -66.0),
        actors=[officer((-44.0, -6.0)),
                person("subject", "subject", (-46.0, 6.0), RED,
                       [Wait(2.0), MoveTo(-33.0, 6.0, 1.6), MoveTo(-32.5, 12.0, 1.4), Wait(5.0), MoveTo(-32.5, 6.0, 1.4),
                        MoveTo(-18.0, 6.0, 1.6), Wait(99)], heading=90)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
        expected=[ExpectedEvent("TRACK_LOST", 12.0, 20.0, "subject"), ExpectedEvent("REACQUIRED", 16.0, 30.0, "subject")],
        limits=common_limits + [MetricLimit("detection.recall", min=0.75)],
        tags=["world:pursuit", "follow", "occlusion"]))

    # PP3: a look-alike in the same red shirt crosses the subject's path
    out.append(Scenario(
        "PP3", "Pursuit: look-alike crosses on the street",
        "A second person in a red shirt crosses the subject's path; the lock must not move to them.",
        duration_s=30.0, drone=DroneInit(-10.0, -12.0, 26.0, 0.0, -64.0),
        actors=[officer((-30.0, -6.0)),
                person("subject", "subject", (-28.0, 6.0), RED, [Wait(2.0), MoveTo(6.0, 6.0, 1.5), Wait(99)], heading=90, vary=False),
                person("decoy", "bystander", (6.0, 6.6), RED, [Wait(2.0), MoveTo(-28.0, 6.6, 1.5), Wait(99)], heading=270,
                       pants=DARK, vary=False),
                person("ped-2", "bystander", (-15.0, -6.0), MUSTARD, [Wander((-20.0, 0.0, -7.0, -5.0), 99.0, 0.8)],
                       pants=JEANS)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
        limits=common_limits + [MetricLimit("subject_lock.correct_fraction", min=0.9)],
        tags=["world:pursuit", "follow", "reid"]))

    # PP4: the subject runs under the street trees past parked cars
    out.append(Scenario(
        "PP4", "Pursuit: running under street trees",
        "The subject runs east along the sidewalk under two street-tree canopies; brief losses are expected and no "
        "other person may take the lock.",
        duration_s=30.0, drone=DroneInit(-14.0, -8.0, 26.0, 0.0, -60.0),
        actors=[officer((-20.0, -6.0)),
                person("subject", "subject", (-16.0, 6.4), RED, [Wait(2.0), MoveTo(34.0, 6.4, 3.0), Wait(99)], heading=90),
                person("ped-3", "bystander", (20.0, -6.0), PURPLE, [Wander((14.0, 28.0, -7.0, -5.0), 99.0, 0.9)])],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
        limits=common_limits + [MetricLimit("detection.recall", min=0.7)],
        tags=["world:pursuit", "follow", "occlusion"]))
    out[-1].pilot_path = [(-14.0, -8.0, 26.0, 0.0), (-14.0, -8.0, 26.0, 0.1), (34.0, -8.0, 26.0, 2.6)]  # type: ignore[attr-defined]

    # PP5 to PP7: autonomous pursuit (simulator only). The autonomy stack follows the designated subject; when the
    # subject is lost, a street-graph belief (autonomy/belief.py) spreads particles along the streets from where they
    # vanished, one set per identity hypothesis, and D12 picks the viewpoint whose camera footprint would cover the
    # most of it on arrival. The drone never moves towards a re-binding nobody confirmed (it holds and keeps the camera
    # on it); a scripted operator watching the video confirms it after 1.2 s if it is the right person, or
    # re-designates the right person.
    auto = [ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject"),
            ScriptedAction(2.0, "operator_watch", "subject"), ScriptedAction(2.5, "autonomy", "FOLLOW")]
    # a covered walkway (pergola) over the north sidewalk past the intersection: cover from above (PJ2)
    pergola = [BoxObj("pergola-1", "carport", (35.5, 34.0), (3.6, 16.0, 2.8), color=(104, 108, 112))]
    walk = covered_walkway(35.5, WALKWAY_Y[0], WALKWAY_Y[1])
    # the subject sprints north up Oak Street into the covered walkway (lost from above), hides in it, then runs on
    into = [Wait(2.0), MoveTo(25.0, 6.0, 1.8), MoveTo(35.5, 14.0, 4.6), MoveTo(35.5, 24.0, 4.6)]
    pp_limits = [MetricLimit("subject_lock.wrong_target", max=0), MetricLimit("world.following_lookalike_steps", max=0)]
    out.append(Scenario(
        "PP5", "Pursuit (autonomous): sprint into the covered walkway, straight on",
        "The subject sprints north up Oak Street into a covered walkway and is lost from above. One hypothesis: the "
        "belief flows straight ahead along the street, so D12 flies the street ahead; the subject comes out of the "
        "walkway's north end into the camera's view.",
        duration_s=60.0, drone=DroneInit(-16.0, -10.0, 24.0, 0.0, -60.0),
        actors=[officer((-24.0, -6.0)),
                person("subject", "subject", (-12.0, 6.0), RED,
                       into + [MoveTo(35.5, 46.0, 4.6), Wait(4.0), MoveTo(35.5, 79.0, 4.6), MoveTo(35.5, 140.0, 4.6),
                               Wait(99)], heading=90)],
        actions=list(auto), extra_boxes=walk,
        expected=[ExpectedEvent("TRACK_LOST", 20.0, 45.0, "subject")],
        limits=pp_limits + [MetricLimit("world.found_again", min=1), MetricLimit("world.belief.max_hypotheses", max=1),
                            MetricLimit("world.belief.first_pick_is_street_ahead", min=1)],
        tags=["world:pursuit", "follow", "autonomy", "decisions"]))
    out[-1].world_eval = _pursuit_eval("subject", "decoy")
    # PP6 and PP7: the same chase, and a second person in the same red shirt (dark trousers, not jeans) steps out of
    # the walkway's north end about when the sprinting subject could have got there, and jogs east along North
    # Road. The real subject is still hiding in the walkway and comes out later.
    lookalike = person("decoy", "bystander", (37.0, 77.0), RED, [Wait(38.0), MoveTo(36.0, 88.5, 3.2),
                                                                 MoveTo(140.0, 89.0, 4.4), Wait(99)],
                       heading=0, pants=DARK, vary=False)
    chase = into + [MoveTo(35.5, 46.0, 4.6), Wait(8.5), MoveTo(35.5, 79.0, 4.6), MoveTo(35.5, 140.0, 4.6), Wait(99)]
    # the walkway hides the subject for longer than the default 20 s search, and in PP6 the drone checks the
    # look-alike's cloud first, so the search runs 45 s before it asks the operator
    longer_search = {"hold_after_s": 45.0}
    out.append(Scenario(
        "PP6", "Pursuit (autonomous): a look-alike after the subject is lost",
        "The subject vanishes into the covered walkway on Oak Street. Seconds later a second person in the same red "
        "shirt steps out of its far end and jogs east along North Road. Two hypotheses, two particle clouds: the "
        "subject still hidden or running on, or the subject is that person. D12 flies to the intersection that keeps "
        "both in view; the drone never follows the look-alike on its own, and a person decides who is who.",
        duration_s=75.0, drone=DroneInit(-16.0, -10.0, 24.0, 0.0, -60.0),
        actors=[officer((-24.0, -6.0)), person("subject", "subject", (-12.0, 6.0), RED, chase, heading=90, vary=False),
                lookalike],
        actions=list(auto), extra_boxes=walk, locks=dict(longer_search),
        limits=pp_limits + [MetricLimit("world.belief.max_hypotheses", min=2),
                            MetricLimit("world.belief.route_covers_both", min=1)],
        tags=["world:pursuit", "follow", "autonomy", "reid"]))
    out[-1].world_eval = _pursuit_eval("subject", "decoy")
    out.append(Scenario(
        "PP7", "Pursuit (autonomous): the operator asks to look for a red shirt and jeans",
        "PP6, and while the two clouds are up the operator types 'look for red shirt, jeans'. The words are parsed into "
        "a clothing descriptor and score each hypothesis: the look-alike wears dark trousers, so the belief shifts to "
        "the subject's cloud and the route changes from the intersection to the walkway the subject is in. The words "
        "reach routing only through the belief, never as text to a model.",
        duration_s=60.0, drone=DroneInit(-16.0, -10.0, 24.0, 0.0, -60.0),
        actors=[officer((-24.0, -6.0)), person("subject", "subject", (-12.0, 6.0), RED, chase, heading=90, vary=False),
                lookalike],
        actions=list(auto) + [ScriptedAction(LOOK_FOR_AT_S, "look_for", "red shirt, jeans")], extra_boxes=walk,
        locks=dict(longer_search),
        limits=pp_limits + [MetricLimit("world.belief.max_hypotheses", min=2),
                            MetricLimit("world.belief.subject_weight_after_look_for", min=0.75),
                            MetricLimit("world.found_again", min=1)],
        tags=["world:pursuit", "follow", "autonomy", "decisions"]))
    out[-1].world_eval = _pursuit_eval("subject", "decoy")

    # PP0: the plain demo. One person runs along the streets and round two corners; nobody else is about. The
    # operator designates them once and the drone follows on its own (simulator only).
    pp0_dispatch = {"description": "red shirt, jeans", "last_seen_xy": (-62.0, 6.0), "radius_m": 24.0, "altitude_m": 24.0}
    out.append(Scenario(
        "PP0", "Pursuit (autonomous): launch from the station, a person runs, the drone follows",
        "The drone sits on the station's pad. A consenting volunteer in a red shirt waits by the street, then jogs "
        "east, turns north at the intersection and east again. The drone launches, finds them by their clothing and "
        "keeps them in frame on its own.",
        duration_s=150.0, drone=DroneInit(LAUNCHPAD[0], LAUNCHPAD[1], 0.0, 45.0, -60.0),
        actors=[officer((LAUNCHPAD[0] + 10.0, LAUNCHPAD[1] + 2.0)),
                person("subject", "subject", (-62.0, 6.0), RED,
                       [Wait(72.0), MoveTo(24.0, 6.0, 3.2), MoveTo(36.0, 14.0, 3.2), MoveTo(36.0, 89.0, 3.2),
                        MoveTo(44.0, 101.0, 3.2), MoveTo(110.0, 101.0, 3.2), Wait(99)], heading=90)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(2.0, "dispatch", pp0_dispatch),
                 ScriptedAction(2.5, "operator_watch", "subject")],
        limits=[MetricLimit("subject_lock.wrong_target", max=0), MetricLimit("world.following_lookalike_steps", max=0),
                MetricLimit("world.wrong_match", max=0), MetricLimit("world.matched", min=1)],
        tags=["world:pursuit", "follow", "autonomy", "dispatch", "demo"]))
    out[-1].dispatch = pp0_dispatch
    out[-1].world_eval = _dispatch_eval("subject", extra=_pursuit_eval("subject", None))

    # PP8: dispatch. The drone sits on the station's pad; the operator gives a description and where the person was
    # last seen. The drone takes off, flies to the area, flies search legs, matches the people the detector finds
    # against the words, tracks the match and reports where they are relative to the pad.
    dispatch = {"description": "red shirt, jeans", "last_seen_xy": (-40.0, -92.0), "radius_m": 48.0, "altitude_m": 28.0}
    out.append(Scenario(
        "PP8", "Dispatch (autonomous): launch, search by description, track, report",
        "The drone launches from the station pad, searches the block where a person in a red shirt and jeans was last "
        "seen, finds them among other people by their clothing, follows them and reports their position relative to "
        "the pad. Nobody is identified: the match is by clothing colour words.",
        duration_s=170.0, drone=DroneInit(LAUNCHPAD[0], LAUNCHPAD[1], 0.0, 45.0, -60.0),
        actors=[officer((-120.0, -110.0)),
                person("subject", "subject", (-62.0, -92.0), RED,
                       [Wait(20.0), MoveTo(10.0, -92.0, 1.5), MoveTo(30.0, -92.0, 1.5), MoveTo(30.0, -40.0, 1.6),
                        MoveTo(30.0, 10.0, 1.6), Wait(99)], heading=90),
                person("ped-b", "bystander", (-70.0, -98.0), BLUE, [Wander((-80.0, -50.0, -102.0, -96.0), 999.0, 0.9)],
                       pants=KHAKI),
                person("ped-m", "bystander", (-20.0, -98.5), MUSTARD, [Wait(10.0), MoveTo(-60.0, -98.5, 1.2), Wait(99)],
                       heading=270, pants=DARK),
                person("ped-p", "bystander", (0.0, -86.0), PURPLE, [Wander((-10.0, 20.0, -88.0, -84.0), 999.0, 0.8)])],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(2.0, "dispatch", dispatch),
                 ScriptedAction(2.5, "operator_watch", "subject")],
        limits=[MetricLimit("world.wrong_match", max=0), MetricLimit("world.matched", min=1),
                MetricLimit("world.report_distance_error_m", max=8.0)],
        tags=["world:pursuit", "follow", "autonomy", "dispatch", "demo"]))
    out[-1].dispatch = dispatch
    out[-1].world_eval = _dispatch_eval("subject")

    # PJ1 and PJ2: the same pursuits flown by the jev-drone stack: the MuJoCo Skydio X2 low over the street, with
    # forward range sectors (a depth-camera stand-in from the simulator's geometry), the reflex that always owns
    # safety, and D6 obstacle maneuvers (Jev when its key is present). At 7 m the street trees are in the way.
    low = {"altitude_m": 7.0, "min_altitude_m": 4.0, "max_altitude_m": 30.0, "standoff_m": 9.0,
           "follow_max_speed_mps": 5.0, "reacquire_max_speed_mps": 6.0}
    out.append(Scenario(
        "PJ1", "jev-drone X2: follow a runner low through the street trees",
        "The same run as PP0, flown by the MuJoCo Skydio X2 at 7 m with the jev-drone stack: the reflex brakes and "
        "steers around the street trees from five range sectors, and D6 picks sidestep, climb or hold when "
        "something blocks the path. The drone starts airborne.",
        duration_s=75.0, drone=DroneInit(-73.0, 6.0, 7.0, 90.0, -35.0),  # behind the runner, looking along the street
        actors=[officer((-74.0, -6.0)),
                person("subject", "subject", (-62.0, 6.0), RED,
                       [Wait(3.0), MoveTo(24.0, 6.0, 3.2), MoveTo(36.0, 14.0, 3.2), MoveTo(36.0, 89.0, 3.2),
                        MoveTo(44.0, 101.0, 3.2), MoveTo(110.0, 101.0, 3.2), Wait(99)], heading=90)],
        actions=list(auto), stack="jev-x2", autonomy=dict(low),
        limits=[MetricLimit("subject_lock.wrong_target", max=0), MetricLimit("world.jev.collisions", max=0)],
        tags=["world:pursuit", "follow", "autonomy", "jev", "demo"]))
    out[-1].world_eval = _jev_eval("subject")
    out.append(Scenario(
        "PJ2", "jev-drone X2: sprint, corner and the covered walkway, low",
        "PP5 flown by the jev-drone stack at 7 m: the subject sprints north at the intersection and hides under the "
        "pergola; the X2 has to get round the street trees and the pergola roof while keeping up, then search.",
        duration_s=55.0, drone=DroneInit(-23.0, 6.0, 7.0, 90.0, -35.0),
        actors=[officer((-24.0, -6.0)),
                person("subject", "subject", (-12.0, 6.0), RED,
                       [Wait(2.0), MoveTo(25.0, 6.0, 1.8), MoveTo(35.5, 14.0, 4.6), MoveTo(35.5, 32.0, 4.6), Wait(5.0),
                        MoveTo(35.5, 88.0, 4.6), MoveTo(60.0, 101.0, 2.0), Wait(99)], heading=90)],
        actions=list(auto), extra_boxes=pergola, stack="jev-x2", autonomy=dict(low),
        limits=[MetricLimit("subject_lock.wrong_target", max=0), MetricLimit("world.jev.collisions", max=0)],
        tags=["world:pursuit", "follow", "autonomy", "jev"]))
    out[-1].world_eval = _jev_eval("subject")

    world = build_world
    for s in out:
        s.world = world  # type: ignore[attr-defined]
    return out


def _jev_eval(subject: str):
    """Scores the low-altitude stack: clearance from the static world at the aircraft's height (a collision is
    under 0.6 m, about the airframe), how often the reflex was in charge, the D6 judgments, and the pursuit itself."""
    base = _pursuit_eval(subject, None)

    def run(res, sc):
        from ...autonomy.reflex import nearest_obstacle_m

        w = build_world()
        boxes = list(w.boxes) + list(sc.extra_boxes)
        clear: list[float] = []
        reflex_steps = follow_steps = collisions = 0
        alts: list[float] = []
        for st in res.steps:
            xy, alt = st.gt.get("drone_xy"), st.gt.get("drone_alt")
            if not xy or alt is None or st.autonomy.get("mode") not in ("FOLLOW", "DISPATCH"):
                continue
            follow_steps += 1
            d = nearest_obstacle_m(boxes, w.trees, (xy[0], xy[1], alt))
            clear.append(d)
            alts.append(alt)
            collisions += d < 0.6
            reflex_steps += bool(st.autonomy.get("reflex"))
        d6 = [d for d in res.decisions if d["task"] == "D6_obstacle" and not d.get("shadow")]
        out = base(res, sc)
        out["jev"] = {"min_clearance_m": round(min(clear), 2) if clear else None,
                      "collisions": collisions, "reflex_fraction": round(reflex_steps / follow_steps, 3) if follow_steps else None,
                      "altitude_m": {"min": round(min(alts), 1), "max": round(max(alts), 1)} if alts else None,
                      "d6_decisions": len(d6),
                      "d6_engines": sorted({d["engine"] for d in d6}),
                      "d6_picks": [(round(d["request_ts_ms"] / 1000, 1), d["engine"], d["selected"], d["acted"]) for d in d6][:12],
                      "scene_source": "simulator geometry (depth-camera stand-in)"}
        return out
    return run


def _dispatch_eval(subject: str, extra=None):
    """Scores a dispatch against ground truth: did the lock land on the described person (never on anyone else),
    how long it took from the dispatch, and how far the reported position was from the true one."""
    def run(res, sc):
        from ...evaluation.gt_eval import _iou

        dispatched_at = next((a["t_ms"] for a in res.operator_log if a["action"] == "dispatch"), None)
        matched_at = None
        wrong = 0
        phases: dict[str, int] = {}
        rep_errs: list[float] = []
        people_seen = 0
        for st in res.steps:
            ds = st.autonomy.get("dispatch") or {}
            if ds:
                phases[ds.get("phase", "?")] = phases.get(ds.get("phase", "?"), 0) + 1
                people_seen = max(people_seen, int(ds.get("people_seen") or 0))
            tid = (st.locks.get("subject") or {}).get("track_id")
            if not tid or ds.get("phase") != "TRACKING":
                continue
            box = next((t["box"] for t in st.tracks if t["id"] == tid and t["box"]), None)
            g = (st.gt.get("actors") or {}).get(subject) or {}
            if box is not None and g.get("box_xyxy"):
                if _iou(box, g["box_xyxy"]) >= 0.3:
                    matched_at = matched_at or st.t_ms
                else:
                    others = [a for k, a in (st.gt.get("actors") or {}).items() if k != subject and a.get("box_xyxy")]
                    if any(_iou(box, a["box_xyxy"]) >= 0.3 for a in others):
                        wrong += 1
            rep = ds.get("last_report")
            if rep and matched_at and abs(rep["ts_ms"] - st.t_ms) < 150:
                gxy = (st.gt.get("states") or {}).get(subject, {}).get("xy")
                if gxy:
                    pad = ds["pad_xy"]
                    true_d = math.hypot(gxy[0] - pad[0], gxy[1] - pad[1])
                    rep_errs.append(abs(rep["distance_m"] - true_d))
        return {**(extra(res, sc) if extra else {}), "matched": int(matched_at is not None),
                "time_to_match_s": round((matched_at - dispatched_at) / 1000.0, 1) if matched_at and dispatched_at else None,
                "wrong_match": wrong, "people_seen": people_seen, "phase_steps": phases,
                "position_reports": len(rep_errs),
                "report_distance_error_m": round(float(np.mean(rep_errs)), 1) if rep_errs else None}
    return run


def _pursuit_eval(subject: str, lookalike: str | None):
    """Scores an autonomous pursuit against simulator ground truth: was the drone ever tracking the look-alike,
    was the subject found again after being lost, and what the routing decisions were."""
    def run(res, sc):
        from ...evaluation.gt_eval import _iou

        following_wrong = verify_steps = follow_steps = in_frame = 0
        lost_at = found_at = None
        for st in res.steps:
            fsm = st.autonomy.get("fsm")
            if st.autonomy.get("mode") != "FOLLOW":
                continue
            follow_steps += 1
            verify_steps += fsm == "VERIFY"
            g = (st.gt.get("actors") or {}).get(subject) or {}
            vis = g.get("visibility", 0) >= 0.5 and g.get("box_xyxy")
            in_frame += bool(vis)
            tid = (st.locks.get("subject") or {}).get("track_id")
            box = next((t["box"] for t in st.tracks if t["id"] == tid and t["box"]), None)
            if fsm == "TRACKING" and lookalike and box is not None:
                d = (st.gt.get("actors") or {}).get(lookalike) or {}
                d_iou = _iou(box, d["box_xyxy"]) if d.get("box_xyxy") else 0.0
                s_iou = _iou(box, g["box_xyxy"]) if g.get("box_xyxy") else 0.0
                if d_iou >= 0.3 and d_iou > s_iou + 0.1:
                    following_wrong += 1
            if lost_at is None and not vis and st.t_ms > 5000:
                lost_at = st.t_ms
            elif lost_at is not None and found_at is None and vis and fsm == "TRACKING":
                found_at = st.t_ms
        d12 = [d for d in res.decisions if d["task"] == "D12_pursuit_route" and not d.get("shadow")]
        return {"belief": _belief_eval(res, d12),
                "follow_steps": follow_steps, "subject_in_frame_fraction": round(in_frame / follow_steps, 3)
                if follow_steps else None,
                "following_lookalike_steps": following_wrong, "verify_hold_steps": verify_steps,
                "lost_at_s": round(lost_at / 1000, 1) if lost_at else None,
                "found_again": int(found_at is not None),
                "found_again_after_s": round((found_at - lost_at) / 1000, 1) if found_at and lost_at else None,
                "d12_decisions": len(d12),
                "d12_picks": [(round(d["request_ts_ms"] / 1000, 1), d["engine"], d["selected"], d["acted"],
                               d["facts"].get("_rules_baseline")) for d in d12][:24],
                "operator_actions": [a for a in res.operator_log if a["action"] in ("confirm_rebind", "redesignate",
                                                                                     "look_for")]}
    return run


def _belief_eval(res, d12: list[dict]) -> dict:
    """What the street-graph belief and the coverage routing did: how many identity hypotheses there were, what D12
    picked and why (coverage per hypothesis), and whether the operator's description changed the route."""
    # hypotheses while searching (an unverified re-binding adds the re-bound person as a hypothesis by design: two
    # hypotheses instead of one merged identity, until a person decides)
    max_h = max((len(st.pursuit["weights"]) for st in res.steps
                 if st.pursuit and st.autonomy.get("fsm") in ("COAST", "REACQUIRE")), default=0)
    max_all = max((len(st.pursuit["weights"]) for st in res.steps if st.pursuit), default=0)
    picks = []
    for d in d12:
        f = d["facts"]
        sel = d["selected"] if d["acted"] else f.get("_rules_baseline")
        c = next((c for c in f.get("candidates", []) if c["id"] == sel), None)
        w = {h["id"]: h["weight"] for h in f.get("hypotheses", [])}
        own = {h: round((c or {}).get("by_hypothesis", {}).get(h, 0.0) / max(wt, 1e-6), 3) for h, wt in w.items()}
        picks.append({"t_s": round(d["request_ts_ms"] / 1000, 1), "pick": sel, "kind": c["kind"] if c else None,
                      "label": c["label"] if c else sel, "coverage": c["coverage"] if c else None,
                      "by_hypothesis": c.get("by_hypothesis") if c else None, "share_of_each_hypothesis": own,
                      "weights": w, "engine": d["engine"], "acted": d["acted"]})
    first = next((p for p in picks if p["pick"] != "hold"), None)
    both = [p for p in picks if len(p["weights"]) >= 2 and p["kind"] is not None
            and sum(1 for h, v in p["share_of_each_hypothesis"].items() if v >= 0.2 and p["weights"][h] >= 0.1) >= 2]
    lf = next((a["t_ms"] / 1000.0 for a in res.operator_log if a["action"] == "look_for"), None)
    before = after = None
    if lf is not None:
        before = next((p for p in reversed(picks) if p["t_s"] <= lf and p["pick"] != "hold"), None)
        after = next((p for p in picks if lf < p["t_s"] <= lf + 8.0 and p["pick"] != "hold"), None)
    return {"max_hypotheses": max_h, "max_hypotheses_with_rebind": max_all, "d12_decisions": len(picks),
            "first_pick": first["pick"] if first else None, "first_pick_label": first["label"] if first else None,
            "first_pick_is_street_ahead": int(bool(first and str(first["label"]).startswith("street ahead"))),
            "route_covers_both": int(bool(both)), "covering_pick": both[0] if both else None,
            "look_for_at_s": lf, "pick_before_look_for": before["pick"] if before else None,
            "pick_after_look_for": after["pick"] if after else None,
            "look_for_changed_route": int(bool(before and after and before["pick"] != after["pick"])),
            "subject_weight_after_look_for": after["weights"].get("H1") if after else None,
            "picks": picks[:30]}
