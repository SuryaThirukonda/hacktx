"""Camera renderer for test12: the simulator's renderer plus a per-pixel depth test for people and the police car.

The base renderer (``overwatch.sim.render``) draws everything with a painter's algorithm by face-centre distance, so a
person standing under the edge of a roof can show through it. Here the static scene is still drawn that way (it only
decides which roof looks on top of which), but people and the police car are drawn afterwards, pixel by pixel, only
where they are nearer the camera than every static surface on that pixel: under a carport roof, behind a truck or
inside the garage they are hidden exactly as far as the geometry hides them. The id buffer (ground truth for scoring)
is written the same way, so a hidden person really has no pixels.

The drone itself (``drone_model``, a DJI Mini 2 to scale) is drawn the same way, only in the simulator's own
third-person and bird's-eye views, never in the drone's camera.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from overwatch.geometry.camera import PinholeCamera
from overwatch.sim.actors import Actor
from overwatch.sim.render import (_PART_ORDER, _PART_SHADE, NEAR, STATIC_ID, RenderEffects, RenderResult,
                                  SceneRenderer, _clip_near, _Item, _shade)
from overwatch.sim.world import BoxObj

VEHICLE_ID = 200
DRONE_ID = 201
WHITE, BLACK, GLASS = (232, 234, 236), (30, 30, 34), (74, 62, 56)
LIGHT_RED, LIGHT_BLUE = (36, 36, 236), (236, 96, 28)
TYRE, HUB = (24, 24, 26), (150, 152, 156)


@dataclass
class Face:
    """One planar polygon of a moving mesh, in world coordinates, with its outward normal."""

    pts: np.ndarray  # (k, 3)
    color: tuple[int, int, int]
    normal: np.ndarray


@dataclass
class Vehicle:
    faces: list[Face]
    shadow: BoxObj  # a box the size of the body, only used to cast its shadow
    ident: int = VEHICLE_ID


def _clip_x(poly: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    """Clip a polygon in the car's side plane (x along the car, z up) to lo <= x <= hi."""
    def clip(pts, keep, edge):
        out = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            ka, kb = keep(a), keep(b)
            if ka:
                out.append(a)
            if ka != kb:
                t = (edge - a[0]) / (b[0] - a[0])
                out.append((edge, a[1] + t * (b[1] - a[1])))
        return out
    pts = clip(poly, lambda p: p[0] >= lo, lo)
    return clip(pts, lambda p: p[0] <= hi, hi) if pts else []


