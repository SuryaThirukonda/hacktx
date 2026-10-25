// Mirror of backend/overwatch/schemas.py (schema_version 1.0). Missing geometry is null, never 0.

export type XY = [number, number];
export type Box = [number, number, number, number];
export type Mode = "SIMULATED" | "LIVE" | "REPLAY";
export type Provenance = "LIVE" | "RECORDED" | "SYNTHETIC";
export type Severity = "info" | "attention" | "warning" | "critical" | "sensor_fault" | "local_proximity";

export interface SourceHealth {
  source_id: string;
  kind: string;
  status: "OK" | "STALE" | "OFFLINE" | "DEGRADED" | "NOT_CONFIGURED" | "PAUSED";
  provenance: Provenance | null;
  last_age_ms: number | null;
  fps: number | null;
  received: number;
  processed: number;
  dropped: number;
  reconnects: number;
  detail: string | null;
}

export interface DepthSector {
  sector: "left" | "front_left" | "front" | "front_right" | "right";
  nearest_m: number | null;
  confidence: "high" | "medium" | "low" | "unknown";
  num_valid_points: number;
}

export interface Track {
  track_id: string;
  class_name: string;
  confidence: number;
  state: "TENTATIVE" | "CONFIRMED" | "LOST" | "EXPIRED";
  visibility: "visible" | "occluded" | "lost";
  source: "drone" | "phone";
  model_id: string;
  box_xyxy_px: Box | null;
  first_seen_ts_ms: number;
  last_seen_ts_ms: number;
  hits: number;
  ground_xy_m: XY | null;
  ground_error_m: number | null;
  velocity_xy_mps: XY | null;
  speed_mps: number | null;
  heading_deg: number | null;
  velocity_valid: boolean;
  calibration_id: string | null;
  lat: number | null;
  lon: number | null;
  role: "subject" | "responder" | null;
  trail: [number, number, number][];
}

export interface MarkerObservation {
  id: number;
  center_px: XY;
  corners_px: XY[];
  world_xy: XY | null;
  used_for_fit: boolean;
  holdout: boolean;
  error_m: number | null;
}

export interface CalibrationStatus {
  calibration_id: string | null;
  source: "markers" | "chained" | "telemetry" | "manual" | "none";
  valid: boolean;
  reason: string;
  H_img_to_ground: number[][] | null;
  markers_detected: number;
  markers_used: number;
  holdout_count: number;
  holdout_rmse_m: number | null;
  fit_rmse_m: number | null;
  age_s: number | null;
  error_bound_m: number | null;
  markers: MarkerObservation[];
  last_marker_calibration_id: string | null;
}

export interface RegistrationStatus {
  method: "auto_trajectory" | "compass_point" | "manual_two_point" | "none";
  valid: boolean;
  confidence: "high" | "medium" | "low" | "none";
  residual_m: number | null;
  pairs: number;
  yaw_deg: number | null;
  translation_m: XY | null;
  reason: string;
}

export interface DroneState {
  position_xy_m: XY | null;
  alt_m: number | null;
  yaw_deg: number | null;
  gimbal_pitch_deg: number | null;
  battery_pct: number | null;
  velocity_xy_mps: XY | null;
  lat: number | null;
  lon: number | null;
  flying: boolean;
  vstick_enabled: boolean;
  sim: boolean;
  footprint_xy_m: XY[] | null;
  telemetry_age_ms: number | null;
}

export interface PhoneSummary {
  connected: boolean;
  provenance: Provenance | null;
  tracking_state: string | null;
  lidar_supported: boolean | null;
  depth_valid_fraction: number | null;
  sectors: DepthSector[];
  nearest_m: number | null;
  nearest_sector: string | null;
  local_alert_active: boolean;
  heading_deg: number | null;
  heading_accuracy_deg: number | null;
  age_ms: number | null;
  frustum_xy_m: XY[] | null;
}

