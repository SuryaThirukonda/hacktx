"""Scene spec v1: one seeded description that builds the simulator world, its ground truth and the 3D view.

Coordinates are local metres (x east, y north) in the simulator's ground frame, which the incident maps to its ENU
frame. Building sides follow the incident-command convention: side A is the designated address/front side, and
B, C, D continue clockwise seen from above (standing at A facing the building: B on the left, C at the rear,
D on the right).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..world import BoxObj, OpeningDecal

Compass = Literal["N", "E", "S", "W"]
Side = Literal["A", "B", "C", "D"]
CLOCKWISE: list[Compass] = ["N", "E", "S", "W"]
NORMALS: dict[str, tuple[float, float]] = {"N": (0.0, 1.0), "E": (1.0, 0.0), "S": (0.0, -1.0), "W": (-1.0, 0.0)}


class OpeningSpec(BaseModel):
    id: str
    kind: Literal["door", "window", "garage_door", "stairs", "roof_hatch", "opening"]
    side: Side
    offset_m: float = 0.0  # along the face from its centre, positive to the right when facing the face
    width_m: float = 1.0
    height_m: float = 2.1
    sill_m: float = 0.0
    blocked: bool = False  # ground truth: debris or a locked gate; the operator marks it manually in the demo


class BuildingSpec(BaseModel):
    id: str
    name: str
    center: tuple[float, float]
    size_ew_m: float  # east-west extent
    size_ns_m: float  # north-south extent
    height_m: float = 3.5
    side_a: Compass = "S"
    color: tuple[int, int, int] = (150, 156, 164)
    openings: list[OpeningSpec] = Field(default_factory=list)

    def compass_of(self, side: Side) -> Compass:
        i = CLOCKWISE.index(self.side_a)
        return CLOCKWISE[(i + "ABCD".index(side)) % 4]

    def side_of(self, compass: Compass) -> Side:
        i = CLOCKWISE.index(self.side_a)
        return "ABCD"[(CLOCKWISE.index(compass) - i) % 4]  # type: ignore[return-value]

    def face(self, side: Side) -> tuple[tuple[float, float], tuple[float, float], float]:
        """(face centre, outward normal, face length) for a side."""
        c = self.compass_of(side)
        nx, ny = NORMALS[c]
        center = (self.center[0] + nx * self.size_ew_m / 2, self.center[1] + ny * self.size_ns_m / 2)
        length = self.size_ew_m if c in ("N", "S") else self.size_ns_m
        return center, (nx, ny), length

    def opening_xy(self, op: OpeningSpec) -> tuple[float, float]:
        (cx, cy), (nx, ny), _ = self.face(op.side)
        # standing outside facing the wall you look along -n; your right is -n rotated by -90 deg = (-ny, nx)
        rx, ry = -ny, nx
        return cx + rx * op.offset_m, cy + ry * op.offset_m

    def side_from_point(self, x: float, y: float) -> Side:
        """Which side a ground point outside the footprint faces (used to organise annotations by side)."""
        dx, dy = (x - self.center[0]) / (self.size_ew_m / 2), (y - self.center[1]) / (self.size_ns_m / 2)
        compass: Compass = ("E" if dx > 0 else "W") if abs(dx) >= abs(dy) else ("N" if dy > 0 else "S")
        return self.side_of(compass)

    def to_box(self) -> BoxObj:
        decals = []
        for op in self.openings:
            (_, _), (nx, ny), _ = self.face(op.side)
            # low-saturation greys so the simulated colour-blob person detector does not fire on doors
            color = {"door": (58, 60, 64), "garage_door": (96, 98, 100), "window": (88, 84, 78),
                     "stairs": (70, 74, 80), "roof_hatch": (60, 62, 66), "opening": (30, 30, 34)}[op.kind]
            decals.append(OpeningDecal(op.id, self.opening_xy(op), (nx, ny), op.width_m, op.sill_m,
                                       op.sill_m + op.height_m, color))
        # BoxObj length runs along its heading (0 deg = north), width across it
        return BoxObj(self.id, "building", self.center, (self.size_ns_m, self.size_ew_m, self.height_m), 0.0,
                      self.color, openings=decals)


class SupplySpec(BaseModel):
    id: str
    label: str
    xy: tuple[float, float]
    size_m: tuple[float, float, float] = (1.2, 0.8, 0.7)


class PersonSpec(BaseModel):
    id: str
    role: Literal["subject", "responder", "bystander"]
    label: str
    start: tuple[float, float]
    heading: float = 0.0
    shirt: tuple[int, int, int] = (40, 40, 200)
    # waypoints: (x, y, speed m/s) or ("wait", seconds)
    script: list[tuple[float, float, float] | tuple[str, float]] = Field(default_factory=list)


class DroneSpec(BaseModel):
    x: float
    y: float
    alt: float = 22.0
    heading: float = 0.0
    gimbal_pitch: float = -55.0


class SceneSpec(BaseModel):
    version: Literal["scene-1"] = "scene-1"
    id: str
    title: str
    seed: int = 7
    description: str = ""
    buildings: list[BuildingSpec] = Field(default_factory=list)
    supplies: list[SupplySpec] = Field(default_factory=list)
    people: list[PersonSpec] = Field(default_factory=list)
    drone: DroneSpec

    def boxes(self) -> list[BoxObj]:
        out = [b.to_box() for b in self.buildings]
        for s in self.supplies:
            out.append(BoxObj(s.id, "supply", s.xy, s.size_m, 15.0, (40, 120, 230)))
        return out

    def ground_truth(self) -> dict:
        """Truth for scoring and for the simulated detectors (labelled SIMULATED wherever it surfaces)."""
        ops = []
        for b in self.buildings:
            for op in b.openings:
                x, y = b.opening_xy(op)
                ops.append({"id": op.id, "building": b.id, "kind": op.kind, "side": op.side,
                            "compass": b.compass_of(op.side), "xy": [round(x, 3), round(y, 3)],
                            "z": [op.sill_m, op.sill_m + op.height_m], "width_m": op.width_m, "blocked": op.blocked})
        return {"scene": self.id, "openings": ops,
                "supplies": [{"id": s.id, "label": s.label, "xy": list(s.xy)} for s in self.supplies],
                "people": [{"id": p.id, "role": p.role, "label": p.label, "start": list(p.start)} for p in self.people]}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(mode="json"), indent=2))


def opening_heading_deg(normal: tuple[float, float]) -> float:
    return math.degrees(math.atan2(normal[0], normal[1])) % 360