def police_car(x: float, y: float, heading_deg: float) -> Vehicle:
    """A low-poly patrol sedan (4.9 x 1.9 x 1.45 m): a real side profile with a sloped windshield and rear window,
    dark glass, wheels with hubs, head- and taillights, black-and-white livery (black nose, tail and fenders, white
    doors and roof) and a red and blue light bar. Heading is degrees clockwise from north, like everything else."""
    h = math.radians(heading_deg)
    fwd = np.array([math.sin(h), math.cos(h), 0.0])
    left = np.array([-math.cos(h), math.sin(h), 0.0])
    up = np.array([0.0, 0.0, 1.0])
    origin = np.array([x, y, 0.0])

    def P(a: float, s: float, z: float) -> np.ndarray:  # a along the car, s to its left, z up
        return origin + fwd * a + left * s + up * z

    centre = P(0.0, 0.0, 0.75)
    faces: list[Face] = []

    def face(pts: list[np.ndarray], color) -> None:
        arr = np.array(pts, float)
        if len(arr) < 3:
            return
        n = np.cross(arr[1] - arr[0], arr[2] - arr[0])
        if np.linalg.norm(n) < 1e-9:
            n = np.cross(arr[2] - arr[0], arr[-1] - arr[0])
        n = n / max(np.linalg.norm(n), 1e-9)
        if np.dot(n, arr.mean(axis=0) - centre) < 0:
            n = -n
        faces.append(Face(arr, color, n))

    # side profile (a along the car, z): bumper, hood, beltline, trunk
    lower = [(2.45, 0.28), (2.45, 0.66), (2.25, 0.82), (1.0, 0.95), (-1.6, 0.97), (-2.35, 0.92), (-2.45, 0.70),
             (-2.45, 0.28)]
    for s in (0.95, -0.95):
        for lo, hi, col in ((1.25, 2.5, BLACK), (-1.15, 1.25, WHITE), (-2.5, -1.15, BLACK)):
            seg = _clip_x(lower, lo, hi)
            face([P(a, s, z) for a, z in seg], col)
        face([P(1.0, s * 0.84, 0.95), P(0.35, s * 0.78, 1.40), P(-0.85, s * 0.78, 1.40), P(-1.6, s * 0.84, 0.97)], GLASS)
        for a in (1.5, -1.5):  # wheels: a tyre disc with a hub, just outside the body
            ang = np.linspace(0, 2 * np.pi, 14, endpoint=False)
            face([P(a + 0.36 * math.cos(t), s * 1.0, 0.36 + 0.36 * math.sin(t)) for t in ang], TYRE)
            face([P(a + 0.17 * math.cos(t), s * 1.01, 0.36 + 0.17 * math.sin(t)) for t in ang], HUB)
    tops = [((2.45, 0.66), (2.25, 0.82), 0.95, BLACK),  # nose
            ((2.25, 0.82), (1.0, 0.95), 0.95, BLACK),  # hood
            ((1.0, 0.95), (0.35, 1.40), 0.82, GLASS),  # windshield
            ((0.35, 1.40), (-0.85, 1.40), 0.78, WHITE),  # roof
            ((-0.85, 1.40), (-1.6, 0.97), 0.82, GLASS),  # rear window
            ((-1.6, 0.97), (-2.35, 0.92), 0.95, BLACK),  # trunk lid
            ((-2.35, 0.92), (-2.45, 0.70), 0.95, BLACK)]  # tail
    for (a0, z0), (a1, z1), half, col in tops:
        face([P(a0, half, z0), P(a1, half, z1), P(a1, -half, z1), P(a0, -half, z0)], col)
    for s in (1, -1):  # the beltline ledge between the doors and the narrower glasshouse
        face([P(1.0, s * 0.95, 0.95), P(-1.6, s * 0.95, 0.97), P(-1.6, s * 0.84, 0.97), P(1.0, s * 0.84, 0.95)], WHITE)
    face([P(2.45, 0.95, 0.28), P(2.45, 0.95, 0.66), P(2.45, -0.95, 0.66), P(2.45, -0.95, 0.28)], BLACK)  # front
    face([P(-2.45, 0.95, 0.28), P(-2.45, 0.95, 0.70), P(-2.45, -0.95, 0.70), P(-2.45, -0.95, 0.28)], BLACK)  # back
    for s in (1, -1):  # headlights and taillights
        face([P(2.46, s * 0.55, 0.50), P(2.46, s * 0.55, 0.62), P(2.46, s * 0.85, 0.62), P(2.46, s * 0.85, 0.50)],
             (210, 236, 240))
        face([P(-2.46, s * 0.55, 0.52), P(-2.46, s * 0.55, 0.66), P(-2.46, s * 0.88, 0.66), P(-2.46, s * 0.88, 0.52)],
             (40, 40, 190))
    # the light bar across the roof: red on the driver's side, blue on the other
    for col, s0, s1 in ((LIGHT_RED, 0.02, 0.66), (LIGHT_BLUE, -0.66, -0.02)):
        a0, a1, z0, z1 = -0.25, 0.1, 1.40, 1.58
        c = [P(a, s, z) for a in (a0, a1) for s in (s0, s1) for z in (z0, z1)]
        for idx in ((1, 3, 7, 5), (0, 2, 6, 4), (2, 3, 7, 6), (0, 1, 5, 4), (4, 5, 7, 6), (0, 1, 3, 2)):
            face([c[i] for i in idx], col)
    shadow = BoxObj("police-car-shadow", "police_car", (x, y), (4.9, 1.9, 1.45), heading_deg, BLACK)
    return Vehicle(faces, shadow)