export interface ResponderState {
  position_xy_m: XY | null;
  position_source: "drone_lock" | "arkit_registered" | "gps" | "none";
  position_error_m: number | null;
  heading_deg: number | null;
  heading_source: string;
  heading_valid: boolean;
  track_id: string | null;
  lat: number | null;
  lon: number | null;
}

export interface LockCandidate {
  track_id: string;
  similarity: number;
  distance_m: number | null;
}

export interface LockState {
  role: "subject" | "responder";
  state: "UNLOCKED" | "LOCKED" | "COAST" | "REACQUIRE" | "UNCERTAIN" | "HOLD";
  track_id: string | null;
  since_ms: number | null;
  lost_ms: number | null;
  predicted_xy_m: XY | null;
  search_radius_m: number | null;
  last_seen_xy_m: XY | null;
  last_seen_ts_ms: number | null;
  candidates: LockCandidate[];
  needs_operator_confirm: boolean;
  verified: boolean;
  rebinds: number;
  description: string | null;
}

export interface RelativeKinematics {
  valid: boolean;
  reason: string;
  range_m: number | null;
  range_error_m: number | null;
  bearing_rel_deg: number | null;
  bearing_abs_deg: number | null;
  clock: number | null;
  closing_mps: number | null;
  ttc_s: number | null;
  alignment_cos: number | null;
  subject_speed_mps: number | null;
  subject_heading_deg: number | null;
  subject_accel_mps2: number | null;
  predicted_path_xy_m: XY[];
}

export interface Obstacle {
  xy_m: XY;
  source: "lidar" | "drone";
  confidence: number;
  ts_ms: number;
}

export interface Zone {
  zone_id: string;
  name: string;
  polygon_xy_m: XY[];
  kind: "task" | "geofence" | "keep_out";
}

export interface EventMsg {
  type: "event";
  event_id: string;
  kind: string;
  severity: Severity;
  ts_ms: number;
  text: string;
  track_id: string | null;
  role: string | null;
  data: Record<string, unknown>;
  evidence_refs: string[];
  geometry_valid: boolean;
  ttl_ms: number;
  source: string;
}

export interface DecisionAnswer {
  name: string;
  type: "choice" | "predicate" | "score" | "refusal";
  choice: string | null;
  probability: number | null;
  score: number | null;
  confidence: number | null;
  probabilities: Record<string, number>;
}

export interface DecisionRecord {
  type: "decision";
  decision_id: string;
  task: "D1_reacquire" | "D2_identity" | "D3_describe" | "D4_motion" | "D5_priority" | "D6_obstacle" | "D7_lead_triage" | "D8_next_sector" | "D9_route" | "D10_dispatch" | "D11_opening" | "D12_pursuit_route" | "D13_intent";
  engine: string;
  model: string | null;
  status: "success" | "timeout" | "error" | "refusal" | "skipped" | "fallback";
  latency_ms: number | null;
  request_ts_ms: number;
  event_ids: string[];
  facts: Record<string, unknown>;
  answers: DecisionAnswer[];
  selected: string | null;
  acted: boolean;
  reason: string;
  shadow: boolean;
  mock: boolean;
  image_used: boolean;
  error: string | null;
}

export interface Alert {
  type: "alert";
  alert_id: string;
  kind: string;
  severity: Severity;
  ts_ms: number;
  expires_ts_ms: number;
  text: string;
  spoken_text: string;
  bearing_rel_deg: number | null;
  clock: number | null;
  range_m: number | null;
  closing_mps: number | null;
  ttc_s: number | null;
  haptic: string | null;
  delivery: string[];
  message_source: string;
  evidence_event_ids: string[];
  decision_id: string | null;
  ack_required: boolean;
  acked: boolean;
  acked_ts_ms: number | null;
  can_read_as_confirmed_location: boolean;
}

export interface TranscriptLine {
  type: "transcript";
  ts_ms: number;
  text: string;
  source: string;
  alert_id: string | null;
}

export interface VStickCommand {
  seq: number;
  ts_ms: number;
  forward_mps: number;
  right_mps: number;
  up_mps: number;
  yaw_rate_dps: number;
  ttl_ms: number;
}

