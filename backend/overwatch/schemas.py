"""Versioned wire contracts shared by the backend, dashboard (dashboard/src/types/contracts.ts) and iOS apps.

Rules (from both source plans):
* every message carries ``schema_version`` and, where it has one, the source's own capture timestamp;
* missing geometry is ``None`` — never zero meters;
* metric fields are only populated when the producing calibration/registration is valid.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

Mode = Literal["SIMULATED", "LIVE", "REPLAY"]
Provenance = Literal["LIVE", "RECORDED", "SYNTHETIC"]
Severity = Literal["info", "attention", "warning", "critical", "sensor_fault", "local_proximity"]
XY = tuple[float, float]


class Msg(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------------------------------------------
# Source health
# --------------------------------------------------------------------------------------------------------------


class SourceHealth(Msg):
    source_id: str
    kind: Literal["drone", "phone", "telemetry", "detector", "calibration", "decisions", "bridge"]
    status: Literal["OK", "STALE", "OFFLINE", "DEGRADED", "NOT_CONFIGURED", "PAUSED"]
    provenance: Provenance | None = None
    last_age_ms: float | None = None
    fps: float | None = None
    received: int = 0
    processed: int = 0
    dropped: int = 0
    reconnects: int = 0
    detail: str | None = None


# --------------------------------------------------------------------------------------------------------------
# Device uplinks
# --------------------------------------------------------------------------------------------------------------


class DepthSector(Msg):
    sector: Literal["left", "front_left", "front", "front_right", "right"]
    nearest_m: float | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    num_valid_points: int = 0


class GPSFix(Msg):
    lat: float
    lon: float
    accuracy_m: float | None = None


class PhoneObservation(Msg):
    """Responder phone uplink (native iOS app or simulator). Depth is processed on-device into sectors."""

    schema_version: str = SCHEMA_VERSION
    type: Literal["phone_observation"] = "phone_observation"
    session_id: str | None = None
    source_id: str = "responder-iphone"
    seq: int = 0
    capture_ts_ms: float
    arkit_tracking_state: Literal["normal", "limited", "not_available"] = "normal"
    tracking_reason: str | None = None
    camera_pose_4x4: list[list[float]] | None = None  # ARKit world-from-camera (y up)
    camera_intrinsics_3x3: list[list[float]] | None = None  # sensor-native, at depth-map resolution
    image_resolution: tuple[int, int] | None = None  # sensor-native capture resolution
    # Rotation applied to the sensor-native image to make the uplinked JPEG and vision_boxes upright
    # (CGImagePropertyOrientation naming): up = native landscape, right = phone held upright (portrait).
    image_orientation: Literal["up", "down", "left", "right"] | None = None
    lidar_supported: bool = True
    depth_mode: Literal["sceneDepth", "smoothedSceneDepth", "none"] = "sceneDepth"
    depth_valid_fraction: float | None = None
    sectors: list[DepthSector] = []
    obstacle_points: list[tuple[float, float, float]] | None = None  # ARKit world frame, floor removed
    floor_y_m: float | None = None
    heading_deg: float | None = None  # true heading from compass
    heading_accuracy_deg: float | None = None
    head_yaw_deg: float | None = None  # AirPods head tracking (relative to phone heading)
    gps: GPSFix | None = None
    battery_pct: float | None = None
    vision_boxes: list[tuple[float, float, float, float]] | None = None  # normalised xyxy from Apple Vision
    vision_ranges_m: list[float | None] | None = None  # LiDAR median range inside each Vision box (on-device)
    local_alert_active: bool = False


class DroneTelemetry(Msg):
    schema_version: str = SCHEMA_VERSION
    type: Literal["telemetry"] = "telemetry"
    source_id: str = "mini2-bridge"
    seq: int = 0
    capture_ts_ms: float
    lat: float | None = None
    lon: float | None = None
    alt_rel_m: float | None = None  # barometric, above take-off
    yaw_deg: float | None = None  # aircraft yaw, true north, clockwise
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    vel_ned: tuple[float, float, float] | None = None
    gimbal_pitch_deg: float | None = None
    battery_pct: float | None = None
    gps_level: int | None = None
    sats: int | None = None
    flying: bool = False
    vstick_enabled: bool = False
    sim: bool = False
    flight_mode: str | None = None


class VStickCommand(Msg):
    type: Literal["vstick"] = "vstick"
    seq: int = 0
    ts_ms: float = 0.0
    forward_mps: float = 0.0
    right_mps: float = 0.0
    up_mps: float = 0.0
    yaw_rate_dps: float = 0.0
    ttl_ms: float = 300.0


class BridgeCommand(Msg):
    type: Literal["vstick", "gimbal", "mode", "hover", "rtl", "sim_start", "sim_stop", "ping", "estop", "operator_rearm"]
    seq: int = 0
    ts_ms: float = 0.0
    payload: dict[str, Any] = {}


# --------------------------------------------------------------------------------------------------------------
# Perception + geometry state
# --------------------------------------------------------------------------------------------------------------


class Track(Msg):
    track_id: str
    class_name: str
    confidence: float
    state: Literal["TENTATIVE", "CONFIRMED", "LOST", "EXPIRED"]
    visibility: Literal["visible", "occluded", "lost"]
    source: Literal["drone", "phone"] = "drone"
    model_id: str
    box_xyxy_px: tuple[float, float, float, float] | None = None
    first_seen_ts_ms: float
    last_seen_ts_ms: float
    hits: int
    ground_xy_m: XY | None = None
    ground_error_m: float | None = None
    velocity_xy_mps: XY | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None
    velocity_valid: bool = False
    calibration_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    role: Literal["subject", "responder"] | None = None
    trail: list[tuple[float, float, float]] = []  # (x_m, y_m, ts_ms) — metric trail only


class MarkerObservation(Msg):
    id: int
    center_px: XY
    corners_px: list[XY] = []
    world_xy: XY | None = None
    used_for_fit: bool = False
    holdout: bool = False
    error_m: float | None = None


class CalibrationStatus(Msg):
    calibration_id: str | None = None
    source: Literal["markers", "chained", "telemetry", "manual", "none"] = "none"
    valid: bool = False
    reason: str = "not calibrated"
    H_img_to_ground: list[list[float]] | None = None
    markers_detected: int = 0
    markers_used: int = 0
    holdout_count: int = 0
    holdout_rmse_m: float | None = None
    fit_rmse_m: float | None = None
    age_s: float | None = None
    error_bound_m: float | None = None
    markers: list[MarkerObservation] = []
    last_marker_calibration_id: str | None = None


class RegistrationStatus(Msg):
    method: Literal["auto_trajectory", "compass_point", "manual_two_point", "none"] = "none"
    valid: bool = False
    confidence: Literal["high", "medium", "low", "none"] = "none"
    residual_m: float | None = None
    pairs: int = 0
    yaw_deg: float | None = None
    translation_m: XY | None = None
    reason: str = "phone frame not registered to the local map"


class DroneState(Msg):
    position_xy_m: XY | None = None
    alt_m: float | None = None
    yaw_deg: float | None = None
    gimbal_pitch_deg: float | None = None
    battery_pct: float | None = None
    velocity_xy_mps: XY | None = None
    lat: float | None = None
    lon: float | None = None
    flying: bool = False
    vstick_enabled: bool = False
    sim: bool = False
    footprint_xy_m: list[XY] | None = None
    telemetry_age_ms: float | None = None


class PhoneSummary(Msg):
    connected: bool = False
    provenance: Provenance | None = None
    tracking_state: str | None = None
    lidar_supported: bool | None = None
    depth_valid_fraction: float | None = None
    sectors: list[DepthSector] = []
    nearest_m: float | None = None
    nearest_sector: str | None = None
    local_alert_active: bool = False
    heading_deg: float | None = None
    heading_accuracy_deg: float | None = None
    age_ms: float | None = None
    frustum_xy_m: list[XY] | None = None  # phone camera coverage on the local map (if registered)


class ResponderState(Msg):
    position_xy_m: XY | None = None
    position_source: Literal["drone_lock", "arkit_registered", "gps", "none"] = "none"
    position_error_m: float | None = None
    heading_deg: float | None = None  # local-frame heading (clockwise from local +y)
    heading_source: Literal["head_yaw", "compass", "arkit", "motion", "none"] = "none"
    heading_valid: bool = False
    track_id: str | None = None
    lat: float | None = None
    lon: float | None = None


class LockCandidate(Msg):
    track_id: str
    similarity: float
    distance_m: float | None = None


class LockState(Msg):
    role: Literal["subject", "responder"]
    state: Literal["UNLOCKED", "LOCKED", "COAST", "REACQUIRE", "UNCERTAIN", "HOLD"] = "UNLOCKED"
    track_id: str | None = None
    since_ts_ms: float | None = None
    lost_ms: float | None = None
    predicted_xy_m: XY | None = None
    search_radius_m: float | None = None
    last_seen_xy_m: XY | None = None
    last_seen_ts_ms: float | None = None
    candidates: list[LockCandidate] = []
    needs_operator_confirm: bool = False
    verified: bool = True
    rebinds: int = 0
    description: str | None = None


class RelativeKinematics(Msg):
    valid: bool = False
    reason: str = "no subject lock"
    range_m: float | None = None
    range_error_m: float | None = None
    bearing_rel_deg: float | None = None
    bearing_abs_deg: float | None = None
    clock: int | None = None
    closing_mps: float | None = None
    ttc_s: float | None = None
    alignment_cos: float | None = None
    subject_speed_mps: float | None = None
    subject_heading_deg: float | None = None
    subject_accel_mps2: float | None = None
    predicted_path_xy_m: list[XY] = []


class Obstacle(Msg):
    xy_m: XY
    source: Literal["lidar", "drone"] = "lidar"
    confidence: float = 0.5
    ts_ms: float


class Zone(Msg):
    zone_id: str
    name: str
    polygon_xy_m: list[XY]
    kind: Literal["task", "geofence", "keep_out"] = "task"


# --------------------------------------------------------------------------------------------------------------
# Events, decisions, alerts
# --------------------------------------------------------------------------------------------------------------


class Event(Msg):
    schema_version: str = SCHEMA_VERSION
    type: Literal["event"] = "event"
    event_id: str
    kind: str
    severity: Severity
    ts_ms: float
    text: str
    track_id: str | None = None
    role: str | None = None
    data: dict[str, Any] = {}
    evidence_refs: list[str] = []
    geometry_valid: bool = False
    ttl_ms: float = 1500.0
    source: str = "rules"


class DecisionAnswer(Msg):
    name: str
    type: Literal["choice", "predicate", "score", "refusal"]
    choice: str | None = None
    probability: float | None = None
    score: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = {}


class DecisionRecord(Msg):
    schema_version: str = SCHEMA_VERSION
    type: Literal["decision"] = "decision"
    decision_id: str
    task: Literal["D1_reacquire", "D2_identity", "D3_describe", "D4_motion", "D5_priority", "D6_obstacle",
                  "D7_lead_triage", "D8_next_sector", "D9_route", "D10_dispatch", "D11_opening",
                  "D12_pursuit_route", "D13_intent"]
    engine: str
    model: str | None = None
    status: Literal["success", "timeout", "error", "refusal", "skipped", "fallback"]
    latency_ms: float | None = None
    request_ts_ms: float
    event_ids: list[str] = []
    facts: dict[str, Any] = {}
    answers: list[DecisionAnswer] = []
    selected: str | None = None
    acted: bool = False
    reason: str = ""
    shadow: bool = False
    mock: bool = False
    image_used: bool = False
    error: str | None = None
    usage: dict[str, Any] | None = None


class Alert(Msg):
    schema_version: str = SCHEMA_VERSION
    type: Literal["alert"] = "alert"
    alert_id: str
    kind: str
    severity: Severity
    ts_ms: float
    expires_ts_ms: float
    text: str
    spoken_text: str
    bearing_rel_deg: float | None = None
    clock: int | None = None
    range_m: float | None = None
    closing_mps: float | None = None
    ttc_s: float | None = None
    haptic: str | None = None
    delivery: list[str] = ["dashboard"]
    message_source: str = "rules"
    evidence_event_ids: list[str] = []
    decision_id: str | None = None
    ack_required: bool = False
    acked: bool = False
    acked_ts_ms: float | None = None
    can_read_as_confirmed_location: bool = False


class TranscriptLine(Msg):
    type: Literal["transcript"] = "transcript"
    ts_ms: float
    text: str
    source: str
    alert_id: str | None = None


# --------------------------------------------------------------------------------------------------------------
# Autonomy
# --------------------------------------------------------------------------------------------------------------


class SafetyStatus(Msg):
    geofence_ok: bool = True
    geofence_margin_m: float | None = None
    altitude_ok: bool = True
    battery_state: Literal["ok", "warn", "rtl", "unknown"] = "unknown"
    link_ok: bool = True
    people_over: bool = False
    clamps: list[str] = []


class DispatchStatus(Msg):
    """A dispatch (simulator only): launch from the pad, search an area for a described person, track them and
    report their position relative to the launch point."""

    description: str
    phase: Literal["TAKEOFF", "TRANSIT", "SEARCH", "COVERED", "TRACKING", "LOST"]
    pad_xy: XY
    started_ms: float
    leg: int = 0
    legs: int = 0
    altitude_m: float = 28.0
    matched_track: str | None = None
    matched_by: str | None = None
    people_seen: int = 0
    rejected: int = 0
    last_report: dict[str, Any] | None = None  # the latest position report (distance and bearing from the pad)


class AutonomyState(Msg):
    motion_profile: Literal["full", "camera_only", "visual_follow"] = "full"
    mode: Literal["MANUAL", "FOLLOW", "HOLD", "RTL", "ESTOP", "DISPATCH", "GOTO"] = "MANUAL"
    fsm: Literal["IDLE", "TRACKING", "VERIFY", "COAST", "REACQUIRE", "HOLD", "TAKEOFF", "TRANSIT", "SEARCH", "COVERED"] = "IDLE"
    target_is_sim: bool = True
    link: Literal["sim", "real", "none"] = "sim"
    real_flight_allowed: bool = False
    commands_enabled: bool = False
    primitive: str | None = None
    primitive_source: str = "none"  # rules | decision:<engine> | none
    last_command: VStickCommand | None = None
    gimbal_cmd_deg: float | None = None
    safety: SafetyStatus = SafetyStatus()
    note: str | None = None
    route: list[XY] | None = None  # the pursuit route being flown (simulator) or advised (real aircraft)
    route_label: str | None = None
    reflex: str | None = None  # the low-altitude stack's reflex, when it is overriding the command
    maneuver: str | None = None  # a committed D6 maneuver (sidestep, climb, hold)
    scene_ahead_m: float | None = None  # free path ahead from the obstacle sectors (depth-camera stand-in)


IncidentProvenance = Literal["live", "replay", "simulated", "manual"]


class PursuitHypothesis(Msg):
    """One identity hypothesis of the street-graph belief (autonomy/belief.py)."""

    id: str
    label: str
    object_id: str | None = None
    track_id: str | None = None
    weight: float
    visible: bool = False
    colors: list[str] | None = None  # (torso, lower) colour words from the session histogram; never a face
    particles: int = 0
    evidence: list[dict[str, Any]] = []  # prior, description (manual), search ("looked, not seen")


class PursuitCandidate(Msg):
    id: str
    kind: Literal["street", "intersection"]
    label: str
    target_xy: XY
    coverage: float  # share of the whole belief the camera would see from there on arrival
    by_hypothesis: dict[str, float] = {}
    length_m: float
    eta_s: float
    penalty: float = 0.0
    score: float | None = None  # None when the safety supervisor rejects it (unsafe)
    unsafe: str | None = None
    why: str | None = None
    waypoints: list[XY] = []
    footprint: list[XY] | None = None


class PursuitRoute(Msg):
    candidate_id: str
    label: str
    waypoints: list[XY] = []
    source: str  # rules | decision:<engine> | rules (model below gate)
    engine: str | None = None
    model_pick: str | None = None
    baseline_pick: str | None = None
    confidence: float | None = None
    acted: bool = False
    reason: str = ""
    decision_id: str | None = None
    ts_ms: float
    provenance: IncidentProvenance = "simulated"


class PursuitState(Msg):
    """Where the lost subject could be, and why the drone flies where it does: the belief (particles per identity
    hypothesis on the street map), the candidate viewpoints with their coverage, and the route chosen."""

    active: bool
    provenance: IncidentProvenance = "simulated"  # of the observations the belief was built from
    map_source: str = "map data"
    started_ms: float | None = None
    lost_ms: float | None = None
    lost_s: float | None = None
    particles_per_hypothesis: int = 0
    hypotheses: list[PursuitHypothesis] = []
    particles: list[tuple[float, float, int]] = []  # weighted display sample: (x, y, hypothesis index)
    candidates: list[PursuitCandidate] = []
    candidates_ts_ms: float | None = None
    route: PursuitRoute | None = None
    description: dict[str, Any] | None = None  # the operator's words as parsed clothing evidence (provenance manual)
    dropped: list[dict[str, Any]] = []
    holding: str | None = None  # why the drone holds instead of flying a route (an unverified re-binding)
    ended: str | None = None  # why the last belief ended (reacquired, released, ...)


# --------------------------------------------------------------------------------------------------------------
# Aggregate state
# --------------------------------------------------------------------------------------------------------------


class EngineStatus(Msg):
    name: str
    available: bool
    mock: bool = False
    detail: str | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    calls: int = 0
    errors: int = 0
    timeouts: int = 0
    refusals: int = 0


class DecisionStatus(Msg):
    primary: str
    active: str
    ai_paused: bool = False
    pause_reason: str | None = None
    engines: list[EngineStatus] = []
    calls_total: int = 0
    acted_total: int = 0
    disagreements: int = 0
    mode_label: str = ""  # e.g. "rules only (no API key)"; shown on the dashboard
    session: int = 0
    session_model_calls: int = 0
    session_tokens: int = 0
    budget_exhausted: bool = False
    dropped_stale: int = 0
    rejected_invalid: int = 0


class Guardrail(Msg):
    id: str
    label: str
    status: Literal["enforced", "active", "warning", "disabled"]
    detail: str = ""


class IncidentState(Msg):
    incident_id: str | None = None
    active: bool = False
    recording: bool = False
    started_ts_ms: float | None = None
    consent_confirmed: bool = False


class SimStatus(Msg):
    scenario: str
    scenario_name: str
    drone_model: str | None = None
    stack: str | None = None  # mini2 (kinematic stand-in) | jev-x2 (MuJoCo X2, reflex and D6 at low altitude)
    t_s: float
    duration_s: float | None = None
    speed: float = 1.0
    paused: bool = False
    faults: dict[str, Any] = {}
    gt_available: bool = True


class MetricsSnapshot(Msg):
    counters: dict[str, float] = {}
    latencies: dict[str, dict[str, float | None]] = {}
    rates: dict[str, float] = {}


class WorldState(Msg):
    schema_version: str = SCHEMA_VERSION
    type: Literal["state"] = "state"
    session_id: str
    seq: int
    server_ts_ms: float
    mode: Mode
    provenance: Provenance
    mission_profile: str
    map_mode: Literal["REGISTERED_METRIC", "DRONE_LOCAL", "PHONE_ONLY", "UNCALIBRATED"]
    sources: list[SourceHealth] = []
    calibration: CalibrationStatus = CalibrationStatus()
    registration: RegistrationStatus = RegistrationStatus()
    drone: DroneState | None = None
    phone: PhoneSummary = PhoneSummary()
    responder: ResponderState = ResponderState()
    tracks: list[Track] = []
    selected_track_id: str | None = None
    locks: dict[str, LockState] = {}
    memory: list[dict[str, Any]] = []  # object memory (tracking/memory.py): persistent people, hidden ones too
    kinematics: RelativeKinematics = RelativeKinematics()
    obstacles: list[tuple[float, float, float]] = []  # registered LiDAR points (x_m, y_m, age_ms), compact
    zones: list[Zone] = []
    geofence: list[XY] = []
    autonomy: AutonomyState = AutonomyState()
    dispatch: DispatchStatus | None = None
    pursuit: PursuitState | None = None
    voice: dict[str, Any] | None = None  # operator commands: transcript, intent, primitive, question, approval
    rescue: dict[str, Any] | None = None  # labelled simulator beacon and crew state
    crew_routing: dict[str, Any] | None = None  # ground-crew routes through water or fire, planned live
    decisions: DecisionStatus | None = None
    alerts_active: list[Alert] = []
    metrics: MetricsSnapshot = MetricsSnapshot()
    sim: SimStatus | None = None
    incident: IncidentState = IncidentState()
    guardrails: list[Guardrail] = []
    notices: list[str] = []
    narration: str | None = None


class FrameOverlayTrack(Msg):
    track_id: str
    box: tuple[float, float, float, float]
    class_name: str
    confidence: float
    state: str
    role: str | None = None
    label: str = ""
    velocity_px: XY | None = None
    ground_xy_m: XY | None = None


class FrameOverlay(Msg):
    """JSON header sent with each video frame on /ws/video/{source}: overlays belong to *this* frame."""

    type: Literal["frame"] = "frame"
    source_id: str
    frame_id: int
    capture_ts_ms: float
    server_ts_ms: float
    width: int
    height: int
    provenance: Provenance
    tracks: list[FrameOverlayTrack] = []
    detections: int = 0
    markers: list[MarkerObservation] = []
    zones_px: list[dict[str, Any]] = []
    horizon: dict[str, float] | None = None
    cmc: dict[str, Any] | None = None
    detector_ms: float | None = None
    model_id: str | None = None
    calibration_valid: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)
