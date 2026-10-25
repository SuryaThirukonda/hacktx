"""Search and rescue world (Search): woodland with clearings, a trail and a creek; a lost person; decoy objects;
a ground team; four search sectors with a probability-of-area prior; a lawnmower sweep flown by the pilot.

Scale: 1.5 by 1.5 km, the plan's size, around the original 240 m core (where SR1 to SR3 happen; its trees are
placed exactly as before). The ground texture is 30 cm per pixel, so the forest floor is soft at low altitude;
people and objects are drawn as 3D shapes and are unaffected. The ground is flat in the renderer (no heightmap
yet). Lost-person profile numbers are illustrative simulation parameters.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ...autonomy.coverage import footprint_width_m, lawnmower, path_length
from ...geometry import probability as prob
from ..actors import MoveTo, Wait
from ..hazards.grid import Grid
from ..world import BoxObj, TreeObj, World, ZoneSpec, launchpad_objects
from .scoring import actor_found, false_objects, false_tracks

BOUNDS = (-750.0, 750.0, -750.0, 750.0)
CORE = (-120.0, 120.0, -120.0, 120.0)
IPP = (-90.0, -90.0)  # initial planning point: the trailhead where the person was last seen
LAUNCHPAD = (-116.0, -106.0)  # the trailhead car park
TRAIL = [(-110.0, -95.0), (-90.0, -90.0), (-60.0, -70.0), (-30.0, -55.0), (0.0, -40.0), (25.0, -10.0), (40.0, 30.0),
         (70.0, 60.0), (110.0, 80.0)]
CREEK = [(-120.0, 40.0), (-80.0, 30.0), (-40.0, 35.0), (0.0, 20.0), (30.0, 0.0), (60.0, -30.0), (120.0, -50.0)]
CLEARINGS = [(-55.0, -45.0, 14.0), (10.0, -55.0, 12.0), (35.0, 15.0, 13.0), (-20.0, 10.0, 11.0), (70.0, -70.0, 12.0)]
TRAIL_OUT = [(-700.0, -420.0), (-420.0, -260.0), (-110.0, -95.0)] + TRAIL[1:] + [(300.0, 210.0), (700.0, 520.0)]
CREEK_OUT = [(-750.0, 160.0), (-400.0, 90.0), (-120.0, 40.0)] + CREEK[1:] + [(380.0, -190.0), (750.0, -260.0)]
# 3 x 3 sectors of 500 m (S5 in the middle holds the planning point)
SECTORS = {f"S{3 * r + c + 1}": [(-750.0 + 500 * c, -750.0 + 500 * r), (-250.0 + 500 * c, -750.0 + 500 * r),
                                  (-250.0 + 500 * c, -250.0 + 500 * r), (-750.0 + 500 * c, -250.0 + 500 * r)]
           for r in range(3) for c in range(3)}
ALT_M, HFOV_DEG = 30.0, 82.0
# The search feed: a 1080p camera at 5 fps and one full-resolution YOLO pass (driver.load applies the camera,
# Pipeline._ensure_detector the detector). Measured on this forest at 30 m, gimbal -50, YOLO26n on the CPU
# (25 Oct): the old 960x540 feed at imgsz 960 saw the person in 0 of 8 frames (20-26 px tall); 1080p at imgsz 1280
# in 2 of 8 (66 ms), at 1920 in 6 of 8 (191 ms), tiled 1280 px in 8 of 8 (293 ms). At 5 fps the 1920 pass costs
# about a second of CPU per simulated second, so the 2x and 3x speeds still run ahead of real time.
SEARCH_CAMERA = {"width": 1920, "height": 1080, "fps": 5}
SEARCH_PERCEPTION = {"yolo_imgsz": 1920, "tiling": False, "tile_px": 1280}


def _paint(tex, img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    res = tex.res_m
    # woodland floor everywhere, grass only in the clearings
    floor = np.array([46, 82, 70], np.float32)
    mask = np.zeros((h, w), np.float32)
    for cx, cy, r in CLEARINGS:
        px, py = tex.to_px(cx, cy)
        cv2.circle(mask, (int(px), int(py)), int(r / res), 1.0, -1, cv2.LINE_AA)
    mask = cv2.GaussianBlur(mask, (0, 0), 1.5 / res)[..., None]
    shade = (0.85 + 0.3 * rng.random((h // 16 + 1, w // 16 + 1))).astype(np.float32)
    shade = cv2.resize(shade, (w, h), interpolation=cv2.INTER_CUBIC)[..., None]
    img = img * mask + floor[None, None, :] * shade * (1 - mask)
    for cx, cy, r in _outer_clearings():
        px, py = tex.to_px(cx, cy)
        cv2.circle(img, (int(px), int(py)), int(r / res), (58, 118, 84), -1, cv2.LINE_AA)
    img = np.ascontiguousarray(img, dtype=np.float32)
    for line, col, width in ((CREEK_OUT, (120, 108, 92), 3.5), (TRAIL_OUT, (92, 112, 124), 1.6)):
        pts = np.array([tex.to_px(*p) for p in line], np.int32)
        cv2.polylines(img, [pts], False, col, max(1, int(width / res)), cv2.LINE_AA)
    return img


def _near(x: float, y: float, line: list[tuple[float, float]], d: float) -> bool:
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        vx, vy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
        if np.hypot(ax + t * vx - x, ay + t * vy - y) < d:
            return True
    return False


def build_world() -> World:
    w = World(bounds=BOUNDS, texture_res_m=0.3, seed=31)
    w.name = "sar"
    w.painter = _paint
    rng = np.random.default_rng(31)
    trees = []
    while len(trees) < 420:  # the original core, placed exactly as before
        x, y = rng.uniform(-118, 118, 2)
        if any(np.hypot(x - cx, y - cy) < r + 3 for cx, cy, r in CLEARINGS) or _near(x, y, TRAIL, 4.0) or \
                _near(x, y, CREEK, 5.0) or np.hypot(x - IPP[0], y - IPP[1]) < 8:
            continue
        g = int(rng.integers(78, 100))
        trees.append(TreeObj(f"tree-{len(trees)}", (float(x), float(y)), canopy_r=float(rng.uniform(2.2, 3.6)),
                             color=(int(rng.integers(32, 44)), g, int(rng.integers(40, 52)))))
    trees += _outer_forest(len(trees))
    w.trees = trees
    w.boxes = [BoxObj("boulder-1", "rock", (-48.0, -40.0), (1.6, 1.2, 0.9), color=(118, 120, 122)),
               BoxObj("log-1", "log", (14.0, -50.0), (4.0, 0.5, 0.45), heading_deg=30.0, color=(70, 84, 98))]
    w.zones = [ZoneSpec(k, f"Sector {k}", v, kind="sector") for k, v in SECTORS.items()]
    w.launchpad = LAUNCHPAD  # the trailhead car park: the team's base, where a dispatched drone launches from
    w.boxes += launchpad_objects(LAUNCHPAD, "trailhead-pad")
    w.geofence = [(-745, -745), (745, -745), (745, 745), (-745, 745)]
    return w


def _outer_clearings() -> list[tuple[float, float, float]]:
    rng = np.random.default_rng(311)
    out = []
    while len(out) < 40:
        x, y = rng.uniform(-730, 730, 2)
        if not _in_core(x, y, 30.0):
            out.append((float(x), float(y), float(rng.uniform(10, 30))))
    return out


def _in_core(x: float, y: float, m: float = 0.0) -> bool:
    return CORE[0] - m <= x <= CORE[1] + m and CORE[2] - m <= y <= CORE[3] + m


def _outer_forest(start: int) -> list[TreeObj]:
    """Woodland outside the core at the core's density (about 7 trees per 1,000 m2), sparser near trails."""
    rng = np.random.default_rng(312)
    clear = _outer_clearings()
    xs = rng.uniform(-745, 745, 24000)
    ys = rng.uniform(-745, 745, 24000)
    out = []
    for x, y in zip(xs, ys):
        if _in_core(x, y) or any((x - cx) ** 2 + (y - cy) ** 2 < (r + 3) ** 2 for cx, cy, r in clear) or \
                _near(x, y, TRAIL_OUT, 4.0) or _near(x, y, CREEK_OUT, 5.0):
            continue
        g = int(rng.integers(78, 100))
        out.append(TreeObj(f"tree-{start + len(out)}", (float(x), float(y)), canopy_r=float(rng.uniform(2.2, 3.6)),
                           color=(int(rng.integers(32, 44)), g, int(rng.integers(40, 52)))))
        if len(out) >= 15500:
            break
    return out