export interface SafetyStatus {
  geofence_ok: boolean;
  geofence_margin_m: number | null;
  altitude_ok: boolean;
  battery_state: "ok" | "warn" | "rtl" | "unknown";
  link_ok: boolean;
  people_over: boolean;
  clamps: string[];
}

export interface DispatchStatus {
  description: string;
  phase: "TAKEOFF" | "TRANSIT" | "SEARCH" | "COVERED" | "TRACKING" | "LOST";
  pad_xy: XY;
  started_ms: number;
  leg: number;
  legs: number;
  altitude_m: number;
  matched_track: string | null;
  matched_by: string | null;
  people_seen: number;
  rejected: number;
  last_report: {
    ts_ms: number; x_m: number; y_m: number; distance_m: number; bearing_deg: number; compass: string; speed_mps: number;
    heading_deg: number | null; uncertainty_m: number | null; state: string; relative_to: string; lat?: number; lon?: number;
  } | null;
}

export interface AutonomyState {
  motion_profile?: "full" | "camera_only" | "visual_follow";
  mode: "MANUAL" | "FOLLOW" | "HOLD" | "RTL" | "ESTOP" | "DISPATCH" | "GOTO";
  fsm: "IDLE" | "TRACKING" | "VERIFY" | "COAST" | "REACQUIRE" | "HOLD" | "TAKEOFF" | "TRANSIT" | "SEARCH" | "COVERED";
  target_is_sim: boolean;
  link: "sim" | "real" | "none";
  real_flight_allowed: boolean;
  commands_enabled: boolean;
  primitive: string | null;
  primitive_source: string;
  last_command: VStickCommand | null;
  gimbal_cmd_deg: number | null;
  safety: SafetyStatus;
  note: string | null;
  route?: [number, number][] | null;
  route_label?: string | null;
  reflex?: string | null;
  maneuver?: string | null;
  scene_ahead_m?: number | null;
}

export interface EngineStatus {
  name: string;
  available: boolean;
  mock: boolean;
  detail: string | null;
  p50_ms: number | null;
  p95_ms: number | null;
  calls: number;
  errors: number;
  timeouts: number;
  refusals: number;
}

export interface DecisionStatus {
  primary: string;
  active: string;
  ai_paused: boolean;
  pause_reason: string | null;
  engines: EngineStatus[];
  calls_total: number;
  acted_total: number;
  disagreements: number;
  mode_label?: string;
  session?: number;
  session_model_calls?: number;
  session_tokens?: number;
  budget_exhausted?: boolean;
  dropped_stale?: number;
  rejected_invalid?: number;
}

export interface Guardrail {
  id: string;
  label: string;
  status: "enforced" | "active" | "warning" | "disabled";
  detail: string;
}

export interface IncidentState {
  incident_id: string | null;
  active: boolean;
  recording: boolean;
  started_ts_ms: number | null;
  consent_confirmed: boolean;
}

export interface SimStatus {
  scenario: string;
  scenario_name: string;
  drone_model?: string | null;
  stack?: string | null;
  t_s: number;
  duration_s: number | null;
  speed: number;
  paused: boolean;
  faults: Record<string, unknown>;
  gt_available: boolean;
  loops?: number;
}

export interface LatencySummary {
  n: number;
  p50: number | null;
  p95: number | null;
  max: number | null;
  last: number | null;
}

export interface MetricsSnapshot {
  counters: Record<string, number>;
  latencies: Record<string, LatencySummary>;
  rates: Record<string, number>;
}

/** Object memory (backend tracking/memory.py): a persistent person, including while hidden. */
export interface MemoryObject {
  object_id: string;
  label: string;
  state: "VISIBLE" | "OCCLUDED" | "OUT_OF_VIEW" | "LOST";
  track_id: string | null;
  xy: [number, number];
  velocity: [number, number];
  error_m: number;
  radius_m: number;
  since_seen_s: number;
  confidence: number;
  link: "first_seen" | "continued" | "possible";
  track_ids: string[];
  ghost_box?: [number, number, number, number];
}

