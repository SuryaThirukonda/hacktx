"""Incident data model shared by every mission pack (Search, Follow, Structure, Indoor).

Rules enforced here, not by convention:
* every timestamp is timezone-aware and stored in UTC (naive datetimes are rejected);
* every position names its coordinate frame, and frames only change through an explicit ``Transform``;
* an observation either has a world position *with* its uncertainty and estimation method, or has neither
  (``world_position = None`` means "location not yet verified", never zero metres);
* every observation carries its provenance (live, replay, simulated, manual).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "incident-1.0"


# ---------------------------------------------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------------------------------------------
def _utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return v.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(_utc)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------------------------------------------
# Frames and positions
# ---------------------------------------------------------------------------------------------------------------
# wgs84                 latitude/longitude/altitude
# enu:<incident_id>     local east-north-up metres for one incident
# camera:<source_id>    camera optical frame (x right, y down, z forward), metres
# image:<source_id>     pixel coordinates of one source
# arkit:<scan_id>       ARKit local world frame of one iPhone scan
# scene:<scene_id>      a reconstructed or imported 3D scene
FRAME_RE = re.compile(r"^(wgs84|enu|camera|image|arkit|scene)(:[A-Za-z0-9_.\-]+)?$")


def _frame(v: str) -> str:
    if not FRAME_RE.match(v):
        raise ValueError(f"unknown coordinate frame {v!r}")
    if v != "wgs84" and ":" not in v:
        raise ValueError(f"frame {v!r} needs an id, e.g. 'enu:INC-1'")
    return v


FrameId = Annotated[str, AfterValidator(_frame)]

Provenance = Literal["live", "replay", "simulated", "manual"]
ReviewStatus = Literal["pending", "confirmed", "rejected"]
MissionMode = Literal["search", "follow", "structure", "indoor"]
SourceKind = Literal["drone_rgb", "ground_rgb", "near_ir", "lidar", "replay", "simulation"]
SourceMode = Literal["live", "replay", "simulated"]
AlertSeverity = Literal["info", "attention", "warning", "critical"]

# today's wire provenance (schemas.Provenance) -> incident provenance
PROVENANCE_FROM_WIRE: dict[str, Provenance] = {"LIVE": "live", "RECORDED": "replay", "SYNTHETIC": "simulated"}


class FrameMismatch(ValueError):
    """Raised whenever geometry from one frame is used in another without a transform."""


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)
    frame: FrameId
    x: float  # east (enu), x (camera/arkit/scene), longitude (wgs84)
    y: float  # north (enu), y, latitude (wgs84)
    z: float = 0.0  # up (enu), z, altitude metres (wgs84)

    def require(self, frame: str) -> "Position":
        if self.frame != frame:
            raise FrameMismatch(f"expected a position in {frame}, got {self.frame}")
        return self


class BoundingBox(BaseModel):
    frame: FrameId  # image:<source>
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> "BoundingBox":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bounding box corners out of order")
        if not self.frame.startswith("image:"):
            raise ValueError("bounding boxes live in an image frame")
        return self


class Transform(BaseModel):
    """A rigid or similarity transform from ``source_frame`` to ``target_frame`` (4x4 homogeneous matrix)."""

    id: str = Field(default_factory=lambda: new_id("TF"))
    source_frame: FrameId
    target_frame: FrameId
    matrix: list[list[float]]
    method: str  # e.g. "aruco", "manual_3pt", "telemetry", "identity"
    residual_m: float | None = None
    created_at: UTCDateTime = Field(default_factory=utcnow)
    valid_until: UTCDateTime | None = None
    manually_verified: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "Transform":
        m = np.asarray(self.matrix, dtype=float)
        if m.shape != (4, 4) or not np.allclose(m[3], [0, 0, 0, 1]):
            raise ValueError("transform matrix must be 4x4 homogeneous")
        if self.source_frame == "wgs84" or self.target_frame == "wgs84":
            raise ValueError("WGS84 conversions use geo.enu_from_wgs84, not a matrix transform")
        return self

    def is_valid(self, at: datetime | None = None) -> bool:
        return self.valid_until is None or (at or utcnow()) <= self.valid_until

    def apply(self, p: Position) -> Position:
        if p.frame != self.source_frame:
            raise FrameMismatch(f"transform {self.source_frame}->{self.target_frame} applied to {p.frame}")
        v = np.asarray(self.matrix, dtype=float) @ np.array([p.x, p.y, p.z, 1.0])
        return Position(frame=self.target_frame, x=float(v[0]), y=float(v[1]), z=float(v[2]))

    @staticmethod
    def from_rt(source: str, target: str, yaw_deg: float = 0.0, t: tuple[float, float, float] = (0, 0, 0),
                scale: float = 1.0, method: str = "manual", **kw: Any) -> "Transform":
        c, s = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
        m = np.eye(4)
        m[:3, :3] = scale * np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        m[:3, 3] = t
        return Transform(source_frame=source, target_frame=target, matrix=m.tolist(), method=method, **kw)


# ---------------------------------------------------------------------------------------------------------------
# Core records
# ---------------------------------------------------------------------------------------------------------------
class CoordinateReference(BaseModel):
    """Where the incident's ENU origin sits on Earth, if known."""

    enu_frame: FrameId
    origin_lat: float | None = None
    origin_lon: float | None = None
    origin_alt_m: float | None = None
    north_offset_deg: float = 0.0  # angle of local +y from true north (clockwise)
    note: str = ""