def sector_poa_after(path_xy: list[tuple[float, float]], spacing_m: float, profile: str = "walk_away") -> dict[str, Any]:
    """Prior POA per sector and the posterior after the flown sweep (POD from coverage); never zero: not clear."""
    g = Grid.over(BOUNDS, 4.0)
    X, Y = g.xy()
    p0 = prob.prior(X, Y, IPP, profile)
    masks = {k: _poly_mask(g, v) for k, v in SECTORS.items()}
    swept = np.zeros_like(p0, bool)
    for (ax, ay), (bx, by) in zip(path_xy, path_xy[1:]):
        n = max(2, int(np.hypot(bx - ax, by - ay) / 2.0))
        for i in range(n + 1):
            x, y = ax + (bx - ax) * i / n, ay + (by - ay) * i / n
            swept |= np.hypot(X - x, Y - y) <= spacing_m / 2.0
    pod = prob.pod(footprint_width_m(ALT_M, HFOV_DEG) * 0.6, spacing_m)  # effective sweep width below footprint
    p1 = prob.update(p0, swept, pod)
    prior_poa = prob.sector_poa(p0, masks)
    post_poa = prob.sector_poa(p1, masks)
    return {"label": "illustrative lost-person profile (simulation parameters, not published statistics)",
            "pod": round(pod, 3), "prior": {k: round(v, 3) for k, v in prior_poa.items()},
            "after_sweep": {k: round(v, 3) for k, v in post_poa.items()},
            "coverage_pct": {k: round(100.0 * float(swept[m].mean()), 1) for k, m in masks.items()}}