export type IncidentProvenance = "live" | "replay" | "simulated" | "manual";

/** One identity hypothesis of the street-graph belief (backend autonomy/belief.py). */
export interface PursuitHypothesis {
  id: string;
  label: string;
  object_id: string | null;
  track_id: string | null;
  weight: number;
  visible: boolean;
  colors: string[] | null;
  particles: number;
  evidence: { kind: string; value?: number; why?: string; likelihood?: number; factor?: number; text?: string; provenance?: string; ts_ms?: number }[];
}

export interface PursuitCandidate {
  id: string;
  kind: "street" | "intersection";
  label: string;
  target_xy: XY;
  coverage: number;
  by_hypothesis: Record<string, number>;
  length_m: number;
  eta_s: number;
  penalty: number;
  score: number | null;
  unsafe: string | null;
  why: string | null;
  waypoints: XY[];
  footprint: XY[] | null;
}

export interface PursuitRoute {
  candidate_id: string;
  label: string;
  waypoints: XY[];
  source: string;
  engine: string | null;
  model_pick: string | null;
  baseline_pick: string | null;
  confidence: number | null;
  acted: boolean;
  reason: string;
  decision_id: string | null;
  ts_ms: number;
  provenance: IncidentProvenance;
}

/** Where the lost subject could be and why the drone flies where it does. */
export interface PursuitState {
  active: boolean;
  provenance: IncidentProvenance;
  map_source: string;
  started_ms: number | null;
  lost_ms: number | null;
  lost_s: number | null;
  particles_per_hypothesis: number;
  hypotheses: PursuitHypothesis[];
  particles: [number, number, number][]; // x, y, hypothesis index
  candidates: PursuitCandidate[];
  candidates_ts_ms: number | null;
  route: PursuitRoute | null;
  description: { text: string; parsed: string; likelihoods: Record<string, number>; by: string; provenance: IncidentProvenance } | null;
  dropped: { hid: string; label: string; why: string; ts_ms: number }[];
  holding: string | null;
  ended: string | null;
}

export interface VoicePrimitive {
  id: string;
  command: string;
  primitive: "FOLLOW" | "SEARCH" | "GOTO" | "FIND" | "HOLD" | "RTL";
  parameters: Record<string, unknown>;
  proposed_by: "human";
  approval_status: "pending" | "approved" | "rejected" | "not_required";
  execution_status: "not_started" | "done" | "failed" | "cancelled";
  safety_constraints: string[];
  reason: string;
  transcript: string;
  intent_confidence: number | null;
  engine: string | null;
  provenance: IncidentProvenance;
  ts_ms: number;
}

export interface VoiceCommand {
  id: string;
  transcript: string;
  source: string;
  by: string;
  ts_ms: number;
  provenance: IncidentProvenance;
  status: "received" | "parsing" | "asking" | "awaiting_approval" | "done" | "failed" | "declined" | "rejected" | "not_found" | "ignored";
  intent?: string | null;
  confidence?: number;
  engine?: string;
  reply?: string;
  question?: string;
  primitive?: VoicePrimitive | null;
  grammar_confidence?: number | null;
}

/** Operator commands (spoken or typed) and what became of them (backend voice/commands.py). */
export interface VoiceView {
  commands: VoiceCommand[];
  question: { cmd: string; question: string; intent: string; options: { id: string; kind: string; label: string; xy: XY }[]; ts_ms: number } | null;
  pending: VoicePrimitive | null;
  require_approval_sim: boolean;
  engine: string;
  gate: number;
}