class Incident(BaseModel):
    schema_version: str = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("INC"))
    title: str
    created_at: UTCDateTime = Field(default_factory=utcnow)
    status: Literal["active", "closed"] = "active"
    mission_mode: MissionMode = "search"
    coordinate_reference: CoordinateReference | None = None
    operator_notes: str = ""
    provenance: Provenance = "simulated"  # what kind of data drives this incident
    scenario_id: str | None = None

    @model_validator(mode="after")
    def _default_ref(self) -> "Incident":
        if self.coordinate_reference is None:
            self.coordinate_reference = CoordinateReference(enu_frame=f"enu:{self.id}")
        return self

    @property
    def enu_frame(self) -> str:
        assert self.coordinate_reference is not None
        return self.coordinate_reference.enu_frame


class SensorSource(BaseModel):
    id: str
    kind: SourceKind
    name: str
    connection_status: Literal["connected", "disconnected", "error", "not_configured"] = "disconnected"
    source_mode: SourceMode
    last_seen_at: UTCDateTime | None = None
    calibration: dict[str, Any] = Field(default_factory=dict)
    detail: str = ""


class Observation(BaseModel):
    """A raw sensor or manual observation. Review decisions are stored separately (``Review``)."""

    schema_version: str = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("OBS"))
    incident_id: str
    sensor_source_id: str
    observed_at: UTCDateTime
    received_at: UTCDateTime = Field(default_factory=utcnow)
    category: Literal["person", "opening", "hazard", "supply", "vehicle", "marker", "other"]
    label: str
    confidence: float | None = Field(default=None, ge=0, le=1)  # only from a calibrated/identified detector
    detector: str | None = None
    bounding_box: BoundingBox | None = None
    world_position: Position | None = None
    horizontal_uncertainty_m: float | None = Field(default=None, ge=0)
    coordinate_frame: FrameId | None = None
    position_estimation_method: Literal["none", "ground_plane", "manual", "simulation_truth", "lidar", "anchor"] = "none"
    evidence_frame_reference: str | None = None
    track_id: str | None = None
    stale_after: UTCDateTime | None = None
    provenance: Provenance
    attributes: dict[str, Any] = Field(default_factory=dict)  # e.g. {"side": "C"} for Structure

    @model_validator(mode="after")
    def _position_rules(self) -> "Observation":
        if self.world_position is None:
            if self.horizontal_uncertainty_m is not None:
                raise ValueError("uncertainty without a position")
            if self.position_estimation_method not in ("none",):
                raise ValueError("an estimation method needs a position")
            self.coordinate_frame = None
        else:
            if self.horizontal_uncertainty_m is None:
                raise ValueError("a world position needs its horizontal uncertainty")
            if self.position_estimation_method == "none":
                raise ValueError("a world position needs an estimation method")
            if self.coordinate_frame is None:
                self.coordinate_frame = self.world_position.frame
            elif self.coordinate_frame != self.world_position.frame:
                raise FrameMismatch("coordinate_frame does not match world_position.frame")
            if self.position_estimation_method == "manual" and self.provenance != "manual":
                raise ValueError("manual positions must carry manual provenance")
        if self.bounding_box is not None and self.bounding_box.frame != f"image:{self.sensor_source_id}":
            raise FrameMismatch("bounding box must be in the image frame of its sensor source")
        return self

    def is_stale(self, now: datetime | None = None) -> bool:
        return self.stale_after is not None and (now or utcnow()) > self.stale_after

    @property
    def location_text(self) -> str:
        if self.world_position is None:
            return "location not yet verified"
        return f"±{self.horizontal_uncertainty_m:.1f} m ({self.position_estimation_method})"