def _poly_mask(g: Grid, poly: list[tuple[float, float]]) -> np.ndarray:
    m = np.zeros((g.ny, g.nx), np.uint8)
    pts = np.array([[(x - g.x0) / g.res_m, (y - g.y0) / g.res_m] for x, y in poly], np.int32)
    cv2.fillPoly(m, [pts], 1)
    return m.astype(bool)


def _eval(lost: str, sweep: list[tuple[float, float]], spacing: float):
    def run(res, sc) -> dict[str, Any]:
        from ...decisions.engines import rule_answers
        from ...decisions.tasks import d8_next_sector, prepare

        poa = sector_poa_after(sweep, spacing)
        sectors = [{"id": k, "poa": poa["after_sweep"][k], "coverage_pct": poa["coverage_pct"][k],
                    "distance_m": float(np.hypot(np.mean([p[0] for p in v]) - sweep[-1][0],
                                                 np.mean([p[1] for p in v]) - sweep[-1][1]))}
                   for k, v in SECTORS.items()]
        job = prepare(d8_next_sector({"sectors": sectors}))
        nxt = next(a.choice for a in rule_answers(job) if a.name == "sector")
        return {"lost_person": actor_found(res, lost), "false_candidate_tracks": false_tracks(res),
                "false_candidate_objects": false_objects(res),
                "sweep_length_m": round(path_length(sweep), 1), "track_spacing_m": round(spacing, 1),
                "poa": poa,
                "searched_sector_poa_after": poa["after_sweep"][max(poa["coverage_pct"], key=poa["coverage_pct"].get)],
                "next_sector_rules": nxt}
    return run


