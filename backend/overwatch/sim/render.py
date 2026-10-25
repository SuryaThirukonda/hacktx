"""OpenCV software renderer for the synthetic field.

Ground = perspective warp of the procedural orthophoto; people, vehicles, crates, trees and cones are 3D primitives
projected through the same pinhole model the pipeline uses, drawn with a painter's algorithm, sun shadows and
Lambert shading. Produces ground-truth boxes/visibility per actor from an ID buffer (for evaluation only).
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..geometry.camera import PinholeCamera
from .actors import Actor
from .texture import GroundTexture
from .world import BoxObj, ConeObj, TreeObj, World

NEAR = 0.08
STATIC_ID = 255


CULL_MIN_OBJECTS = 60  # small worlds (the training field) render everything, exactly as before
CULL_RANGE_M = 450.0  # beyond the 400 m fog distance
CULL_RANGE_GROUND_M = 120.0  # cameras near the ground (the responder's phone): nearer objects hide the rest


@dataclass
class RenderEffects:
    """Image degradations. The defaults are a clean feed (an ideal camera); the simulator adds noise, blur, low light
    and compression only when a scenario's fault window asks for them."""

    blur_px: float = 0.0
    brightness: float = 1.0
    noise_sigma: float = 0.0
    jpeg_quality: int | None = None
    fog_m: float | None = None
    vignette: float = 0.0
    saturation: float = 1.0


@dataclass
class ActorGT:
    actor_id: str
    role: str
    box_xyxy: tuple[float, float, float, float] | None
    visible_box_xyxy: tuple[float, float, float, float] | None
    visibility: float
    foot_px: tuple[float, float] | None
    in_frame: bool
    depth_m: float


@dataclass
class RenderResult:
    image: np.ndarray
    gt: dict[str, ActorGT] = field(default_factory=dict)
    id_mask: np.ndarray | None = None


@dataclass
class _Item:
    depth: float
    kind: str  # "poly" | "hull" | "circle"
    pts: np.ndarray  # projected pixel coords (N,2) or center (1,2)
    color: tuple[int, int, int]
    ident: int
    radius: float = 0.0


# shading per body part (a lit top and shadowed sides read as volume from above) and the draw order within a person
_PART_SHADE = {"torso": 1.08, "stripe": 1.0, "thigh": 0.96, "shin": 0.9, "upper_arm": 0.98, "forearm": 0.94, "shoe": 1.0,
               "shirt": 1.12, "backpack": 0.95, "neck": 0.92, "hat_brim": 0.9}
_PART_ORDER = {"hair": 0.02, "face": 0.03, "hat_brim": 0.04, "hat": 0.05, "shirt": 0.01, "hand": 0.01}


def _ring(center: np.ndarray, ax1: np.ndarray, ax2: np.ndarray, r1: float, r2: float, n: int = 10) -> np.ndarray:
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return center[None, :] + np.outer(np.cos(a) * r1, ax1) + np.outer(np.sin(a) * r2, ax2)


