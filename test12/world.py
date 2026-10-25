"""The test12 map: the pursuit world's streets (Ash, Oak and Pine Streets; South Road, Main Street and North Road)
with the police station in the south-west corner and many more places to hide.

All cover is real geometry, so the camera is blocked by it in the image, not by a rule:

* dense trees: rows of large street trees along North Road and Oak Street, and a wooded park east of Pine Street;
* carports (roofs on posts) beside houses, a covered walkway on Oak Street and awnings along the downtown strip;
* bus shelters, kiosks and parked box trucks along the streets;
* a downtown strip of 3 to 5 storey buildings on Main Street, and a parking garage with a street entrance.

Map data for the runner and the search: the street graph (intersections and named segments), the sidewalk lanes
people run on, the hiding spots (where each is, how it is reached from the sidewalk, what kind of cover it is) and the
occluders the line-of-sight model uses. The map is fixed; only the people and the crime differ between runs.
Coordinates are local metres, x east, y north; the base (the drone pad) is the origin of every report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from overwatch.sim.texture import GroundTexture, build_ground_texture
from overwatch.sim.world import BoxObj, OpeningDecal, TreeObj, World, launchpad_objects

BOUNDS = (-160.0, 240.0, -150.0, 150.0)
STREETS_X = (-110.0, 30.0, 150.0)  # north-south streets
STREETS_Y = (-95.0, 0.0, 95.0)  # east-west streets
NAMES_X = {-110.0: "Ash St", 30.0: "Oak St", 150.0: "Pine St"}
NAMES_Y = {-95.0: "South Rd", 0.0: "Main St", 95.0: "North Rd"}
ROAD_W = {"ew": 8.0, "ns": 7.0}
LANE_OFFSET = {"ew": 6.0, "ns": 5.5}  # sidewalk centre line from the street centre line
WALK_W = 2.2
PAD = (-138.0, -128.0)  # the drone pad: the origin of every report
CAR_HOME = (-122.0, -128.0)  # the police car's bay, facing the driveway to Ash St
ASH_JOIN = (-110.0, -128.0)  # where the station driveway meets Ash St
DOWNTOWN = (-104.0, 22.0)  # x range of the Main St strip of taller buildings
GARAGE = (53.0, 97.0, 17.0, 47.0)  # x0, x1, y0, y1
GARAGE_DOOR = (60.0, 67.0)  # x range of the entrance in the south wall (facing Main St)
PARK = (158.0, 238.0, -89.0, -9.0)
WALKWAY = (33.7, 37.3, 22.0, 78.0)  # Oak St east sidewalk, covered

GREY_ROOF = (104, 108, 112)
POST = (86, 88, 92)


@dataclass
class HideSpot:
    """A place a person can get out of the drone's sight. ``behind`` spots hide only from one side: the person stands
    on the far side of ``obstacle`` from the drone (the side is chosen when they get there)."""

    sid: str
    kind: str  # carport | walkway | awning | shelter | kiosk | truck | garage | trees
    label: str
    approach: tuple[float, float]  # a point on the sidewalk the spot is reached from
    hide: tuple[float, float]
    behind: bool = False
    obstacle: tuple[float, float, float, float] | None = None  # x0, x1, y0, y1 of what they hide behind

    def as_dict(self) -> dict:
        return {"id": self.sid, "kind": self.kind, "label": self.label, "approach": self.approach, "hide": self.hide,
                "behind": self.behind}


def abox(oid: str, kind: str, x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, color,
         reflective: bool = False) -> BoxObj:
    """An axis-aligned box (heading 0: length along y, width along x)."""
    return BoxObj(oid, kind, ((x0 + x1) / 2, (y0 + y1) / 2), (y1 - y0, x1 - x0, z1 - z0), 0.0, color, z0=z0,
                  reflective=reflective)


def roof_on_posts(prefix: str, kind: str, x0: float, x1: float, y0: float, y1: float, z: float, color=GREY_ROOF,
                  post_every: float = 5.0, posts_on: str = "both") -> list[BoxObj]:
    """A roof slab at height ``z`` on thin posts along its long sides (``posts_on``: both, or the side named by
    "x0", "x1", "y0", "y1" when the other side is fixed to a wall)."""
    out = [abox(f"{prefix}-roof", kind, x0, x1, y0, y1, z, z + 0.22, color)]
    along_x = (x1 - x0) >= (y1 - y0)
    length = (x1 - x0) if along_x else (y1 - y0)
    n = max(1, int(round(length / post_every)))
    ts = np.linspace(0.2, length - 0.2, n + 1)
    sides = {"both": ("lo", "hi"), "x0": ("lo",), "x1": ("hi",), "y0": ("lo",), "y1": ("hi",)}[posts_on]
    for i, t in enumerate(ts):
        for side in sides:
            if along_x:
                px, py = x0 + t, (y0 + 0.15 if side == "lo" else y1 - 0.15)
            else:
                px, py = (x0 + 0.15 if side == "lo" else x1 - 0.15), y0 + t
            out.append(abox(f"{prefix}-post{i}{side}", "post", px - 0.07, px + 0.07, py - 0.07, py + 0.07, 0.0, z, POST))
    return out


def shelter(prefix: str, cx: float, cy: float, along_x: bool, back: int) -> list[BoxObj]:
    """A bus shelter: roof, an opaque back panel on side ``back`` (+1/-1 across the street axis) and one end panel."""
    L, D = 4.0, 1.8
    if along_x:
        x0, x1, y0, y1 = cx - L / 2, cx + L / 2, cy - D / 2, cy + D / 2
        by = y1 - 0.05 if back > 0 else y0 + 0.05
        panels = [abox(f"{prefix}-back", "shelter", x0, x1, by - 0.04, by + 0.04, 0.2, 2.4, (148, 154, 158)),
                  abox(f"{prefix}-end", "shelter", x0, x0 + 0.08, y0, y1, 0.2, 2.4, (148, 154, 158))]
    else:
        x0, x1, y0, y1 = cx - D / 2, cx + D / 2, cy - L / 2, cy + L / 2
        bx = x1 - 0.05 if back > 0 else x0 + 0.05
        panels = [abox(f"{prefix}-back", "shelter", bx - 0.04, bx + 0.04, y0, y1, 0.2, 2.4, (148, 154, 158)),
                  abox(f"{prefix}-end", "shelter", x0, x1, y0, y0 + 0.08, 0.2, 2.4, (148, 154, 158))]
    roof = [abox(f"{prefix}-roof", "shelter", x0 - 0.1, x1 + 0.1, y0 - 0.1, y1 + 0.1, 2.4, 2.58, (112, 118, 124))]
    posts = [abox(f"{prefix}-post{i}", "post", px - 0.06, px + 0.06, py - 0.06, py + 0.06, 0.0, 2.4, POST)
             for i, (px, py) in enumerate([(x0 + 0.1, y0 + 0.1), (x1 - 0.1, y0 + 0.1), (x0 + 0.1, y1 - 0.1),
                                           (x1 - 0.1, y1 - 0.1)])]
    return roof + panels + posts


def kiosk(oid: str, cx: float, cy: float) -> list[BoxObj]:
    s = 1.1
    return [abox(oid, "kiosk", cx - s, cx + s, cy - s, cy + s, 0.0, 2.6, (92, 116, 138)),
            abox(f"{oid}-cap", "kiosk", cx - s - 0.2, cx + s + 0.2, cy - s - 0.2, cy + s + 0.2, 2.6, 2.8, (70, 78, 92))]


def truck(prefix: str, cx: float, cy: float, along_x: bool, facing: int, cab_color) -> tuple[list[BoxObj], tuple]:
    """A box truck parked along a street: a cargo box and a cab (``facing`` +1: the cab at the +x / +y end)."""
    def span(a: float, b: float, w: float) -> tuple[float, float, float, float]:
        lo, hi = (cx + a * facing, cx + b * facing) if along_x else (cy + a * facing, cy + b * facing)
        lo, hi = min(lo, hi), max(lo, hi)
        return (lo, hi, cy - w / 2, cy + w / 2) if along_x else (cx - w / 2, cx + w / 2, lo, hi)
    cargo = span(-3.8, 1.6, 2.5)
    cab = span(1.7, 3.7, 2.3)
    out = [abox(f"{prefix}-box", "truck", *cargo, 0.55, 3.7, (206, 208, 206)),
           abox(f"{prefix}-cab", "truck", *cab, 0.45, 2.75, cab_color),
           abox(f"{prefix}-chassis", "truck", *span(-3.6, 3.6, 2.0), 0.25, 0.55, (40, 42, 46))]
    for i, a in enumerate((-2.8, 2.6)):
        for j, side in enumerate((-1, 1)):
            if along_x:
                x0, x1 = sorted((cx + (a - 0.45) * facing, cx + (a + 0.45) * facing))
                y0, y1 = sorted((cy + side * 1.05, cy + side * 1.3))
            else:
                y0, y1 = sorted((cy + (a - 0.45) * facing, cy + (a + 0.45) * facing))
                x0, x1 = sorted((cx + side * 1.05, cx + side * 1.3))
            out.append(abox(f"{prefix}-wheel{i}{j}", "truck", x0, x1, y0, y1, 0.0, 0.9, (28, 28, 30)))
    hull = (min(cargo[0], cab[0]), max(cargo[1], cab[1]), min(cargo[2], cab[2]), max(cargo[3], cab[3]))
    return out, hull


def garage() -> list[BoxObj]:
    """A parking garage: decks above the ground floor, walls round it and an entrance facing Main St."""
    x0, x1, y0, y1 = GARAGE
    d0, d1 = GARAGE_DOOR
    c = (128, 128, 124)
    deck = abox("garage-decks", "garage", x0, x1, y0, y1, 3.2, 11.0, c)
    for k, (z0, z1) in enumerate(((4.1, 5.6), (7.3, 8.8))):  # the open decks read as dark bands on every face
        deck.openings += [OpeningDecal(f"garage-band{k}s", ((x0 + x1) / 2, y0), (0.0, -1.0), x1 - x0 - 2, z0, z1),
                          OpeningDecal(f"garage-band{k}n", ((x0 + x1) / 2, y1), (0.0, 1.0), x1 - x0 - 2, z0, z1),
                          OpeningDecal(f"garage-band{k}w", (x0, (y0 + y1) / 2), (-1.0, 0.0), y1 - y0 - 2, z0, z1),
                          OpeningDecal(f"garage-band{k}e", (x1, (y0 + y1) / 2), (1.0, 0.0), y1 - y0 - 2, z0, z1)]
    deck.openings.append(OpeningDecal("garage-door-shadow", ((d0 + d1) / 2, y0), (0.0, -1.0), d1 - d0, 0.0, 3.2,
                                      (34, 34, 36)))
    w = 0.4
    walls = [abox("garage-wall-n", "garage", x0, x1, y1 - w, y1, 0.0, 3.2, c),
             abox("garage-wall-w", "garage", x0, x0 + w, y0, y1, 0.0, 3.2, c),
             abox("garage-wall-e", "garage", x1 - w, x1, y0, y1, 0.0, 3.2, c),
             abox("garage-wall-sw", "garage", x0, d0, y0, y0 + w, 0.0, 3.2, c),
             abox("garage-wall-se", "garage", d1, x1, y0, y0 + w, 0.0, 3.2, c)]
    return [deck] + walls


def _house(oid: str, x: float, y: float, along_x: bool, h: float, shade: int) -> BoxObj:
    hw, hd = (5.0, 4.0) if along_x else (4.0, 5.0)
    return abox(oid, "house", x - hw, x + hw, y - hd, y + hd, 0.0, h, (shade, shade + 2, shade + 7))


# ---------------------------------------------------------------------------------------------------------------
def street_graph() -> dict:
    """Intersections and street segments with names, plus the police station's driveway (a service road the police
    car uses; people do not run on it)."""
    xs = (BOUNDS[0],) + STREETS_X + (BOUNDS[1],)
    ys = (BOUNDS[2],) + STREETS_Y + (BOUNDS[3],)
    nodes: dict[str, tuple[float, float]] = {}
    edges: list[dict] = []

    def nid(x: float, y: float) -> str:
        return f"{x:+.0f},{y:+.0f}"

    def add(a: tuple[float, float], b: tuple[float, float], name: str, axis: str, kind: str = "street") -> None:
        nodes[nid(*a)], nodes[nid(*b)] = a, b
        edges.append({"a": nid(*a), "b": nid(*b), "name": name, "axis": axis, "kind": kind})

    for y in STREETS_Y:
        for a, b in zip(xs, xs[1:]):
            add((a, y), (b, y), NAMES_Y[y], "ew")
    for x in STREETS_X:
        col = list(ys)
        if x == -110.0:
            col = sorted(col + [ASH_JOIN[1]])
        for a, b in zip(col, col[1:]):
            add((x, a), (x, b), NAMES_X[x], "ns")
    add(CAR_HOME, ASH_JOIN, "the station driveway", "ew", "service")
    labels: dict[str, str] = {}
    for n, (x, y) in nodes.items():
        if x in NAMES_X and y in NAMES_Y:
            labels[n] = f"{NAMES_X[x]} & {NAMES_Y[y]}"
        elif (x, y) == CAR_HOME:
            labels[n] = "the police station"
        elif x in NAMES_X:
            labels[n] = f"{NAMES_X[x]} at the {'south' if y < 0 else 'north'} edge" if abs(y) >= 149 else NAMES_X[x]
        else:
            labels[n] = f"{NAMES_Y[y]} at the {'west' if x < 0 else 'east'} edge"
    return {"nodes": nodes, "edges": edges, "labels": labels}


def _paint(tex: GroundTexture, img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    res = tex.res_m
    img = np.ascontiguousarray(img, dtype=np.float32)

    def line(a, b, width, col):
        pa, pb = tex.to_px(*a), tex.to_px(*b)
        cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), col, max(1, int(width / res)), cv2.LINE_AA)

    def rect(x0, y0, x1, y1, col):
        p, q = tex.to_px(x0, y1), tex.to_px(x1, y0)
        cv2.rectangle(img, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), col, -1)

    # the park: darker, longer grass with gravel paths
    p0, p1 = tex.to_px(PARK[0], PARK[3]), tex.to_px(PARK[1], PARK[2])
    img[int(p0[1]):int(p1[1]), int(p0[0]):int(p1[0])] *= np.array([0.86, 0.9, 0.86], np.float32)
    for a, b in (((PARK[0], PARK[2]), (PARK[1], PARK[3])), ((PARK[0], PARK[3]), (PARK[1], PARK[2])),
                 (((PARK[0] + PARK[1]) / 2, PARK[2]), ((PARK[0] + PARK[1]) / 2, PARK[3]))):
        line(a, b, 2.4, (118, 150, 166))
    concrete, asphalt = (170, 172, 170), (74, 76, 78)
    for y in STREETS_Y:
        for d in (LANE_OFFSET["ew"], -LANE_OFFSET["ew"]):
            line((BOUNDS[0], y + d), (BOUNDS[1], y + d), WALK_W, concrete)
    for x in STREETS_X:
        for d in (LANE_OFFSET["ns"], -LANE_OFFSET["ns"]):
            line((x + d, BOUNDS[2]), (x + d, BOUNDS[3]), WALK_W, concrete)
    rect(DOWNTOWN[0], 7.0, DOWNTOWN[1], 9.6, (160, 163, 164))  # downtown frontage paving
    rect(DOWNTOWN[0], -9.6, DOWNTOWN[1], -7.0, (160, 163, 164))
    for y in STREETS_Y:
        line((BOUNDS[0], y), (BOUNDS[1], y), ROAD_W["ew"], asphalt)
    for x in STREETS_X:
        line((x, BOUNDS[2]), (x, BOUNDS[3]), ROAD_W["ns"], asphalt)
    rect(PAD[0] - 16, PAD[1] - 14, -113.0, PAD[1] + 14, (96, 98, 100))  # the station's apron
    rect(-114.0, ASH_JOIN[1] - 3.0, -110.0, ASH_JOIN[1] + 3.0, asphalt)  # driveway to Ash St
    rect(GARAGE_DOOR[0], 4.0, GARAGE_DOOR[1], GARAGE[2] + 2, asphalt)  # garage ramp from Main St
    for y in STREETS_Y:  # lane dashes
        for x in np.arange(BOUNDS[0] + 2.0, BOUNDS[1], 6.0):
            if all(abs(x - sx) > 6 for sx in STREETS_X):
                line((x, y), (x + 3.0, y), 0.15, (200, 205, 205))
    for x in STREETS_X:
        for y in np.arange(BOUNDS[2] + 2.0, BOUNDS[3], 6.0):
            if all(abs(y - sy) > 6 for sy in STREETS_Y):
                line((x, y), (x, y + 3.0), 0.15, (200, 205, 205))
    for x in STREETS_X:  # zebra crossings on every approach to an intersection
        for y in STREETS_Y:
            for k in np.linspace(-2.6, 2.6, 6):
                line((x + k, y + 7.4), (x + k, y + 9.6), 0.45, (214, 218, 218))
                line((x + k, y - 7.4), (x + k, y - 9.6), 0.45, (214, 218, 218))
                line((x + 7.0, y + k), (x + 9.2, y + k), 0.45, (214, 218, 218))
                line((x - 7.0, y + k), (x - 9.2, y + k), 0.45, (214, 218, 218))
    return img


def build_world() -> tuple[World, list[HideSpot], dict]:
    """The world (geometry for the renderer), the hiding spots and extra layout for the map page."""
    w = World(bounds=BOUNDS, texture_res_m=0.08, seed=1212)
    w.name = "test12-pursuit"
    w.painter = _paint
    w.boxes, w.trees = [], []
    spots: list[HideSpot] = []
    rng = np.random.default_rng(1212)
    occupied: list[tuple[float, float, float, float]] = []  # footprints kept clear of houses and trees

    def keep_clear(x0, x1, y0, y1):
        occupied.append((x0, x1, y0, y1))

    def clear(x: float, y: float, r: float) -> bool:
        return not any(x0 - r <= x <= x1 + r and y0 - r <= y <= y1 + r for x0, x1, y0, y1 in occupied)

    # --- the base: drone pad and hangar, the police station, the apron -------------------------------------------
    w.launchpad = PAD
    w.boxes += launchpad_objects(PAD, "base-pad")
    st = abox("police-station", "building", -150.0, -124.0, -114.0, -100.0, 0.0, 8.0, (150, 118, 92))
    st.openings += [OpeningDecal("station-door", (-137.0, -114.0), (0.0, -1.0), 3.0, 0.0, 2.6, (60, 50, 44))]
    for k, z in enumerate((3.0, 5.6)):
        st.openings.append(OpeningDecal(f"station-win{k}", (-137.0, -114.0), (0.0, -1.0), 22.0, z, z + 1.3,
                                        (88, 76, 66)))
    w.boxes.append(st)
    keep_clear(-160.0, -106.0, -150.0, -92.0)

    # --- downtown: 3 to 5 storey buildings along Main St with awnings, kiosks and a shelter ---------------------
    north = [(-102, -80, 12.0), (-76, -56, 16.0), (-52, -30, 10.5), (-26, -8, 14.0), (-4, 20, 17.0)]
    south = [(-102, -84, 14.0), (-80, -58, 10.5), (-54, -34, 17.0), (-30, -6, 12.0), (-2, 20, 15.0)]
    tints = [(120, 124, 130), (108, 112, 122), (132, 128, 124), (112, 118, 126), (126, 120, 116)]
    for side, rows in ((1, north), (-1, south)):
        for i, (x0, x1, h) in enumerate(rows):
            y0, y1 = (9.6, 26.0) if side > 0 else (-26.0, -9.6)
            b = abox(f"dt-{'n' if side > 0 else 's'}{i}", "building", x0, x1, y0, y1, 0.0, h, tints[(i + (side < 0)) % 5])
            face_y = y0 if side > 0 else y1
            for k, z in enumerate(np.arange(3.6, h - 1.0, 3.4)):  # a band of windows per storey on the street face
                b.openings.append(OpeningDecal(f"{b.oid}-win{k}", ((x0 + x1) / 2, face_y), (0.0, -float(side)),
                                               (x1 - x0) - 2.5, float(z), float(z) + 1.4, (70, 66, 60)))
            w.boxes.append(b)
    keep_clear(DOWNTOWN[0], DOWNTOWN[1], -30.0, 30.0)
    for oid, (x0, x1), side in (("awning-n0", (-101.0, -81.0), 1), ("awning-n2", (-51.0, -31.0), 1),
                                ("awning-n4", (-3.0, 19.0), 1), ("awning-s1", (-79.0, -59.0), -1),
                                ("awning-s3", (-29.0, -7.0), -1)):
        y0, y1 = (4.6, 9.6) if side > 0 else (-9.6, -4.6)
        w.boxes += roof_on_posts(oid, "awning", x0, x1, y0, y1, 3.0, (96, 102, 112), 5.0, "y0" if side > 0 else "y1")
        cx = (x0 + x1) / 2
        spots.append(HideSpot(oid, "awning", f"under the shop awning on Main St (x {cx:.0f})",
                              (cx, 6.0 * side), (cx, 7.4 * side)))
    for oid, cx, cy in (("kiosk-dt1", -78.0, 8.3), ("kiosk-dt2", -56.0, -8.3), ("kiosk-park", 158.4, -50.0)):
        w.boxes += kiosk(oid, cx, cy)
        spots.append(HideSpot(oid, "kiosk", "behind a kiosk on " + ("Main St" if abs(cy) < 20 else "Pine St"),
                              (cx if abs(cy) < 20 else 155.5, 6.0 * np.sign(cy) if abs(cy) < 20 else cy), (cx, cy),
                              behind=True, obstacle=(cx - 1.1, cx + 1.1, cy - 1.1, cy + 1.1)))

    # --- the parking garage and the covered walkway on Oak St ---------------------------------------------------
    w.boxes += garage()
    keep_clear(GARAGE[0] - 4, GARAGE[1] + 4, GARAGE[2] - 6, GARAGE[3] + 4)
    door_x = (GARAGE_DOOR[0] + GARAGE_DOOR[1]) / 2
    spots.append(HideSpot("garage", "garage", "inside the parking garage on Main St", (door_x, 6.0),
                          (door_x, GARAGE[2] + 8.0)))
    x0, x1, y0, y1 = WALKWAY
    w.boxes += roof_on_posts("walkway", "walkway", x0, x1, y0, y1, 2.6, GREY_ROOF, 6.0)
    keep_clear(x0 - 1, x1 + 6, y0, y1)
    for k, y in enumerate((34.0, 62.0)):
        spots.append(HideSpot(f"walkway-{k}", "walkway", "under the covered walkway on Oak St", (35.5, y), (35.5, y)))

    # --- bus shelters --------------------------------------------------------------------------------------------
    for oid, cx, cy, along_x, back, lane in (("shelter-dt", -44.0, -8.3, True, -1, (-44.0, -6.0)),
                                             ("shelter-north", -40.0, 87.0, True, -1, (-40.0, 89.0)),
                                             ("shelter-south", 100.0, -87.0, True, 1, (100.0, -89.0)),
                                             ("shelter-pine", 141.6, 50.0, False, -1, (144.5, 50.0)),
                                             ("shelter-ash", -102.4, -50.0, False, 1, (-104.5, -50.0))):
        w.boxes += shelter(oid, cx, cy, along_x, back)
        keep_clear(cx - 2.6, cx + 2.6, cy - 2.6, cy + 2.6)
        street = "Main St" if abs(cy) < 20 else ("North Rd" if cy > 80 else "South Rd" if cy < -80 else
                                                 "Pine St" if cx > 100 else "Ash St")
        spots.append(HideSpot(oid, "shelter", f"in the bus shelter on {street}", lane, (cx, cy)))

    # --- box trucks parked at the curb -------------------------------------------------------------------------
    for i, (cx, cy, along_x, facing, cab) in enumerate(((100.0, -3.0, True, 1, (60, 70, 140)),
                                                         (-50.0, -92.0, True, -1, (120, 90, 60)),
                                                         (-107.4, 40.0, False, 1, (70, 110, 70)),
                                                         (152.6, 64.0, False, -1, (150, 150, 150)),
                                                         (80.0, 98.0, True, 1, (90, 60, 50)))):
        parts, hull = truck(f"truck-{i}", cx, cy, along_x, facing, cab)
        w.boxes += parts
        keep_clear(hull[0] - 1, hull[1] + 1, hull[2] - 1, hull[3] + 1)
        lane = (cx, cy + (6.0 - 3.0) * np.sign(cy - round(cy / 95.0) * 95.0)) if along_x else \
            (cx + (5.5 - 2.6) * np.sign(cx - min(STREETS_X, key=lambda s: abs(s - cx))), cy)
        spots.append(HideSpot(f"truck-{i}", "truck", "behind a parked box truck", (float(lane[0]), float(lane[1])),
                              ((hull[0] + hull[1]) / 2, (hull[2] + hull[3]) / 2), behind=True, obstacle=hull))

    # --- the wooded park east of Pine St, with big trees over the sidewalks round it ---------------------------
    keep_clear(PARK[0] - 3, PARK[1] + 2, PARK[2] - 2, PARK[3] + 2)
    k = 0
    for gx in np.arange(PARK[0] + 8, PARK[1] - 4, 9.5):
        for gy in np.arange(PARK[2] + 8, PARK[3] - 4, 9.5):
            x, y = gx + rng.uniform(-2.5, 2.5), gy + rng.uniform(-2.5, 2.5)
            cz = float(rng.uniform(5.6, 7.0))
            w.trees.append(TreeObj(f"park-tree-{k}", (float(x), float(y)), trunk_r=0.26, trunk_h=cz - 1.8,
                                   canopy_r=float(rng.uniform(3.6, 5.0)), canopy_z=cz,
                                   color=(int(rng.integers(34, 46)), int(rng.integers(80, 100)), int(rng.integers(42, 54)))))
            k += 1
    edge_trees = [(159.6, y) for y in np.arange(-84.0, -10.0, 10.0)] + \
                 [(x, -10.8) for x in np.arange(166.0, 236.0, 10.0)] + [(x, -84.6) for x in np.arange(166.0, 236.0, 10.0)]
    for x, y in edge_trees:
        w.trees.append(TreeObj(f"park-edge-{k}", (float(x), float(y)), trunk_r=0.28, trunk_h=4.0, canopy_r=5.0,
                               canopy_z=6.0, color=(38, int(rng.integers(84, 98)), 48)))
        k += 1
    for sid, approach, hide in (("park-pine-n", (155.5, -26.0), (159.6, -24.0)),
                                ("park-pine-s", (155.5, -66.0), (159.6, -64.0)),
                                ("park-main", (196.0, -6.0), (196.0, -10.8)),
                                ("park-south", (216.0, -89.0), (216.0, -84.6))):
        spots.append(HideSpot(sid, "trees", "under the big trees at the park's edge", approach, hide))

    # --- dense street-tree rows on North Rd and Oak St ----------------------------------------------------------
    def tree_ok(x: float, y: float) -> bool:
        return clear(x, y, 3.0) and all(math.hypot(x - sx, y - sy) > 14 for sx in STREETS_X for sy in STREETS_Y)

    for x in np.arange(-102.0, 146.0, 14.0):
        for side in (1, -1):
            y = 95.0 + side * 9.0
            if tree_ok(x, y):
                w.trees.append(TreeObj(f"north-tree-{k}", (float(x), float(y)), trunk_h=3.0, canopy_r=4.0,
                                       canopy_z=4.8, color=(40, int(rng.integers(86, 100)), 50)))
                k += 1
    for y in np.arange(-142.0, 146.0, 14.0):
        for side in (1, -1):
            x = 30.0 + side * 8.5
            if tree_ok(x, y) and not (side < 0 and -32 < y < 32):
                w.trees.append(TreeObj(f"oak-tree-{k}", (float(x), float(y)), trunk_h=3.0, canopy_r=3.8,
                                       canopy_z=4.6, color=(42, int(rng.integers(84, 98)), 48)))
                k += 1
    tree_rows = list(w.trees)
    for t in tree_rows:
        if t.oid.startswith(("north-tree", "oak-tree")):
            keep_clear(t.xy[0] - 1, t.xy[0] + 1, t.xy[1] - 1, t.xy[1] + 1)
    row_spots = [t for t in tree_rows if t.oid.startswith(("north-tree", "oak-tree"))][::5]
    for t in row_spots:
        ns = t.oid.startswith("oak-tree")
        lane = (30.0 + 5.5 * np.sign(t.xy[0] - 30.0), t.xy[1]) if ns else (t.xy[0], 95.0 + 6.0 * np.sign(t.xy[1] - 95.0))
        hide = (float(lane[0] + (t.xy[0] - lane[0]) * 0.6), float(lane[1] + (t.xy[1] - lane[1]) * 0.6))
        spots.append(HideSpot(f"tree-{t.oid}", "trees", f"under the street trees on {'Oak St' if ns else 'North Rd'}",
                              (float(lane[0]), float(lane[1])), hide))

    # --- houses along the other streets, with carports beside a third of them ----------------------------------
    hk = ck = 0
    lots = []
    for sy in STREETS_Y:
        for x in np.arange(BOUNDS[0] + 12, BOUNDS[1] - 12, 22.0):
            for side in (1, -1):
                lots.append(("ew", float(x), sy, side))
    for sx in STREETS_X:
        for y in np.arange(BOUNDS[2] + 12, BOUNDS[3] - 12, 22.0):
            for side in (1, -1):
                lots.append(("ns", sx, float(y), side))
    taken: list[tuple[float, float]] = []
    for axis, a, b, side in lots:
        x, y = (a, b + side * 21.0) if axis == "ew" else (a + side * 21.0, b)
        if not (BOUNDS[0] + 8 < x < BOUNDS[1] - 8 and BOUNDS[2] + 8 < y < BOUNDS[3] - 8):
            continue
        if any(abs(x - sx) < 16 for sx in STREETS_X) and any(abs(y - sy) < 16 for sy in STREETS_Y):
            continue  # corners stay open
        if any(abs(y - sy) < 13 for sy in STREETS_Y) or any(abs(x - sx) < 13 for sx in STREETS_X):
            continue
        if not clear(x, y, 6.0) or any(abs(x - p) < 13 and abs(y - q) < 13 for p, q in taken):
            continue
        taken.append((x, y))
        hk += 1
        w.boxes.append(_house(f"house-{hk}", x, y, axis == "ew", float(rng.uniform(5.0, 7.0)), int(rng.integers(104, 134))))
        if rng.random() < 0.38:
            ck += 1
            if axis == "ew":
                cx, cy = x + 8.2, b + side * 18.0
                cx0, cx1, cy0, cy1 = cx - 2.1, cx + 2.1, cy - 3.0, cy + 3.0
                lane = (cx, b + side * LANE_OFFSET["ew"])
            else:
                cx, cy = a + side * 18.0, y + 8.2
                cx0, cx1, cy0, cy1 = cx - 3.0, cx + 3.0, cy - 2.1, cy + 2.1
                lane = (a + side * LANE_OFFSET["ns"], cy)
            if clear(cx, cy, 1.0):
                w.boxes += roof_on_posts(f"carport-{ck}", "carport", cx0, cx1, cy0, cy1, 2.45, GREY_ROOF, 5.6)
                street = NAMES_Y[b] if axis == "ew" else NAMES_X[a]
                spots.append(HideSpot(f"carport-{ck}", "carport", f"under a carport on {street}", lane, (cx, cy)))
        if rng.random() < 0.3:  # a car in the parking lane in front of the house
            g = int(rng.integers(60, 190))
            if axis == "ew":
                car = BoxObj(f"car-{hk}", "vehicle", (x + float(rng.uniform(-5, 5)), b + side * 2.9), (4.5, 1.9, 1.5),
                             heading_deg=90.0, color=(g, g + 2, g + 4), reflective=True)
            else:
                car = BoxObj(f"car-{hk}", "vehicle", (a + side * 2.5, y + float(rng.uniform(-5, 5))), (4.5, 1.9, 1.5),
                             heading_deg=0.0, color=(g, g + 2, g + 4), reflective=True)
            if clear(car.center_xy[0], car.center_xy[1], 3.0):
                w.boxes.append(car)
    w.streets = street_graph()
    w.geofence = [(BOUNDS[0] + 4, BOUNDS[2] + 4), (BOUNDS[1] - 4, BOUNDS[2] + 4), (BOUNDS[1] - 4, BOUNDS[3] - 4),
                  (BOUNDS[0] + 4, BOUNDS[3] - 4)]
    layout = {"park": PARK, "garage": GARAGE, "garage_door": GARAGE_DOOR, "downtown": DOWNTOWN, "walkway": WALKWAY}
    return w, spots, layout


@lru_cache(maxsize=1)
def world_bundle() -> tuple[World, list[HideSpot], dict, GroundTexture]:
    """The map is the same for every run: built once per process. The 5000 x 3750 ground texture takes about 6 s to
    paint, so it is kept in ``data/cache`` (keyed by the code that paints it) and memory-mapped by every process that
    draws frames."""
    w, spots, layout = build_world()
    return w, spots, layout, _ground_texture(w)


def _ground_texture(w: World) -> GroundTexture:
    import hashlib
    import inspect
    import os

    from overwatch.sim import texture as tx

    from . import REPO_ROOT

    src = inspect.getsource(tx) + inspect.getsource(_paint) + repr((w.bounds, w.texture_res_m, w.seed))
    path = REPO_ROOT / "data" / "cache" / f"test12-ground-{hashlib.sha1(src.encode()).hexdigest()[:12]}.npy"
    x0, x1, y0, y1 = w.bounds
    if path.exists():
        try:
            return GroundTexture(np.load(path, mmap_mode="r"), w.texture_res_m, x0, y1)
        except (OSError, ValueError):
            pass  # a half-written or stale file: paint it again
    tex = build_ground_texture(w)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp.npy")
        np.save(tmp, tex.image)
        os.replace(tmp, path)
    except OSError:
        pass  # a read-only checkout still works, it just paints the texture every time
    return tex


def to_base(xy) -> tuple[float, float]:
    """World metres to the base frame (the drone pad is the origin)."""
    return float(xy[0] - PAD[0]), float(xy[1] - PAD[1])