def scenarios():
    from ..scenarios import JEANS, MUSTARD, DroneInit, MetricLimit, Scenario, person, responder

    spacing = footprint_width_m(ALT_M, HFOV_DEG) * 0.6  # 40% sidelap
    out = []

    def sweep(poly):
        wps = lawnmower(poly, spacing, heading_deg=0.0)
        return wps

    team = responder((-100.0, -92.0), [Wait(4.0), MoveTo(-60.0, -70.0, 1.2), MoveTo(-30.0, -55.0, 1.2), Wait(999)],
                     heading=60.0)

    # SR1: the lost person sits in a clearing inside the first sector swept
    s1 = sweep([(-80.0, -80.0), (-20.0, -80.0), (-20.0, -20.0), (-80.0, -20.0)])
    out.append(Scenario(
        "SR1", "Search: lost person in a clearing",
        "The pilot sweeps the first sector at 30 m with the camera straight down; the lost person sits in a clearing.",
        duration_s=round(path_length(s1) / 6.0 + 12.0), drone=DroneInit(s1[0][0], s1[0][1] - 8, ALT_M, 0.0, -88.0),
        actors=[team, person("lost", "subject", (-52.0, -47.0), MUSTARD, [Wait(999)], pants=JEANS, height=1.0)],
        limits=[MetricLimit("world.lost_person.found", min=1), MetricLimit("world.false_candidate_objects", max=0),
                MetricLimit("world.searched_sector_poa_after", min=0.001)],
        tags=["world:sar", "search"]))
    out[-1].pilot_path = [(s1[0][0], s1[0][1] - 8, ALT_M, 0.0)] + [(x, y, ALT_M, 6.0) for x, y in s1]
    out[-1].world_eval = _eval("lost", s1, spacing)

    # SR2: decoys (a red jacket and an orange tarp) in the clearings along the sweep
    s2 = sweep([(-10.0, -75.0), (50.0, -75.0), (50.0, -25.0), (-10.0, -25.0)])
    sc2 = Scenario(
        "SR2", "Search: decoys along the sweep",
        "A dropped red jacket and an orange tarp lie in clearings; the lost person is near the log. Decoys become "
        "candidates for review, which is the point: the camera cannot tell a jacket from a person.",
        duration_s=round(path_length(s2) / 6.0 + 12.0), drone=DroneInit(s2[0][0], s2[0][1] - 8, ALT_M, 0.0, -88.0),
        actors=[team, person("lost", "subject", (17.0, -53.0), MUSTARD, [Wait(999)], pants=JEANS, height=1.0)],
        extra_boxes=[BoxObj("decoy-jacket", "debris", (6.0, -58.0), (0.6, 0.45, 0.12), color=(40, 40, 200)),
                     BoxObj("decoy-tarp", "debris", (14.0, -47.0), (2.0, 1.4, 0.06), color=(30, 120, 230))],
        limits=[MetricLimit("world.lost_person.found", min=1), MetricLimit("world.false_candidate_objects", max=2)],
        tags=["world:sar", "search", "decoys"])
    sc2.pilot_path = [(s2[0][0], s2[0][1] - 8, ALT_M, 0.0)] + [(x, y, ALT_M, 6.0) for x, y in s2]
    sc2.world_eval = _eval("lost", s2, spacing)
    out.append(sc2)

    # SR3: the person is in the open in the far clearing of the sector (marked targets are always visible to the
    # drone; the under-canopy case was dropped on 25 Oct)
    s3 = sweep([(-50.0, -15.0), (10.0, -15.0), (10.0, 40.0), (-50.0, 40.0)])
    sc3 = Scenario(
        "SR3", "Search: person in the far clearing",
        "The lost person sits in the open in the clearing at the far side of the sector; the sweep reaches them on "
        "its last legs. The sector's probability drops only where the camera actually looked.",
        duration_s=round(path_length(s3) / 6.0 + 12.0), drone=DroneInit(s3[0][0], s3[0][1] - 8, ALT_M, 0.0, -88.0),
        actors=[team, person("lost", "subject", (-20.0, 10.0), MUSTARD, [Wait(999)], pants=JEANS,
                             height=1.0)],
        limits=[MetricLimit("world.lost_person.found", min=1), MetricLimit("world.searched_sector_poa_after", min=0.001)],
        tags=["world:sar", "search"])
    sc3.pilot_path = [(s3[0][0], s3[0][1] - 8, ALT_M, 0.0)] + [(x, y, ALT_M, 6.0) for x, y in s3]
    sc3.world_eval = _eval("lost", s3, spacing)
    out.append(sc3)

    # SR4: dispatch from the trailhead. The team describes the missing hiker's clothing; the drone launches from the
    # car park, searches the first sector by the trail, tags everyone the detector sees for review, matches the
    # description, holds over the match and reports where they are from the trailhead.
    from ..scenarios import BLUE, DARK, ScriptedAction
    from .pursuit import _dispatch_eval

    dispatch = {"description": "yellow jacket, jeans", "last_seen_xy": (-50.0, -50.0), "radius_m": 40.0, "altitude_m": 30.0}
    sc4 = Scenario(
        "SR4", "Dispatch (autonomous): find the hiker in the yellow jacket",
        "The drone launches from the trailhead car park, searches the sector by the trail for a hiker in a yellow "
        "jacket and jeans, tags everyone it sees for review, matches the description by clothing colour and reports "
        "the position from the trailhead. A second walker in a blue jacket is on the trail.",
        duration_s=150.0, drone=DroneInit(LAUNCHPAD[0], LAUNCHPAD[1], 0.0, 45.0, -60.0),
        actors=[team, person("lost", "subject", (-52.0, -47.0), MUSTARD, [Wait(999)], pants=JEANS, height=1.72),
                person("walker", "bystander", (-30.0, -62.0), BLUE, [Wait(15.0), MoveTo(-60.0, -70.0, 1.1), Wait(999)],
                       pants=DARK, heading=250)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(2.0, "dispatch", dispatch),
                 ScriptedAction(2.5, "operator_watch", "lost")],
        limits=[MetricLimit("world.wrong_match", max=0), MetricLimit("world.matched", min=1),
                MetricLimit("world.lost_person.found", min=1)],
        tags=["world:sar", "search", "autonomy", "dispatch"])
    sc4.dispatch = dispatch
    sc4.world_eval = _dispatch_eval("lost", extra=lambda res, sc: {"lost_person": actor_found(res, "lost"),
                                                                  "false_candidate_objects": false_objects(res)})
    out.append(sc4)

    for s in out:
        s.world = build_world
        # a 1080p feed and the learned detector on a larger input: a person stays a few dozen pixels tall to the
        # model at search altitude, so the sweep does not miss them
        s.detector, s.camera, s.perception = "ultralytics", SEARCH_CAMERA, SEARCH_PERCEPTION
    return out


_CANOPY_XY: tuple[float, float] | None = None


def _under_canopy(w: World | None = None) -> tuple[float, float]:
    """A spot inside sector sweep 3 directly under a large crown."""
    global _CANOPY_XY  # noqa: PLW0603
    if w is None and _CANOPY_XY is not None:
        return _CANOPY_XY
    w = w or build_world()
    best = max((t for t in w.trees if -45 < t.xy[0] < 5 and -10 < t.xy[1] < 35), key=lambda t: t.canopy_r)
    _CANOPY_XY = best.xy
    return best.xy