MINI2_BODY, MINI2_DARK, MINI2_PROP = (204, 206, 208), (58, 60, 64), (118, 120, 124)


def drone_model(x: float, y: float, z: float, heading_deg: float) -> Vehicle:
    """The DJI Mini 2 to scale: a 14 x 7.5 x 5 cm body, four arms out to motors on a 21 cm diagonal, 12 cm propeller
    discs (spinning, so drawn as grey discs), and the gimbal camera under the nose: about 27 x 29 cm in all."""
    h = math.radians(heading_deg)
    fwd = np.array([math.sin(h), math.cos(h), 0.0])
    left = np.array([-math.cos(h), math.sin(h), 0.0])
    up = np.array([0.0, 0.0, 1.0])
    origin = np.array([x, y, z])

    def P(a: float, s: float, zz: float) -> np.ndarray:  # a forward, s to the left, zz up, from the body's centre
        return origin + fwd * a + left * s + up * zz

    faces: list[Face] = []

    def box(a0: float, a1: float, s0: float, s1: float, z0: float, z1: float, color) -> None:
        c = [P(a, sv, zz) for a in (a0, a1) for sv in (s0, s1) for zz in (z0, z1)]
        mid = P((a0 + a1) / 2, (s0 + s1) / 2, (z0 + z1) / 2)
        for idx in ((1, 3, 7, 5), (0, 2, 6, 4), (2, 3, 7, 6), (0, 1, 5, 4), (4, 5, 7, 6), (0, 1, 3, 2)):
            pts = np.array([c[i] for i in idx])
            n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
            n = n / max(float(np.linalg.norm(n)), 1e-12)
            if np.dot(n, pts.mean(axis=0) - mid) < 0:
                n = -n
            faces.append(Face(pts, color, n))

    def flat(pts: list[np.ndarray], color) -> None:  # a horizontal polygon seen from above
        faces.append(Face(np.array(pts, float), color, up.copy()))

    box(-0.07, 0.07, -0.0375, 0.0375, -0.02, 0.03, MINI2_BODY)
    box(0.07, 0.095, -0.016, 0.016, -0.04, -0.012, MINI2_DARK)  # the gimbal camera under the nose
    ring = np.linspace(0.0, 2 * np.pi, 16, endpoint=False)
    for a, sv in ((0.068, 0.085), (0.068, -0.085), (-0.07, 0.075), (-0.07, -0.075)):
        root = np.array([math.copysign(0.05, a), math.copysign(0.03, sv)])
        d = np.array([a, sv]) - root
        perp = np.array([-d[1], d[0]]) / float(np.linalg.norm(d)) * 0.008
        flat([P(*(root + perp), 0.012), P(*(root - perp), 0.012), P(*(np.array([a, sv]) - perp), 0.012),
              P(*(np.array([a, sv]) + perp), 0.012)], MINI2_DARK)  # the arm
        box(a - 0.012, a + 0.012, sv - 0.012, sv + 0.012, 0.0, 0.03, MINI2_DARK)  # the motor
        flat([P(a + 0.062 * math.cos(u), sv + 0.062 * math.sin(u), 0.034) for u in ring], MINI2_PROP)
    shadow = BoxObj("drone-shadow", "drone", (x, y), (0.24, 0.24, 0.05), heading_deg, BLACK, z0=z)
    return Vehicle(faces, shadow, DRONE_ID)


@dataclass
class VehicleGT:
    box_xyxy: tuple[float, float, float, float] | None
    visibility: float
    in_frame: bool