def _shade(color: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(int(max(0, min(255, c * k))) for c in color)  # type: ignore[return-value]


def _clip_near(pc: np.ndarray) -> np.ndarray:
    """Sutherland-Hodgman clip of an ordered camera-space polygon against z = NEAR."""
    out = []
    n = len(pc)
    for i in range(n):
        a, b = pc[i], pc[(i + 1) % n]
        ina, inb = a[2] >= NEAR, b[2] >= NEAR
        if ina:
            out.append(a)
        if ina != inb:
            t = (NEAR - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out) if out else np.zeros((0, 3))


class SceneRenderer:
    def __init__(self, world: World, texture: GroundTexture) -> None:
        self.world = world
        self.levels = [texture, texture.downsampled(texture.res_m * 2), texture.downsampled(texture.res_m * 4)]
        sun = np.array(world.sun_dir, dtype=float)
        self.sun = sun / np.linalg.norm(sun)
        self._vignette: dict = {}
        self.rng = np.random.default_rng(world.seed + 17)
        # view culling for large worlds: static object centres and reach (size plus height lean), computed once
        self._cull = len(world.boxes) + len(world.trees) > CULL_MIN_OBJECTS
        self._box_xy = np.array([b.center_xy for b in world.boxes], float).reshape(-1, 2)
        self._box_reach = np.array([max(b.size_lwh[:2]) / 2 + 1.6 * (b.z0 + b.size_lwh[2]) for b in world.boxes], float)
        self._tree_xy = np.array([t.xy for t in world.trees], float).reshape(-1, 2)
        self._tree_reach = np.array([t.canopy_r + 1.6 * (t.canopy_z + t.canopy_r) for t in world.trees], float)
        self._visible: tuple[list, list] | None = None
        self._tree_cache: dict[str, tuple] = {}
        self._shadow_cache: dict[str, np.ndarray] = {}

    # ---------------------------------------------------------------------------------------------------------
    def render(self, cam: PinholeCamera, actors: list[Actor], effects: RenderEffects | None = None,
               ground_level: bool = False, with_gt: bool = True) -> RenderResult:
        fx = effects or RenderEffects()
        W, H = cam.intr.width, cam.intr.height
        self._visible = self._view_set(cam) if self._cull else None
        img = self._render_ground(cam, ground_level, fx)
        id_mask = np.zeros((H, W), np.uint8) if with_gt else None
        self._render_shadows(cam, img, actors)
        items, actor_parts = self._collect(cam, actors)
        items.sort(key=lambda it: -it.depth)
        for it in items:
            self._draw(img, id_mask, it)
        gt = self._ground_truth(cam, actors, actor_parts, id_mask) if with_gt else {}
        img = self._post(img, fx)
        return RenderResult(img, gt, id_mask)

    def _view_set(self, cam: PinholeCamera) -> tuple[list, list]:
        """Boxes and trees that can appear in this view: inside the ground footprint (grown by each object's reach)
        when the camera looks down, or within the fog distance in front of it when the horizon is visible."""
        fp = cam.footprint(CULL_RANGE_M)
        c = np.asarray(cam.pose.position[:2], float)
        def keep(xy: np.ndarray, reach: np.ndarray) -> np.ndarray:
            if not len(xy):
                return np.zeros(0, bool)
            if fp is not None:
                poly = np.array(fp, float)
                x0, y0 = poly.min(axis=0)
                x1, y1 = poly.max(axis=0)
                return ((xy[:, 0] + reach >= x0) & (xy[:, 0] - reach <= x1) &
                        (xy[:, 1] + reach >= y0) & (xy[:, 1] - reach <= y1))
            rng_m = CULL_RANGE_GROUND_M if cam.pose.position[2] < 5.0 else CULL_RANGE_M
            return np.hypot(xy[:, 0] - c[0], xy[:, 1] - c[1]) <= rng_m + reach
        kb, kt = keep(self._box_xy, self._box_reach), keep(self._tree_xy, self._tree_reach)
        return ([b for b, k in zip(self.world.boxes, kb) if k], [t for t, k in zip(self.world.trees, kt) if k])

    def _boxes(self) -> list:
        return self._visible[0] if self._visible is not None else self.world.boxes

    def _trees(self) -> list:
        return self._visible[1] if self._visible is not None else self.world.trees

    # ---------------------------------------------------------------------------------------------------------
    def _render_ground(self, cam: PinholeCamera, ground_level: bool, fx: RenderEffects) -> np.ndarray:
        W, H = cam.intr.width, cam.intr.height
        Hg = cam.ground_homography()
        a, b, c = cam.ray_z_coefficients()
        corners_rz = [a * u + b * v + c for u, v in ((0, 0), (W, 0), (0, H), (W, H))]
        cz = float(cam.pose.position[2])
        horizon_visible = max(corners_rz) > -cz / 400.0
        if not ground_level and not horizon_visible and not fx.fog_m:
            gsd = cam.ground_sample_distance() or self.levels[0].res_m
            level = self.levels[0]
            for lv in self.levels:
                if lv.res_m <= gsd * 1.15:
                    level = lv
            return self._warp(level, Hg, W, H)
        uu, vv = np.meshgrid(np.arange(W, dtype=np.float32) + 0.5, np.arange(H, dtype=np.float32) + 0.5)
        rz = a * uu + b * vv + c
        with np.errstate(divide="ignore", invalid="ignore"):
            dist = np.where(rz < -1e-6, -cz / rz, np.inf).astype(np.float32)
        if ground_level:
            fine = self._warp(self.levels[0], Hg, W, H)
            coarse = self._warp(self.levels[2], Hg, W, H)
            w = np.clip((dist - 5.0) / 7.0, 0, 1)[..., None].astype(np.float32)
            img = fine.astype(np.float32) * (1 - w) + coarse.astype(np.float32) * w
        else:
            img = self._warp(self.levels[0], Hg, W, H).astype(np.float32)
        sky = ~np.isfinite(dist) | (dist > 400)
        fog_m = fx.fog_m or 400.0
        haze = np.array([214, 206, 196], np.float32)
        fw = (1.0 - np.exp(-np.where(np.isfinite(dist), dist, 1e6) / fog_m)).astype(np.float32)[..., None]
        img = img * (1 - fw) + haze * fw
        if sky.any():
            norm = np.sqrt(1 + ((uu - cam.intr.cx) / cam.intr.fx) ** 2 + ((vv - cam.intr.cy) / cam.intr.fy) ** 2)
            t = np.clip(rz / norm * 3.0, 0, 1)[..., None].astype(np.float32)
            horizon_c = np.array([226, 216, 204], np.float32)
            zenith_c = np.array([216, 186, 146], np.float32)  # pale blue: stays under the sim detector's saturation gate
            img = np.where(sky[..., None], horizon_c * (1 - t) + zenith_c * t, img)
        return np.clip(img, 0, 255).astype(np.uint8)

    @staticmethod
    def _warp(tex: GroundTexture, Hg: np.ndarray, W: int, H: int) -> np.ndarray:
        M = Hg @ tex.world_from_tex()
        return cv2.warpPerspective(tex.image, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    # ---------------------------------------------------------------------------------------------------------
    def _ground_poly_to_img(self, cam: PinholeCamera, pts_xy: np.ndarray) -> np.ndarray | None:
        P = np.column_stack([pts_xy, np.zeros(len(pts_xy))])
        uv, z = cam.project(P)
        if (z < NEAR).any():
            return None
        return uv

    def _shadow_points(self, P: np.ndarray) -> np.ndarray:
        s = self.sun
        k = P[:, 2] / -s[2]
        return P[:, :2] + k[:, None] * s[None, :2]

    def _render_shadows(self, cam: PinholeCamera, img: np.ndarray, actors: list[Actor]) -> None:
        H, W = img.shape[:2]
        mask = np.zeros((H, W), np.uint8)
        polys: list[np.ndarray] = []
        for a in actors:
            if not a.active:
                continue
            p = np.array([a.pos[0], a.pos[1], 0.0])
            ex, ey = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
            feet = _ring(p, ex, ey, 0.2, 0.2, 8)
            body = _ring(p + np.array([0, 0, a.height * 0.82]), ex, ey, 0.22, 0.22, 8)
            head = _ring(p + np.array([0, 0, a.height * 0.94]), ex, ey, 0.12, 0.12, 8)
            pts = np.vstack([feet[:, :2], self._shadow_points(body), self._shadow_points(head)])
            polys.append(cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2))
        for bx in self._boxes():
            pts = self._shadow_points(bx.corners())
            polys.append(cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2))
        for tr in self._trees():
            ring = self._shadow_cache.get(tr.oid)
            if ring is None:  # the sun does not move: one shadow outline per tree
                c = np.array([tr.xy[0], tr.xy[1], tr.canopy_z])
                sc = self._shadow_points(c[None, :])[0]
                ring = _ring(np.array([sc[0], sc[1], 0.0]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]),
                             tr.canopy_r * 1.05, tr.canopy_r * 0.95, 18)[:, :2]
                self._shadow_cache[tr.oid] = ring
            polys.append(ring)
        for cn in self.world.cones:
            base = _ring(np.array([cn.xy[0], cn.xy[1], 0.0]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), cn.r, cn.r, 8)
            tip = self._shadow_points(np.array([[cn.xy[0], cn.xy[1], cn.h]]))
            polys.append(cv2.convexHull(np.vstack([base[:, :2], tip]).astype(np.float32)).reshape(-1, 2))
        for poly in polys:
            uv = self._ground_poly_to_img(cam, poly.astype(float))
            if uv is None:
                continue
            if (uv[:, 0].max() < 0 or uv[:, 0].min() > W or uv[:, 1].max() < 0 or uv[:, 1].min() > H):
                continue
            cv2.fillPoly(mask, [np.round(uv * 4).astype(np.int32)], 255, cv2.LINE_AA, shift=2)
        if mask.any():
            m = cv2.GaussianBlur(mask, (5, 5), 0)
            k = cv2.subtract(np.full_like(m, 255), cv2.multiply(m, np.full_like(m, 107), scale=1 / 255.0))
            img[:] = cv2.multiply(img, cv2.merge([k, k, k]), scale=1 / 255.0)

    # ---------------------------------------------------------------------------------------------------------
    def _light(self, normal: np.ndarray) -> float:
        return 0.58 + 0.42 * max(0.0, float(-np.dot(normal, self.sun)))

    def _hull_item(self, cam: PinholeCamera, P: np.ndarray, color, ident: int) -> _Item | None:
        uv, z = cam.project(P)
        keep = z >= NEAR
        if keep.sum() < 3:
            return None
        uv = uv[keep]
        if not np.isfinite(uv).all() or np.abs(uv).max() > 1e5:
            return None
        center = P.mean(axis=0)
        depth = float(np.linalg.norm(center - cam.pose.position))
        return _Item(depth, "hull", uv, color, ident)

    def _sphere_item(self, cam: PinholeCamera, center: np.ndarray, r: float, color, ident: int) -> _Item | None:
        uv, z = cam.project(center[None, :])
        if z[0] < NEAR + r:
            return None
        rad = cam.intr.fx * r / z[0]
        return _Item(float(np.linalg.norm(center - cam.pose.position)), "circle", uv, color, ident, radius=float(rad))

    def _person_parts(self, a: Actor) -> list[tuple[str, np.ndarray, tuple[int, int, int], bool]]:
        """Returns (part name, 3D points, colour, is_sphere). A sphere is encoded as [centre, [radius, 0, 0]].

        A person is drawn as a jointed figure: head with hair and a face, neck, torso (shirt, open jacket, vest,
        backpack), two arms with elbows and hands, two legs with knees, shoes and an optional hat. Limbs swing with
        the walking phase and runners lean forward. All of it is seen from above by the drone and at eye level by
        the responder's phone."""
        s = a.height / 1.75
        h = math.radians(a.heading)
        fwd = np.array([math.sin(h), math.cos(h), 0.0])
        right = np.array([math.cos(h), -math.sin(h), 0.0])
        up = np.array([0.0, 0.0, 1.0])
        p = np.array([a.pos[0], a.pos[1], 0.0])
        speed = float(np.linalg.norm(a.vel))
        amp = min(0.42, 0.10 + 0.075 * speed) * s
        sw = math.sin(a.phase) * amp
        lift = max(0.0, math.sin(a.phase)) * min(0.18, 0.05 + 0.03 * speed) * s  # the swinging leg comes off the ground
        hip_z, waist_z, chest_z, sh_z = 0.92 * s, 1.12 * s, 1.32 * s, 1.44 * s
        head_z, head_r = 1.63 * s, 0.105 * s
        lean = fwd * min(0.12, 0.025 * speed)  # runners lean forward
        app = a.app
        top = app.jacket or ((0, 128, 255) if app.vest else app.shirt)
        parts: list[tuple[str, np.ndarray, tuple[int, int, int], bool]] = []

        def sphere(name: str, c: np.ndarray, r: float, color) -> None:
            parts.append((name, np.vstack([c, [r, 0.0, 0.0]]), color, True))

        def tube(name: str, p0: np.ndarray, r0: float, p1: np.ndarray, r1: float, color, n: int = 8) -> None:
            parts.append((name, np.vstack([_ring(p0, right, fwd, r0, r0, n), _ring(p1, right, fwd, r1, r1, n)]), color, False))

        # legs: thigh (hip to knee) and shin (knee to ankle); the swinging leg bends at the knee and lifts
        for side, sgn in ((-1, 1), (1, -1)):
            swing = sw * sgn
            raise_ = lift if swing > 0 else 0.0
            hip = p + right * (0.095 * s * side) + up * hip_z
            knee = p + right * (0.1 * s * side) + fwd * (swing * 0.55 + max(0.0, swing) * 0.1) + up * (0.5 * s + raise_ * 0.7)
            ankle = p + right * (0.1 * s * side) + fwd * swing + up * (0.08 * s + raise_)
            tube("thigh", hip, 0.085 * s, knee, 0.065 * s, app.pants)
            shin_col = app.pants if app.trousers == "long" else app.skin
            tube("shin", knee, 0.062 * s, ankle, 0.045 * s, shin_col)
            foot = ankle - up * 0.04 * s + fwd * 0.05 * s
            shoe = _ring(foot, right, fwd, 0.055 * s, 0.12 * s, 8)
            parts.append(("shoe", np.vstack([shoe, shoe + up * 0.07 * s]), (34, 34, 38), False))
        # torso in two pieces so the shading differs: hips to chest (shirt) and chest to shoulders (shirt or jacket)
        hips = _ring(p + up * hip_z + lean * 0.4, right, fwd, 0.17 * s, 0.11 * s)
        waist = _ring(p + up * waist_z + lean * 0.6, right, fwd, 0.16 * s, 0.105 * s)
        chest = _ring(p + up * chest_z + lean * 0.85, right, fwd, 0.2 * s, 0.12 * s)
        shoulders = _ring(p + up * sh_z + lean, right, fwd, app.shoulder_m / 2 * s, 0.125 * s)
        parts.append(("torso", np.vstack([hips, waist]), app.shirt if app.jacket is None else app.jacket, False))
        parts.append(("torso", np.vstack([waist, chest, shoulders]), top, False))
        if app.jacket is not None:  # an open jacket shows a strip of the shirt down the front
            front = p + fwd * (0.1 * s) + lean * 0.7
            strip = np.vstack([_ring(front + up * hip_z, right, fwd, 0.05 * s, 0.03 * s, 6),
                               _ring(front + up * (sh_z - 0.05 * s), right, fwd, 0.05 * s, 0.03 * s, 6)])
            parts.append(("shirt", strip, app.shirt, False))
        if app.vest:
            for z in (1.1 * s, 1.3 * s):
                band = _ring(p + up * z + lean * (z / sh_z), right, fwd, 0.215 * s, 0.135 * s)
                parts.append(("stripe", np.vstack([band - up * 0.025, band + up * 0.025]), (215, 222, 222), False))
        if app.backpack is not None:
            c = p - fwd * (0.17 * s) + lean * 0.8
            ring = _ring(c + up * (1.02 * s), right, fwd, 0.15 * s, 0.075 * s, 4)
            parts.append(("backpack", np.vstack([ring, ring + up * (0.42 * s)]), app.backpack, False))
        # arms: shoulder to elbow (sleeve), elbow to wrist (sleeve or skin), a hand
        for side, sgn in ((-1, -1), (1, 1)):
            swing = sw * sgn
            shoulder = p + right * (app.shoulder_m / 2 * s * side) + up * (sh_z - 0.04 * s) + lean
            elbow = shoulder + fwd * (swing * 0.45) - up * (0.3 * s) + right * (0.02 * s * side)
            wrist = elbow + fwd * (swing * 0.5 + max(0.0, swing) * 0.15) - up * (0.26 * s) + right * (0.02 * s * side)
            tube("upper_arm", shoulder, 0.055 * s, elbow, 0.045 * s, top, 6)
            tube("forearm", elbow, 0.045 * s, wrist, 0.035 * s, app.shirt if app.sleeves == "long" else app.skin, 6)
            sphere("hand", wrist - up * (0.03 * s), 0.045 * s, app.skin)
        # neck, head, hair and face
        tube("neck", p + up * (sh_z - 0.02 * s) + lean, 0.065 * s, p + up * (sh_z + 0.07 * s) + lean * 1.05, 0.055 * s, app.skin, 6)
        head_c = p + up * head_z + lean * 1.1
        sphere("head", head_c, head_r, app.skin)
        sphere("hair", head_c + up * (0.03 * s) - fwd * (0.015 * s), head_r * 0.95, app.hair)
        sphere("face", head_c + fwd * (0.07 * s) - up * (0.01 * s), head_r * 0.72, app.skin)
        if app.hat is not None:
            brim = _ring(head_c + up * (0.085 * s), right, fwd, 0.17 * s, 0.17 * s, 10)
            parts.append(("hat_brim", np.vstack([brim, brim + up * 0.015]), app.hat, False))
            sphere("hat", head_c + up * (0.12 * s), head_r * 0.85, app.hat)
        return parts

    def _collect(self, cam: PinholeCamera, actors: list[Actor]) -> tuple[list[_Item], dict[int, list[_Item]]]:
        items: list[_Item] = []
        actor_parts: dict[int, list[_Item]] = {}
        for idx, a in enumerate(actors):
            if not a.active:
                continue
            ident = idx + 1
            plist: list[_Item] = []
            for name, P, color, is_sphere in self._person_parts(a):
                if is_sphere:
                    it = self._sphere_item(cam, P[0], float(P[1][0]), color, ident)
                else:
                    k = _PART_SHADE.get(name, 1.0)
                    it = self._hull_item(cam, P, _shade(color, k), ident)
                if it is not None:
                    it.depth -= _PART_ORDER.get(name, 0.0)  # hair over the head, the face over the hair, the hat over all
                    plist.append(it)
            actor_parts[ident] = plist
            items.extend(plist)
        for bx in self._boxes():
            items.extend(self._box_items(cam, bx))
        if self._cull:  # large worlds: project every canopy sphere in one batch
            items.extend(self._tree_items_batch(cam, self._trees()))
        else:
            for tr in self._trees():
                items.extend(self._tree_items(cam, tr))
        for cn in self.world.cones:
            items.extend(self._cone_items(cam, cn))
        return items, actor_parts

    def _box_items(self, cam: PinholeCamera, bx: BoxObj) -> list[_Item]:
        out: list[_Item] = []
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
        for part in parts:
            cs = part.corners()
            for face, n in part.faces():
                fc = cs[face].mean(axis=0)
                if np.dot(n, cam.pose.position - fc) <= 0:
                    continue  # back-face
                pc = cam.world_to_cam(cs[face])
                if (pc[:, 2] < NEAR).any():
                    pc = _clip_near(pc)
                    if len(pc) < 3:
                        continue
                uv = np.stack([cam.intr.fx * pc[:, 0] / pc[:, 2] + cam.intr.cx,
                               cam.intr.fy * pc[:, 1] / pc[:, 2] + cam.intr.cy], axis=1)
                color = _shade(part.color, self._light(n))
                out.append(_Item(float(np.linalg.norm(fc - cam.pose.position)), "poly", uv, color, STATIC_ID))
                if bx.kind == "cardboard" and n[2] > 0.9:
                    # packing tape strip on top
                    tape = cs[face].copy()
                    mid = tape.mean(axis=0)
                    tape = mid + (tape - mid) * np.array([0.18, 1.0, 1.0])
                    tuv, tz = cam.project(tape + np.array([0, 0, 0.002]))
                    if (tz > NEAR).all():
                        out.append(_Item(float(np.linalg.norm(fc - cam.pose.position)) - 0.01, "poly", tuv,
                                         (150, 170, 182), STATIC_ID))
        for op in bx.openings:
            nx, ny = op.normal_xy
            n3 = np.array([nx, ny, 0.0])
            c = np.array([op.center_xy[0] + nx * 0.03, op.center_xy[1] + ny * 0.03])
            if np.dot(n3, cam.pose.position - np.array([c[0], c[1], (op.z0 + op.z1) / 2])) <= 0:
                continue  # facing away
            t = np.array([-ny, nx]) * op.width / 2
            quad = np.array([[*(c - t), op.z0], [*(c + t), op.z0], [*(c + t), op.z1], [*(c - t), op.z1]])
            uv, z = cam.project(quad)
            if (z > NEAR).all():
                # paint just after the face it sits on (painter's order uses the face centre distance)
                cs = bx.corners()
                face, _ = max(bx.faces(), key=lambda fn: float(np.dot(fn[1], n3)))
                d = float(np.linalg.norm(cs[face].mean(axis=0) - cam.pose.position))
                out.append(_Item(d - 0.01, "poly", uv, _shade(op.color, self._light(n3)), STATIC_ID))
        return out

    def _tree_geometry(self, tr: TreeObj) -> tuple[np.ndarray, list[tuple[np.ndarray, float, tuple[int, int, int]]]]:
        """A tree's trunk hull points and canopy spheres. Trees never move, so this is built once per tree."""
        g = self._tree_cache.get(tr.oid)
        if g is not None:
            return g
        base = np.array([tr.xy[0], tr.xy[1], 0.0])
        ex, ey, up = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])
        trunk = np.vstack([_ring(base, ex, ey, tr.trunk_r, tr.trunk_r, 8),
                           _ring(base + up * tr.trunk_h, ex, ey, tr.trunk_r * 0.8, tr.trunk_r * 0.8, 8)])
        c = base + up * tr.canopy_z
        # canopy: several overlapping spheres for an organic silhouette, kept inside canopy_r (scenarios rely on it)
        rng = np.random.default_rng(zlib.crc32(tr.oid.encode()))  # stable across processes (str hash is salted)
        spheres = []
        for k in range(5):
            r = tr.canopy_r * (0.62 if k else 0.8)
            off = rng.normal(0, tr.canopy_r * 0.25, 3) * np.array([1, 1, 0.5])
            reach = math.hypot(off[0], off[1])
            if reach > tr.canopy_r * 1.05 - r:
                off[:2] *= (tr.canopy_r * 1.05 - r) / reach
            shade = 0.85 + 0.3 * (off[2] / max(tr.canopy_r, 1e-6) + 0.5) * 0.5 + 0.08 * k
            spheres.append((c + off, r, _shade(tr.color, shade)))
        self._tree_cache[tr.oid] = (trunk, spheres)
        return trunk, spheres

    def _tree_items_batch(self, cam: PinholeCamera, trees: list[TreeObj]) -> list[_Item]:
        out: list[_Item] = []
        if not trees:
            return out
        centres, radii, colors = [], [], []
        for tr in trees:
            trunk, spheres = self._tree_geometry(tr)
            it = self._hull_item(cam, trunk, (62, 70, 78), STATIC_ID)
            if it:
                out.append(it)
            for c, r, col in spheres:
                centres.append(c)
                radii.append(r)
                colors.append(col)
        P = np.array(centres)
        R = np.array(radii)
        uv, z = cam.project(P)
        dist = np.linalg.norm(P - cam.pose.position[None, :], axis=1)
        for i in np.nonzero(z >= NEAR + R)[0]:
            out.append(_Item(float(dist[i]), "circle", uv[i:i + 1], colors[i], STATIC_ID,
                             radius=float(cam.intr.fx * R[i] / z[i])))
        return out

    def _tree_items(self, cam: PinholeCamera, tr: TreeObj) -> list[_Item]:
        out = []
        trunk, spheres = self._tree_geometry(tr)
        it = self._hull_item(cam, trunk, (62, 70, 78), STATIC_ID)
        if it:
            out.append(it)
        for centre, r, color in spheres:
            it = self._sphere_item(cam, centre, r, color, STATIC_ID)
            if it:
                out.append(it)
        return out

    def _cone_items(self, cam: PinholeCamera, cn: ConeObj) -> list[_Item]:
        base = np.array([cn.xy[0], cn.xy[1], 0.0])
        ex, ey, up = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])
        cone = np.vstack([_ring(base, ex, ey, cn.r, cn.r, 10), (base + up * cn.h)[None, :]])
        out = []
        it = self._hull_item(cam, cone, cn.color, STATIC_ID)
        if it:
            out.append(it)
        stripe = np.vstack([_ring(base + up * cn.h * 0.45, ex, ey, cn.r * 0.58, cn.r * 0.58, 10),
                            _ring(base + up * cn.h * 0.6, ex, ey, cn.r * 0.45, cn.r * 0.45, 10)])
        it2 = self._hull_item(cam, stripe, (230, 230, 230), STATIC_ID)
        if it2:
            it2.depth -= 0.01
            out.append(it2)
        return out

    # ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def _draw(img: np.ndarray, id_mask: np.ndarray | None, it: _Item) -> None:
        if it.kind == "circle":
            c = (int(round(it.pts[0, 0] * 4)), int(round(it.pts[0, 1] * 4)))
            r = max(1, int(round(it.radius * 4)))
            if r > 40000:
                return
            cv2.circle(img, c, r, it.color, -1, cv2.LINE_AA, shift=2)
            if id_mask is not None:
                cv2.circle(id_mask, c, r, it.ident, -1, cv2.LINE_8, shift=2)
            return
        pts = it.pts
        if it.kind == "hull":
            pts = cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2)
        ipts = np.round(pts * 4).astype(np.int32)
        if np.abs(ipts).max() > 2 ** 27:
            return
        cv2.fillPoly(img, [ipts], it.color, cv2.LINE_AA, shift=2)
        if id_mask is not None:
            cv2.fillPoly(id_mask, [ipts], it.ident, cv2.LINE_8, shift=2)

    def _ground_truth(self, cam: PinholeCamera, actors: list[Actor], actor_parts: dict[int, list[_Item]],
                      id_mask: np.ndarray | None) -> dict[str, ActorGT]:
        W, H = cam.intr.width, cam.intr.height
        out: dict[str, ActorGT] = {}
        for idx, a in enumerate(actors):
            ident = idx + 1
            foot_uv, fz = cam.project(np.array([[a.pos[0], a.pos[1], 0.0]]))
            depth = float(np.linalg.norm(np.array([a.pos[0], a.pos[1], 0.0]) - cam.pose.position))
            parts = actor_parts.get(ident, [])
            if not a.active or not parts:
                out[a.id] = ActorGT(a.id, a.role, None, None, 0.0, None, False, depth)
                continue
            pts = []
            for it in parts:
                if it.kind == "circle":
                    c = it.pts[0]
                    pts.extend([c - it.radius, c + it.radius])
                else:
                    pts.extend(list(it.pts))
            P = np.array(pts)
            x0, y0 = P.min(axis=0)
            x1, y1 = P.max(axis=0)
            cx0, cy0, cx1, cy1 = max(0.0, x0), max(0.0, y0), min(float(W), x1), min(float(H), y1)
            if cx1 <= cx0 or cy1 <= cy0 or id_mask is None:
                out[a.id] = ActorGT(a.id, a.role, None, None, 0.0, None, False, depth)
                continue
            ix0, iy0, ix1, iy1 = int(cx0), int(cy0), int(math.ceil(cx1)), int(math.ceil(cy1))
            roi = id_mask[iy0:iy1, ix0:ix1]
            visible = roi == ident
            full = np.zeros_like(roi)
            for it in parts:
                if it.kind == "circle":
                    c = (int(round((it.pts[0, 0] - ix0) * 4)), int(round((it.pts[0, 1] - iy0) * 4)))
                    cv2.circle(full, c, max(1, int(round(it.radius * 4))), 1, -1, cv2.LINE_8, shift=2)
                else:
                    hp = cv2.convexHull(it.pts.astype(np.float32)).reshape(-1, 2) - np.array([ix0, iy0])
                    cv2.fillPoly(full, [np.round(hp * 4).astype(np.int32)], 1, cv2.LINE_8, shift=2)
            area = int(full.sum())
            vis_n = int(visible.sum())
            vis_box = None
            if vis_n > 0:
                ys, xs = np.nonzero(visible)
                vis_box = (float(xs.min() + ix0), float(ys.min() + iy0), float(xs.max() + ix0 + 1), float(ys.max() + iy0 + 1))
            frac = vis_n / area if area > 0 else 0.0
            foot = (float(foot_uv[0, 0]), float(foot_uv[0, 1])) if fz[0] > NEAR else None
            out[a.id] = ActorGT(a.id, a.role, (cx0, cy0, cx1, cy1), vis_box, float(min(1.0, frac)), foot,
                                vis_n > 0, depth)
        return out

    # ---------------------------------------------------------------------------------------------------------
    def _post(self, img: np.ndarray, fx: RenderEffects) -> np.ndarray:
        H, W = img.shape[:2]
        if fx.blur_px >= 1.5:
            k = int(round(fx.blur_px))
            kernel = np.full((1, k), 1.0 / k, np.float32)
            img = cv2.filter2D(img, -1, kernel)
        if fx.saturation != 1.0:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            hsv[..., 1] = cv2.convertScaleAbs(hsv[..., 1], alpha=fx.saturation)
            img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        if fx.vignette > 0:
            key = (W, H, round(fx.vignette, 3))
            if key not in self._vignette:
                yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
                r = np.hypot((xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2)) / math.sqrt(2)
                v = ((1.0 - fx.vignette) + fx.vignette * (1.0 - r ** 2.2)) * 255.0
                v8 = np.clip(v, 0, 255).astype(np.uint8)
                self._vignette[key] = cv2.merge([v8, v8, v8])
            img = cv2.multiply(img, self._vignette[key], scale=1 / 255.0)
        if fx.brightness != 1.0:
            img = cv2.convertScaleAbs(img, alpha=fx.brightness)
        if fx.noise_sigma > 0:
            bank = self._noise_bank(W, H)
            n = bank[int(self.rng.integers(len(bank)))]
            img = cv2.add(img.astype(np.int16), (n * (fx.noise_sigma / 4.0)).astype(np.int16), dtype=cv2.CV_16S)
            img = np.clip(img, 0, 255).astype(np.uint8)
        if fx.jpeg_quality:
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(fx.jpeg_quality)])
            if ok:
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return img

    def _noise_bank(self, W: int, H: int) -> list[np.ndarray]:
        key = ("noise", W, H)
        bank = self._vignette.get(key)  # type: ignore[arg-type]
        if bank is None:
            rng = np.random.default_rng(11)
            bank = [rng.normal(0.0, 4.0, (H, W, 3)).astype(np.float32) for _ in range(6)]
            self._vignette[key] = bank  # type: ignore[assignment]
        return bank  # type: ignore[return-value]