export interface WorldState {
  rescue?: { ping?: { xy: XY }; beacon?: { xy: XY; status?: string }; phase?: string } | null;
  schema_version: string;
  type: "state";
  session_id: string;
  seq: number;
  server_ts_ms: number;
  mode: Mode;
  provenance: Provenance;
  mission_profile: string;
  map_mode: "REGISTERED_METRIC" | "DRONE_LOCAL" | "PHONE_ONLY" | "UNCALIBRATED";
  sources: SourceHealth[];
  calibration: CalibrationStatus;
  registration: RegistrationStatus;
  drone: DroneState | null;
  phone: PhoneSummary;
  responder: ResponderState;
  tracks: Track[];
  selected_track_id: string | null;
  locks: Record<string, LockState>;
  memory?: MemoryObject[];
  kinematics: RelativeKinematics;
  obstacles: [number, number, number][]; // registered LiDAR points: x_m, y_m, age_ms
  zones: Zone[];
  geofence: XY[];
  autonomy: AutonomyState;
  dispatch?: DispatchStatus | null;
  pursuit?: PursuitState | null;
  voice?: VoiceView | null;
  crew_routing?: { kind: string; label: string; ts_ms: number; depart_s: number; provenance: IncidentProvenance; route: Record<string, unknown> | null; alternatives: Record<string, unknown>[]; why: string | null; level_m?: number } | null;
  decisions: DecisionStatus | null;
  alerts_active: Alert[];
  metrics: MetricsSnapshot;
  sim: SimStatus | null;
  incident: IncidentState;
  guardrails: Guardrail[];
  notices: string[];
  narration: string | null;
}

export interface Extras {
  bridge_control?: { connected?: boolean; legacy: boolean; capable: boolean; inhibited: boolean; motion_profile: string | null; web_control_supported?: boolean; pilot_reason?: string | null; error?: string | null } | null;
  type: "extras";
  occupancy: { png_b64: string; origin_xy: XY; cell_m: number; nx: number; ny: number; updates: number } | null;
  motion_label: { label: string | null; confidence: number | null; engine: string; acted: boolean; ts_ms: number } | null;
  describe_proposal: {
    decision_id: string;
    description: string;
    track_id: string | null;
    label: string | null;
    confidence: number | null;
    engine: string;
    acted: boolean;
    reason: string;
  } | null;
  look_for?: { text: string; since_ms: number; by: string } | null;
  cross_view: { phone_box: number; drone_track: string | null; range_m: number | null }[];
  autonomy_audit: { ts_ms: number; kind: string; text: string }[];
  bridge_audit: { ts_ms: number; kind: string; text: string }[];
  suppressed: { event_id: string; kind: string; reason: string }[];
  alert_latency_ms: number | null;
}

export interface FrameOverlayTrack {
  track_id: string;
  box: Box;
  class_name: string;
  confidence: number;
  state: string;
  role: "subject" | "responder" | null;
  label: string;
  velocity_px: XY | null;
  ground_xy_m: XY | null;
}

export interface FrameOverlay {
  type: "frame";
  source_id: string;
  frame_id: number;
  capture_ts_ms: number;
  server_ts_ms: number;
  width: number;
  height: number;
  provenance: Provenance;
  tracks: FrameOverlayTrack[];
  detections: number;
  markers: MarkerObservation[];
  zones_px: { zone_id: string; name: string; kind: string; polygon: XY[] }[];
  cmc: { ok: boolean; inliers: number; shift_px: number } | null;
  detector_ms: number | null;
  model_id: string | null;
  calibration_valid: boolean;
  extra: Record<string, unknown>;
}

export interface FaultSpec {
  type: "bool" | "float";
  label: string;
  group: string;
  min?: number;
  max?: number;
}

export interface ScenarioInfo {
  id: string;
  name: string;
  description: string;
  duration_s: number;
  tags: string[];
  loop: boolean;
}

export interface ResponderView {
  type: "responder_view";
  ts_ms: number;
  session_id: string;
  subject_state: string;
  verified: boolean;
  kin: RelativeKinematics;
  responder: ResponderState;
  sectors: DepthSector[];
  local_alert: boolean;
  alerts: Alert[];
  mode: Mode;
  ai_paused: boolean;
  profile: string;
}