class Review(BaseModel):
    id: str = Field(default_factory=lambda: new_id("REV"))
    observation_id: str
    status: ReviewStatus
    reviewer: str
    at: UTCDateTime = Field(default_factory=utcnow)
    note: str = ""
    corrected_label: str | None = None


class ObservationView(BaseModel):
    """An observation merged with its latest review, as the UI shows it."""

    observation: Observation
    review_status: ReviewStatus = "pending"
    reviewer: str | None = None
    review_timestamp: UTCDateTime | None = None
    stale: bool = False


class TrackPoint(BaseModel):
    t: UTCDateTime
    position: Position | None = None  # None when only image evidence exists
    bbox: BoundingBox | None = None


class Track(BaseModel):
    id: str = Field(default_factory=lambda: new_id("TRK"))
    incident_id: str
    observation_ids: list[str] = Field(default_factory=list)
    first_seen: UTCDateTime
    last_seen: UTCDateTime
    positions: list[TrackPoint] = Field(default_factory=list)
    tracking_status: Literal["active", "lost", "ended"] = "active"
    identity_status: Literal["unverified", "operator_designated"] = "unverified"
    designated_by: str | None = None
    provenance: Provenance


class SceneAnchor(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ANC"))
    incident_id: str
    name: str
    type: Literal["entrance", "door", "window", "stairs", "garage", "opening", "hazard", "supply", "room",
                  "landmark", "person", "other"]
    coordinate_frame: FrameId
    position: Position
    transform_id: str | None = None
    uncertainty_m: float | None = None
    manually_verified: bool = False
    observation_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    created_at: UTCDateTime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _frame(self) -> "SceneAnchor":
        if self.position.frame != self.coordinate_frame:
            raise FrameMismatch("anchor position frame does not match coordinate_frame")
        return self


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ALR"))
    incident_id: str
    severity: AlertSeverity
    source_observation_ids: list[str] = Field(default_factory=list)
    message: str
    created_at: UTCDateTime = Field(default_factory=utcnow)
    delivered_at: UTCDateTime | None = None
    acknowledged_at: UTCDateTime | None = None
    acknowledged_by: str | None = None
    status: Literal["pending", "delivered", "acknowledged", "expired"] = "pending"
    provenance: Provenance = "simulated"


ActionType = Literal["review_candidate", "inspect_area", "mark_last_seen", "request_operator_confirmation",
                     "notify_responder", "show_structure_side", "hold_for_more_information",
                     "return_to_operator_review"]


class MissionAction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ACT"))
    incident_id: str
    mission: MissionMode
    action_type: ActionType
    proposed_by: Literal["rules", "model", "human"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = "pending"
    execution_status: Literal["not_started", "done", "failed", "cancelled"] = "not_started"
    safety_constraints: list[str] = Field(default_factory=list)
    reason: str = ""
    created_at: UTCDateTime = Field(default_factory=utcnow)


class IncidentEvent(BaseModel):
    """One entry of the append-only incident log (enough to replay the incident)."""

    seq: int = 0
    incident_id: str
    at: UTCDateTime = Field(default_factory=utcnow)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


def stale_at(observed_at: datetime, seconds: float) -> datetime:
    return observed_at + timedelta(seconds=seconds)