class DepthRenderer(SceneRenderer):
    """``render(cam, actors, vehicles=[boxes])``: people and vehicles depth-tested against the static scene."""

    def __init__(self, world, texture) -> None:
        super().__init__(world, texture)
        self._shadow_extra: list[BoxObj] = []
        self._geo: dict[int, list] = {}  # static boxes never move: their parts' corners and faces, computed once

    def _parts(self, bx: BoxObj) -> list[tuple[BoxObj, np.ndarray, list[tuple[list[int], np.ndarray, np.ndarray]]]]:
        """(part, corners, [(face vertex indices, outward normal, face centre)]): a parked car is a body and a cabin."""
        g = self._geo.get(id(bx))
        if g is not None:
            return g
        if bx.kind == "vehicle":
            L, Wd, Hh = bx.size_lwh
            body = BoxObj(bx.oid, bx.kind, bx.center_xy, (L, Wd, Hh * 0.55), bx.heading_deg, bx.color)
            h = math.radians(bx.heading_deg)
            off = np.array([math.sin(h), math.cos(h)]) * (-0.15 * L)
            cabin = BoxObj(bx.oid, bx.kind, (bx.center_xy[0] + off[0], bx.center_xy[1] + off[1]),
                           (L * 0.55, Wd * 0.9, Hh * 0.45), bx.heading_deg, _shade(bx.color, 0.55), z0=Hh * 0.55)
            parts = [body, cabin]
        else:
            parts = [bx]
        g = []
        for part in parts:
            cs = part.corners()
            g.append((part, cs, [(face, n, cs[face].mean(axis=0)) for face, n in part.faces()]))
        self._geo[id(bx)] = g
        return g

    def _boxes(self) -> list:
        base = super()._boxes()
        return base + self._shadow_extra if self._shadow_extra else base

    # ---------------------------------------------------------------------------------------------------------
    def render(self, cam: PinholeCamera, actors: list[Actor], effects: RenderEffects | None = None,
               ground_level: bool = False, with_gt: bool = True,
               vehicles: list[Vehicle] | None = None) -> RenderResult:
        fx = effects or RenderEffects()
        W, H = cam.intr.width, cam.intr.height
        vehicles = vehicles or []
        self._visible = self._view_set(cam) if self._cull else None
        img = self._render_ground(cam, ground_level, fx)
        id_mask = np.zeros((H, W), np.uint8)
        self._shadow_extra = [v.shadow for v in vehicles]
        try:
            self._render_shadows(cam, img, actors)
        finally:
            self._shadow_extra = []
        static: list[_Item] = []
        for bx in super()._boxes():
            static.extend(self._box_items(cam, bx))
        static.extend(self._tree_items_depth(cam, self._trees()))
        static.sort(key=lambda it: -it.depth)
        for it in static:
            self._draw(img, None, it)
        boxes2d = np.array([self._bbox(it) for it in static], float).reshape(-1, 4)
        # people: their parts in painter's order among themselves, each pixel tested against the static depth
        actor_parts: dict[int, list[_Item]] = {}
        dyn: list[tuple[_Item, int]] = []
        for idx, a in enumerate(actors):
            if not a.active:
                continue
            ident = idx + 1
            plist = self._person_items(cam, a, ident)
            actor_parts[ident] = plist
            dyn.extend((it, ident) for it in plist)
        vparts: list[_Item] = []
        for v in vehicles:
            vparts.extend(self._face_items(cam, v.faces, v.ident))
        dyn.extend((it, it.ident) for it in vparts)
        groups: dict[int, list[_Item]] = {}
        for it, ident in dyn:
            groups.setdefault(ident, []).append(it)
        zstatic: dict[int, tuple[tuple[int, int, int, int], np.ndarray]] = {}
        for ident, items in groups.items():
            roi = self._roi([self._bbox(it) for it in items], W, H)
            if roi is not None:
                zstatic[ident] = (roi, self._depth_in_roi(cam, static, boxes2d, roi))
        # far to near; a z-buffer among the moving things too (a vehicle's light bar sits a hair above its roof, which
        # painter's order gets wrong), with a little slack for a person's own parts so hair stays over the head
        dyn.sort(key=lambda pair: -pair[0].depth)
        zdyn = np.full((H, W), np.inf, np.float32)
        for it, ident in dyn:
            if ident in zstatic:
                self._draw_tested(cam, img, id_mask, it, *zstatic[ident], zdyn, 0.0 if ident >= VEHICLE_ID else 0.3)
        gt = self._ground_truth(cam, actors, actor_parts, id_mask) if with_gt else {}
        res = RenderResult(self._post(img, fx), gt, id_mask)
        car = [it for it in vparts if it.ident == VEHICLE_ID]
        res.vehicle_gt = self._vehicle_gt(car, id_mask, W, H) if car else None  # type: ignore[attr-defined]
        return res

    # ---------------------------------------------------------------------------------------------------------
    def _person_items(self, cam: PinholeCamera, a: Actor, ident: int) -> list[_Item]:
        plist: list[_Item] = []
        for name, P, color, is_sphere in self._person_parts(a):
            if is_sphere:
                it = self._sphere_item(cam, P[0], float(P[1][0]), color, ident)
                if it is not None:
                    pc = cam.world_to_cam(P[0][None, :])[0]
                    it.geo = ("const", float(pc[2] - P[1][0]))
            else:
                it = self._hull_item(cam, P, _shade(color, _PART_SHADE.get(name, 1.0)), ident)
                if it is not None:
                    it.geo = ("const", float(cam.world_to_cam(P)[:, 2].mean()))
            if it is not None:
                it.depth -= _PART_ORDER.get(name, 0.0)
                plist.append(it)
        return plist

    def _box_items(self, cam: PinholeCamera, bx: BoxObj, ident: int = STATIC_ID) -> list[_Item]:
        """The base renderer's box faces (and door/window decals), each carrying its plane for the depth test."""
        out: list[_Item] = []
        for part, cs, faces in self._parts(bx):
            for face, n, fc in faces:
                if np.dot(n, cam.pose.position - fc) <= 0:
                    continue
                pc = cam.world_to_cam(cs[face])
                if (pc[:, 2] < NEAR).any():
                    pc = _clip_near(pc)
                    if len(pc) < 3:
                        continue
                uv = np.stack([cam.intr.fx * pc[:, 0] / pc[:, 2] + cam.intr.cx,
                               cam.intr.fy * pc[:, 1] / pc[:, 2] + cam.intr.cy], axis=1)
                it = _Item(float(np.linalg.norm(fc - cam.pose.position)), "poly", uv,
                           _shade(part.color, self._light(n)), ident)
                it.geo = self._plane(cam, n, fc)
                out.append(it)
        for op in bx.openings:
            nx, ny = op.normal_xy
            n3 = np.array([nx, ny, 0.0])
            c = np.array([op.center_xy[0] + nx * 0.03, op.center_xy[1] + ny * 0.03])
            if np.dot(n3, cam.pose.position - np.array([c[0], c[1], (op.z0 + op.z1) / 2])) <= 0:
                continue
            t = np.array([-ny, nx]) * op.width / 2
            quad = np.array([[*(c - t), op.z0], [*(c + t), op.z0], [*(c + t), op.z1], [*(c - t), op.z1]])
            uv, z = cam.project(quad)
            if (z > NEAR).all():
                _, _, faces = self._parts(bx)[0]
                _, _, fc = max(faces, key=lambda f: float(np.dot(f[1], n3)))  # the face it is painted on
                d = float(np.linalg.norm(fc - cam.pose.position))
                it = _Item(d - 0.01, "poly", uv, _shade(op.color, self._light(n3)), ident)
                it.geo = self._plane(cam, n3, quad.mean(axis=0))
                out.append(it)
        return out

    def _face_items(self, cam: PinholeCamera, faces: list[Face], ident: int) -> list[_Item]:
        out: list[_Item] = []
        for f in faces:
            fc = f.pts.mean(axis=0)
            if np.dot(f.normal, cam.pose.position - fc) <= 0:
                continue
            pc = cam.world_to_cam(f.pts)
            if (pc[:, 2] < NEAR).any():
                pc = _clip_near(pc)
                if len(pc) < 3:
                    continue
            uv = np.stack([cam.intr.fx * pc[:, 0] / pc[:, 2] + cam.intr.cx,
                           cam.intr.fy * pc[:, 1] / pc[:, 2] + cam.intr.cy], axis=1)
            it = _Item(float(np.linalg.norm(fc - cam.pose.position)), "poly", uv, _shade(f.color, self._light(f.normal)),
                       ident)
            it.geo = self._plane(cam, f.normal, fc)
            out.append(it)
        return out

    def _tree_items_depth(self, cam: PinholeCamera, trees) -> list[_Item]:
        out: list[_Item] = []
        for tr in trees:
            trunk, spheres = self._tree_geometry(tr)
            it = self._hull_item(cam, trunk, (62, 70, 78), STATIC_ID)
            if it is not None:
                it.geo = ("const", float(cam.world_to_cam(trunk)[:, 2].min()))
                out.append(it)
            for c, r, col in spheres:
                it = self._sphere_item(cam, c, r, col, STATIC_ID)
                if it is not None:
                    it.geo = ("sphere", cam.world_to_cam(c[None, :])[0], float(r))
                    out.append(it)
        return out

    @staticmethod
    def _plane(cam: PinholeCamera, n_world: np.ndarray, p_world: np.ndarray) -> tuple:
        n_c = cam.R @ np.asarray(n_world, float)
        p_c = cam.world_to_cam(np.asarray(p_world, float)[None, :])[0]
        return ("plane", n_c, float(n_c @ p_c))

    # ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def _bbox(it: _Item) -> tuple[float, float, float, float]:
        if it.kind == "circle":
            c = it.pts[0]
            return (c[0] - it.radius, c[1] - it.radius, c[0] + it.radius, c[1] + it.radius)
        return (float(it.pts[:, 0].min()), float(it.pts[:, 1].min()), float(it.pts[:, 0].max()), float(it.pts[:, 1].max()))

    @staticmethod
    def _roi(bbs, W: int, H: int) -> tuple[int, int, int, int] | None:
        b = np.array(bbs, float)
        x0, y0 = max(0, int(math.floor(b[:, 0].min())) - 1), max(0, int(math.floor(b[:, 1].min())) - 1)
        x1, y1 = min(W, int(math.ceil(b[:, 2].max())) + 1), min(H, int(math.ceil(b[:, 3].max())) + 1)
        return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None

    @staticmethod
    def _mask(it: _Item, roi: tuple[int, int, int, int]) -> np.ndarray:
        x0, y0, x1, y1 = roi
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        if it.kind == "circle":
            c = (int(round((it.pts[0, 0] - x0) * 4)), int(round((it.pts[0, 1] - y0) * 4)))
            cv2.circle(m, c, max(1, int(round(it.radius * 4))), 1, -1, cv2.LINE_8, shift=2)
        else:
            pts = it.pts if it.kind == "poly" else cv2.convexHull(it.pts.astype(np.float32)).reshape(-1, 2)
            ip = np.round((pts - np.array([x0, y0])) * 4).astype(np.int32)
            if np.abs(ip).max() > 2 ** 27:
                return m.astype(bool)
            cv2.fillPoly(m, [ip], 1, cv2.LINE_8, shift=2)
        return m.astype(bool)

    @staticmethod
    def _z_of(cam: PinholeCamera, geo: tuple, us: np.ndarray, vs: np.ndarray) -> np.ndarray:
        """Camera-space depth of an item's surface at pixels (us, vs), in the drawing's pixel convention."""
        kind = geo[0]
        if kind == "const":
            return np.full(us.shape, geo[1])
        rx = (us - cam.intr.cx) / cam.intr.fx
        ry = (vs - cam.intr.cy) / cam.intr.fy
        if kind == "plane":
            _, n, d = geo
            den = n[0] * rx + n[1] * ry + n[2]
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(np.abs(den) > 1e-9, d / den, np.inf)
            return np.where(z > NEAR, z, np.inf)
        _, c, r = geo  # sphere: nearest intersection of the pixel ray with it
        rr = rx * rx + ry * ry + 1.0
        rc = rx * c[0] + ry * c[1] + c[2]
        disc = rc * rc - rr * (float(c @ c) - r * r)
        t = (rc - np.sqrt(np.maximum(disc, 0.0))) / rr
        return np.where(disc >= 0, t, c[2] - r)

    def _depth_in_roi(self, cam: PinholeCamera, static: list[_Item], boxes2d: np.ndarray,
                      roi: tuple[int, int, int, int]) -> np.ndarray:
        x0, y0, x1, y1 = roi
        z = np.full((y1 - y0, x1 - x0), np.inf, np.float32)
        if not len(static):
            return z
        hit = (boxes2d[:, 2] >= x0) & (boxes2d[:, 0] <= x1) & (boxes2d[:, 3] >= y0) & (boxes2d[:, 1] <= y1)
        for i in np.nonzero(hit)[0]:
            it = static[i]
            geo = getattr(it, "geo", None)
            if geo is None:
                continue
            m = self._mask(it, roi)
            if not m.any():
                continue
            vs, us = np.nonzero(m)
            zz = self._z_of(cam, geo, us + x0, vs + y0).astype(np.float32)
            z[vs, us] = np.minimum(z[vs, us], zz)
        return z

    def _draw_tested(self, cam: PinholeCamera, img: np.ndarray, id_mask: np.ndarray, it: _Item,
                     roi: tuple[int, int, int, int], zst: np.ndarray, zdyn: np.ndarray, slack: float) -> None:
        x0, y0, x1, y1 = roi
        m = self._mask(it, roi)
        if not m.any():
            return
        vs, us = np.nonzero(m)
        zz = self._z_of(cam, it.geo, us + x0, vs + y0)
        gv, gu = vs + y0, us + x0
        keep = (zz < zst[vs, us] - 0.02) & (zz < zdyn[gv, gu] + slack)
        if not keep.any():
            return
        gv, gu = gv[keep], gu[keep]
        img[gv, gu] = it.color
        id_mask[gv, gu] = it.ident
        zdyn[gv, gu] = np.minimum(zdyn[gv, gu], zz[keep])

    def _vehicle_gt(self, parts: list[_Item], id_mask: np.ndarray, W: int, H: int) -> VehicleGT:
        if not parts:
            return VehicleGT(None, 0.0, False)
        b = np.array([self._bbox(it) for it in parts])
        x0, y0 = max(0.0, b[:, 0].min()), max(0.0, b[:, 1].min())
        x1, y1 = min(float(W), b[:, 2].max()), min(float(H), b[:, 3].max())
        if x1 <= x0 or y1 <= y0:
            return VehicleGT(None, 0.0, False)
        roi = (int(x0), int(y0), int(math.ceil(x1)), int(math.ceil(y1)))
        full = np.zeros((roi[3] - roi[1], roi[2] - roi[0]), bool)
        for it in parts:
            full |= self._mask(it, roi)
        vis = id_mask[roi[1]:roi[3], roi[0]:roi[2]] == VEHICLE_ID
        area = int(full.sum())
        return VehicleGT((x0, y0, x1, y1), float(vis.sum()) / area if area else 0.0, bool(vis.any()))
