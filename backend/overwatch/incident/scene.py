"""3D scene assets (mesh, point cloud, splat), their frames, and annotation overlays.

* An asset lives in its own frame (``scene:<id>`` for imported meshes and clouds, ``arkit:<id>`` for iPhone scans).
  Annotations made on an asset (room labels, marks) are stored in that frame and always display on it.
* Anything from another frame (incident ENU observations, drone-scene anchors) is drawn on an asset only through a
  valid transform chain. Without one the overlay is refused with the missing link named. Nothing is placed by guess.
* Import format: a PLY or OBJ file (metres) plus an optional ``transform.json``:
  ``{"source_frame": "scene:<name>", "target_frame": "enu:<incident>", "matrix": [[4x4]], "method": "...",
  "residual_m": 0.12}``. A file without a transform is viewable but not aligned.
* Nothing is presented as reconstructed unless it was: each asset records how it was made (``origin``), and the
  simulator export is labelled as exported geometry, not a reconstruction.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .models import FrameId, FrameMismatch, Position, Provenance, Transform, UTCDateTime, new_id, utcnow
from .store import IncidentStore

AssetKind = Literal["mesh", "point_cloud", "splat"]
AssetFormat = Literal["ply", "obj", "splat", "ksplat"]
MAX_ASSET_BYTES = 300 * 1024 * 1024


class NoAlignment(ValueError):
    """No valid transform chain between two frames."""


class SceneAsset(BaseModel):
    id: str = Field(default_factory=lambda: new_id("SCN"))
    incident_id: str
    name: str
    kind: AssetKind
    format: AssetFormat
    frame: FrameId
    file: str  # path relative to the scenes directory
    bytes: int
    provenance: Provenance
    origin: str  # how it was made, e.g. "iPhone LiDAR scan", "COLMAP + splatfacto", "simulator geometry export"
    reconstructed: bool = False  # True only for an actual reconstruction from imagery or a scan
    points: int | None = None
    faces: int | None = None
    up_axis: Literal["z", "y"] = "z"
    created_at: UTCDateTime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------------------------------------------
def inspect_file(data: bytes, fmt: str) -> tuple[str, int | None, int | None]:
    """Validate the file and return (kind, points, faces). Raises ValueError for anything unparseable."""
    if fmt == "ply":
        head = data[:4096]
        if not head.startswith(b"ply"):
            raise ValueError("not a PLY file (missing 'ply' magic)")
        end = head.find(b"end_header")
        if end < 0:
            raise ValueError("PLY header has no end_header within 4 KB")
        verts = faces = 0
        props: list[str] = []
        current = None
        for line in head[:end].decode("ascii", "replace").splitlines():
            parts = line.split()
            if parts[:2] == ["element", "vertex"]:
                verts, current = int(parts[2]), "vertex"
            elif parts[:2] == ["element", "face"]:
                faces, current = int(parts[2]), "face"
            elif parts[:1] == ["property"] and current == "vertex":
                props.append(parts[-1])
        if verts <= 0:
            raise ValueError("PLY has no vertices")
        if {"f_dc_0", "opacity", "scale_0", "rot_0"} <= set(props):
            return "splat", verts, None  # a 3D Gaussian splatting PLY
        return ("mesh" if faces else "point_cloud"), verts, faces or None
    if fmt == "obj":
        text = data.decode("utf-8", "replace")
        v = sum(1 for ln in text.splitlines() if ln.startswith("v "))
        f = sum(1 for ln in text.splitlines() if ln.startswith("f "))
        if v == 0:
            raise ValueError("OBJ has no vertices")
        return ("mesh" if f else "point_cloud"), v, f or None
    if fmt in ("splat", "ksplat"):
        if len(data) < 32:
            raise ValueError("splat file is empty")
        return "splat", (len(data) // 32 if fmt == "splat" else None), None
    raise ValueError(f"unsupported format {fmt!r} (PLY, OBJ, .splat or .ksplat)")


def parse_transform_json(raw: bytes | str, asset_frame: str, incident_enu: str) -> Transform:
    d = json.loads(raw)
    d.setdefault("source_frame", asset_frame)
    if d["source_frame"] != asset_frame:
        raise FrameMismatch(f"transform.json source_frame {d['source_frame']!r} is not the asset frame {asset_frame!r}")
    d.setdefault("target_frame", incident_enu)
    d.setdefault("method", "imported transform.json")
    return Transform.model_validate({k: d[k] for k in ("source_frame", "target_frame", "matrix", "method",
                                                       "residual_m", "manually_verified", "valid_until") if k in d})


# ---------------------------------------------------------------------------------------------------------------
def resolve(store: IncidentStore, incident_id: str, source: str, target: str,
            at: datetime | None = None) -> tuple[np.ndarray, list[str]]:
    """4x4 matrix mapping ``source`` to ``target`` through valid stored transforms (inverses allowed, up to 3 hops)."""
    if source == target:
        return np.eye(4), ["identity"]
    edges: dict[str, list[tuple[str, np.ndarray, str]]] = {}
    for tf in store.list_transforms(incident_id):
        if not tf.is_valid(at):
            continue
        m = np.asarray(tf.matrix, float)
        edges.setdefault(tf.source_frame, []).append((tf.target_frame, m, f"{tf.id} ({tf.method})"))
        edges.setdefault(tf.target_frame, []).append((tf.source_frame, np.linalg.inv(m), f"inverse of {tf.id} ({tf.method})"))
    frontier = [(source, np.eye(4), [])]
    seen = {source}
    for _ in range(3):
        nxt = []
        for frame, m, path in frontier:
            for to, em, label in edges.get(frame, []):
                if to in seen:
                    continue
                mm, pp = em @ m, path + [label]
                if to == target:
                    return mm, pp
                seen.add(to)
                nxt.append((to, mm, pp))
        frontier = nxt
    raise NoAlignment(f"no valid alignment between {source} and {target}")


def transform_point(m: np.ndarray, p: Position, target: str) -> Position:
    v = m @ np.array([p.x, p.y, p.z, 1.0])
    return Position(frame=target, x=float(v[0]), y=float(v[1]), z=float(v[2]))


def overlay(store: IncidentStore, incident_id: str, frame: str, enu_frame: str) -> dict[str, Any]:
    """Annotations to draw on an asset in ``frame``. Its own annotations always; ENU observations and anchors from
    other frames only through a valid transform chain (otherwise ``NoAlignment``)."""
    own = [a for a in store.list_anchors(incident_id) if a.coordinate_frame == frame]
    items: list[dict[str, Any]] = [{"kind": "anchor", "id": a.id, "label": a.name, "type": a.type,
                                    "position": a.position.model_dump(), "frame_of_record": frame,
                                    "provenance": "manual" if a.manually_verified else a.attributes.get("provenance", "manual"),
                                    "observation_id": a.observation_id, "created_at": a.created_at.isoformat()}
                                   for a in own]
    foreign = [a for a in store.list_anchors(incident_id) if a.coordinate_frame != frame]
    views = [v for v in store.list_observations(incident_id) if v.observation.world_position is not None
             and v.review_status != "rejected"]
    needs = {a.coordinate_frame for a in foreign} | ({enu_frame} if views else set())
    mats: dict[str, tuple[np.ndarray, list[str]]] = {}
    missing: list[str] = []
    for src in needs:
        try:
            mats[src] = resolve(store, incident_id, src, frame)
        except NoAlignment:
            missing.append(src)
    for a in foreign:
        if a.coordinate_frame in mats:
            m, path = mats[a.coordinate_frame]
            items.append({"kind": "anchor", "id": a.id, "label": a.name, "type": a.type,
                          "position": transform_point(m, a.position, frame).model_dump(),
                          "frame_of_record": a.coordinate_frame, "via": path, "observation_id": a.observation_id,
                          "provenance": a.attributes.get("provenance", "manual"), "created_at": a.created_at.isoformat()})
    for v in views:
        o = v.observation
        if o.world_position.frame in mats:  # type: ignore[union-attr]
            m, path = mats[o.world_position.frame]  # type: ignore[union-attr]
            items.append({"kind": "observation", "id": o.id, "label": o.label, "type": o.category,
                          "position": transform_point(m, o.world_position, frame).model_dump(),  # type: ignore[arg-type]
                          "frame_of_record": o.world_position.frame, "via": path,  # type: ignore[union-attr]
                          "observation_id": o.id, "provenance": o.provenance, "review_status": v.review_status,
                          "uncertainty_m": o.horizontal_uncertainty_m, "created_at": o.observed_at.isoformat()})
    return {"frame": frame, "items": items, "aligned_frames": sorted(mats), "unaligned_frames": sorted(missing),
            "note": None if not missing else
            f"Not shown: annotations in {', '.join(sorted(missing))}: no valid alignment with {frame}."}


# ---------------------------------------------------------------------------------------------------------------
def simulator_point_cloud(scene_spec: Any, step_m: float = 0.5, extent_m: float = 45.0) -> tuple[bytes, int]:
    """The simulator's world geometry as a coloured ASCII point cloud (ground grid + building boxes + supplies),
    exported exactly from the scene spec. This is exported geometry, not a reconstruction."""
    pts: list[tuple[float, float, float, int, int, int]] = []
    g = np.arange(-extent_m, extent_m + 1e-6, step_m * 2)
    for x in g:
        for y in g:
            pts.append((float(x), float(y), 0.0, 74, 120, 62))
    for b in getattr(scene_spec, "buildings", []):
        cx, cy = b.center
        hx, hy, h = b.size_ew_m / 2, b.size_ns_m / 2, b.height_m
        for z in np.arange(0, h + 1e-6, step_m):
            for x in np.arange(cx - hx, cx + hx + 1e-6, step_m):
                pts += [(float(x), cy - hy, float(z), 150, 150, 150), (float(x), cy + hy, float(z), 150, 150, 150)]
            for y in np.arange(cy - hy, cy + hy + 1e-6, step_m):
                pts += [(cx - hx, float(y), float(z), 140, 140, 140), (cx + hx, float(y), float(z), 140, 140, 140)]
        for x in np.arange(cx - hx, cx + hx + 1e-6, step_m):
            for y in np.arange(cy - hy, cy + hy + 1e-6, step_m):
                pts.append((float(x), float(y), h, 120, 120, 125))
    for s in getattr(scene_spec, "supplies", []):
        for dz in (0.1, 0.3, 0.5):
            pts.append((s.xy[0], s.xy[1], dz, 220, 110, 40))
    head = ("ply\nformat ascii 1.0\ncomment Overwatch simulator geometry export (not a reconstruction)\n"
            f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    body = "\n".join(f"{x:.3f} {y:.3f} {z:.3f} {r} {gg} {bb}" for x, y, z, r, gg, bb in pts)
    return (head + body + "\n").encode(), len(pts)


def sample_indoor_scan(seed: int = 7) -> tuple[bytes, int]:
    """A SYNTHETIC sample indoor scan (three rooms and a corridor as wall and floor points), generated by code for
    trying Indoor mode without an iPhone. It is labelled synthetic wherever it is shown."""
    rng = np.random.default_rng(seed)
    walls = [((0, 0), (12, 0)), ((12, 0), (12, 8)), ((12, 8), (0, 8)), ((0, 8), (0, 0)),  # outer shell
             ((4, 0), (4, 5)), ((8, 0), (8, 5)), ((0, 5), (3.2, 5)), ((4.8, 5), (7.2, 5)), ((8.8, 5), (12, 5))]
    pts = []
    for (x0, y0), (x1, y1) in walls:
        n = int(np.hypot(x1 - x0, y1 - y0) / 0.05)
        for i in range(n):
            t = i / max(1, n - 1)
            for z in np.arange(0, 2.6, 0.1):
                pts.append((x0 + (x1 - x0) * t + rng.normal(0, 0.01), y0 + (y1 - y0) * t + rng.normal(0, 0.01),
                            z, 200, 196, 186))
    for x in np.arange(0, 12, 0.15):
        for y in np.arange(0, 8, 0.15):
            pts.append((x, y, rng.normal(0, 0.005), 120, 104, 88))
    head = ("ply\nformat ascii 1.0\ncomment SYNTHETIC sample indoor scan generated by Overwatch (not a real scan)\n"
            f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    body = "\n".join(f"{x:.3f} {y:.3f} {z:.3f} {r} {g} {b}" for x, y, z, r, g, b in pts)
    return (head + body + "\n").encode(), len(pts)


def save_asset(store: IncidentStore, asset: SceneAsset) -> SceneAsset:
    store._put("scene_assets", {"id": asset.id, "incident_id": asset.incident_id,  # noqa: SLF001
                                "created_at": asset.created_at.isoformat()}, asset)
    store.append_event(asset.incident_id, "scene_asset", asset.model_dump(mode="json"))
    return asset


def list_assets(store: IncidentStore, incident_id: str) -> list[SceneAsset]:
    return store._list("scene_assets", SceneAsset, incident_id, "created_at")  # noqa: SLF001


def get_asset(store: IncidentStore, asset_id: str) -> SceneAsset:
    return store._get("scene_assets", SceneAsset, asset_id)  # noqa: SLF001


def scenes_dir(store: IncidentStore, repo_root: Path) -> Path:
    base = Path(store.path).parent if store.path != ":memory:" else repo_root / "data"
    d = base / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d
