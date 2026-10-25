"""Typed configuration loaded from config/default.yaml, an optional override YAML, and environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.yaml"


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SessionCfg(_Section):
    mode: Literal["SIMULATED", "LIVE", "REPLAY"] = "SIMULATED"
    scenario: str = "demo"
    mission_profile: Literal["responder_safety", "search_rescue"] = "responder_safety"
    recording_enabled: bool = False
    consenting_participants_only: bool = True
    replay_path: str | None = None


class ServerCfg(_Section):
    host: str = "0.0.0.0"
    port: int = 8000
    state_hz: float = 10.0
    video_jpeg_quality: int = 72
    video_max_fps: float = 15.0
    cors_origins: list[str] = ["*"]


class GeoRef(_Section):
    lat: float
    lon: float
    alt_m: float = 0.0


class WorldCfg(_Section):
    geo_reference: GeoRef | None = None
    north_offset_deg: float = 0.0


class DroneSourceCfg(_Section):
    adapter: str = "auto"
    url: str | None = None
    target_fps: float = 15.0
    max_age_ms: float = 500.0
    width: int = 960
    height: int = 540
    hfov_deg: float = 71.6


class PhoneSourceCfg(_Section):
    adapter: str = "auto"
    target_hz: float = 10.0
    max_age_ms: float = 350.0
    video_width: int = 640
    video_height: int = 480
    hfov_deg: float = 64.0


class TelemetrySourceCfg(_Section):
    max_age_ms: float = 1000.0


class SourcesCfg(_Section):
    drone: DroneSourceCfg = DroneSourceCfg()
    phone: PhoneSourceCfg = PhoneSourceCfg()
    telemetry: TelemetrySourceCfg = TelemetrySourceCfg()


class TrackerCfg(_Section):
    high_thresh: float = 0.5
    low_thresh: float = 0.15
    match_iou: float = 0.25
    n_init: int = 3
    lost_after_ms: float = 300.0
    expire_after_ms: float = 4000.0
    trail_seconds: float = 30.0


class CmcCfg(_Section):
    enabled: bool = True
    max_corners: int = 400
    min_inliers: int = 20


class TilingCfg(_Section):
    enabled: bool = False
    tile_px: int = 640
    overlap: float = 0.2
    full_frame: bool = True


class PerceptionCfg(_Section):
    detector: str = "auto"
    yolo_weights: str = "yolo26n.pt"
    yolo_imgsz: int = 960
    confidence_min: float = 0.25
    classes: list[str] = ["person", "bicycle", "car", "motorcycle", "bus", "truck"]
    device: str = "auto"
    tiling: TilingCfg = TilingCfg()
    tracker: TrackerCfg = TrackerCfg()
    cmc: CmcCfg = CmcCfg()


class MarkersCfg(_Section):
    dictionary: str = "DICT_4X4_50"
    size_m: float = 1.0
    layout: dict[int, tuple[float, float]] = {}
    holdout_ids: list[int] = []
    detect_every_n_frames: int = 3


class CalibrationCfg(_Section):
    markers: MarkersCfg = MarkersCfg()
    min_markers: int = 4
    max_holdout_rmse_m: float = 0.5
    max_age_s: float = 10.0
    chained_max_age_s: float = 6.0
    telemetry_projection: bool = True
    telemetry_error_m_per_m_alt: float = 0.06


class RegistrationCfg(_Section):
    min_pairs: int = 12
    min_travel_m: float = 2.0
    max_residual_m: float = 0.6
    window_s: float = 30.0


class PhoneCfg(_Section):
    corridor_half_width_m: float = 0.9
    max_range_m: float = 5.0
    min_obstacle_height_m: float = 0.15
    max_obstacle_height_m: float = 2.0
    min_points: int = 25
    persistence_frames: int = 2
    proximity_alert_m: float = 1.2
    registration: RegistrationCfg = RegistrationCfg()


class FusionCfg(_Section):
    occupancy_cell_m: float = 0.25
    occupancy_size_m: tuple[float, float] = (60.0, 40.0)
    occupancy_origin_m: tuple[float, float] = (-20.0, -14.0)
    track_ttl_ms: float = 750.0
    ground_contact_sigma_m: float = 0.35


class LocksCfg(_Section):
    gallery_size: int = 30
    rebind_similarity: float = 0.58
    rebind_margin: float = 0.10
    search_radius_m: float = 3.0
    search_growth_mps: float = 2.5
    coast_after_ms: float = 700.0
    reacquire_after_ms: float = 2000.0
    hold_after_s: float = 20.0


class KinematicsCfg(_Section):
    closing_window_s: float = 0.8
    min_samples: int = 5


class AlertsCfg(_Section):
    closing_fast_mps: float = 2.5
    closing_alignment_cos: float = 0.8
    closing_sustain_s: float = 0.4
    ttc_critical_s: float = 3.0
    warning_range_m: float = 8.0
    warning_closing_mps: float = 1.0
    callout_interval_s: float = 8.0
    callout_bearing_change_deg: float = 30.0
    track_lost_ms: float = 700.0
    cooldown_s: float = 4.0
    max_speech_per_minute: int = 12
    alert_ttl_s: float = 6.0
    local_proximity_independent_of_ai: bool = True
    allow_identity_claims: bool = False


class DecisionsCfg(_Section):
    primary: str = "openai"
    shadow: list[str] = []
    fallback_chain: list[str] = ["strands", "rules"]
    allow_mock: bool = True
    openai_model: str = "gpt-6-luna"
    openai_base_url: str = "https://api.openai.com/v1"
    typesafe_model: str = "jev-latest"
    typesafe_url: str = "https://api.typesafe.ai/v1/systemone"
    strands_url: str = "http://127.0.0.1:8099/v1/systemone"
    deadline_ms: float = 800.0
    min_confidence: float = 0.70
    identity_min_confidence: float = 0.75
    # every D1 option is a bounded manoeuvre under the safety supervisor: a wrong pick costs search time, not safety
    min_confidence_by_task: dict[str, float] = {"D1_reacquire": 0.5}
    stale_after_s: float = 1.5  # answers older than this (or from an earlier session) are logged and dropped
    max_hz_per_task: float = 3.0
    pursuit_engine: str = "rules"  # rules | model: who answers D1, D4 and D12 while following (rules: no API calls)
    voice_engine: str = "rules"  # rules | model: who answers D13_intent (rules: the keyword grammar, no API calls)
    # cost guard per session (one mission run): past either budget the router answers with rules only and says so
    max_model_calls_per_session: int = 2000
    max_tokens_per_session: int = 400_000
    probability_sum_tolerance: float = 0.05
    allow_images: bool = False
    log_path: str | None = None


class AutonomyCfg(_Section):
    enabled: bool = True
    allow_real_flight: bool = False
    motion_profile: Literal["full", "camera_only", "visual_follow"] = "full"
    camera_yaw_max_dps: float = Field(default=15.0, gt=0, le=15)
    camera_gimbal_rate_dps: float = Field(default=10.0, gt=0, le=10)
    visual_max_speed_mps: float = Field(default=0.5, gt=0, le=0.5)
    standoff_m: float = 12.0
    altitude_m: float = 18.0
    min_altitude_m: float = 12.0
    max_altitude_m: float = 60.0
    follow_max_speed_mps: float = 4.0
    reacquire_max_speed_mps: float = 6.0
    max_yaw_rate_dps: float = 45.0
    max_vertical_mps: float = 1.5
    command_hz: float = 20.0
    command_ttl_ms: float = 300.0
    link_timeout_s: float = 2.0
    battery_warn_pct: float = 30.0
    battery_rtl_pct: float = 20.0
    people_over_radius_m: float = 3.0
    geofence: list[tuple[float, float]] = []


class VoiceCfg(_Section):
    transcription_model: str = "gpt-transcribe"
    narrator: str = "template"
    gemini_model: str = "gemini-3.8-flash"
    summary_interval_s: float = 10.0
    tts: str = "client"
    elevenlabs_model: str = "eleven_flash_v2_5"


class PrivacyCfg(_Section):
    incident_required_for_lock: bool = True
    crowd_threshold: int = 12
    retain_video: bool = False
    send_images_to_cloud: bool = False


class Secrets(_Section):
    """Loaded only from the environment; never serialised to clients."""

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    typesafe_api_key: str | None = None
    strands_url: str | None = None
    gemini_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    tiger_database_url: str | None = None  # Tiger Data (TimescaleDB): mirrors events, telemetry and detections
    # ElevenLabs accounts as (account number, key), preferred first: ELEVENLABS_ACCOUNT picks the primary,
    # the others are automatic fallbacks (ELEVENLABS_API_KEY = account 1, ELEVENLABS_API_KEY2 = account 2, ...)
    elevenlabs_accounts: list[tuple[int, str]] = []
    overwatch_token: str | None = None


class SARCfg(_Section):
    enabled: bool = True  # simulated hazard worlds only; never aircraft control
    threshold: float = Field(default=3.0, gt=0)
    flood_depth_m: float = Field(default=0.15, ge=0)
    flood_weight: float = Field(default=3.0, ge=0)
    fire_weight: float = Field(default=3.0, ge=0)
    stationary_weight: float = Field(default=1.0, ge=0)
    stationary_s: float = Field(default=10.0, gt=0)
    min_frames: int = Field(default=3, ge=2)
    route_max_depth_m: float = Field(default=0.15, ge=0, le=0.3)
    approach_radius_m: float = Field(default=3.0, gt=0, le=10)


class Config(_Section):
    session: SessionCfg = SessionCfg()
    server: ServerCfg = ServerCfg()
    world: WorldCfg = WorldCfg()
    sources: SourcesCfg = SourcesCfg()
    perception: PerceptionCfg = PerceptionCfg()
    calibration: CalibrationCfg = CalibrationCfg()
    phone: PhoneCfg = PhoneCfg()
    fusion: FusionCfg = FusionCfg()
    locks: LocksCfg = LocksCfg()
    kinematics: KinematicsCfg = KinematicsCfg()
    alerts: AlertsCfg = AlertsCfg()
    decisions: DecisionsCfg = DecisionsCfg()
    autonomy: AutonomyCfg = AutonomyCfg()
    voice: VoiceCfg = VoiceCfg()
    privacy: PrivacyCfg = PrivacyCfg()
    sar: SARCfg = SARCfg()
    secrets: Secrets = Field(default_factory=Secrets, exclude=True)

    def public_dict(self) -> dict[str, Any]:
        """Config safe to show in the dashboard (no secrets, only key presence)."""
        d = self.model_dump(mode="json", exclude={"secrets"})
        d["keys_present"] = {
            "openai": bool(self.secrets.openai_api_key),
            "typesafe": bool(self.secrets.typesafe_api_key),
            "tiger": bool(self.secrets.tiger_database_url),
            "gemini": bool(self.secrets.gemini_api_key),
            "elevenlabs": bool(self.secrets.elevenlabs_api_key),
            "access_token_required": bool(self.secrets.overwatch_token),
        }
        return d


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _tiger_url_from_file() -> str | None:
    """The Tiger Cloud credentials file as downloaded from the console (``Service URL: postgres://...``), looked
    for at the repository root or at TIGER_CREDENTIALS_FILE. Read, never logged."""
    import re

    candidates = [Path(os.environ["TIGER_CREDENTIALS_FILE"])] if os.environ.get("TIGER_CREDENTIALS_FILE") else []
    candidates += sorted(REPO_ROOT.glob("tiger-cloud-*credentials*.txt"))
    for path in candidates:
        try:
            m = re.search(r"postgres(?:ql)?://\S+", path.read_text())
        except OSError:
            continue
        if m:
            return m.group(0)
    return None


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); existing environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and key not in os.environ:
            os.environ[key] = value


_ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "OVERWATCH_MODE": ("session", "mode"),
    "OVERWATCH_SCENARIO": ("session", "scenario"),
    "OVERWATCH_PROFILE": ("session", "mission_profile"),
    "OVERWATCH_REPLAY_PATH": ("session", "replay_path"),
    "OVERWATCH_DRONE_URL": ("sources", "drone", "url"),
    "OVERWATCH_DRONE_ADAPTER": ("sources", "drone", "adapter"),
    "OVERWATCH_PHONE_ADAPTER": ("sources", "phone", "adapter"),
    "OVERWATCH_DETECTOR": ("perception", "detector"),
    "OVERWATCH_YOLO_WEIGHTS": ("perception", "yolo_weights"),
    "OVERWATCH_DECISIONS_PRIMARY": ("decisions", "primary"),
    "OVERWATCH_PORT": ("server", "port"),
}


def load_config(override_path: str | os.PathLike[str] | None = None, **overrides: Any) -> Config:
    _load_dotenv(REPO_ROOT / ".env")
    data: dict[str, Any] = {}
    if DEFAULT_CONFIG_PATH.exists():
        data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()) or {}
    override_path = override_path or os.environ.get("OVERWATCH_CONFIG")
    if override_path:
        data = _deep_merge(data, yaml.safe_load(Path(override_path).read_text()) or {})
    for env, path in _ENV_OVERRIDES.items():
        if env in os.environ and os.environ[env] != "":
            cursor = data
            for key in path[:-1]:
                cursor = cursor.setdefault(key, {})
            cursor[path[-1]] = os.environ[env]
    if overrides:
        data = _deep_merge(data, overrides)
    cfg = Config.model_validate(data)
    cfg.secrets = Secrets(
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        tiger_database_url=(os.environ.get("TIGER_DATABASE_URL") or os.environ.get("CONNECTION_STRING")
                            or _tiger_url_from_file()),
        openai_base_url=os.environ.get("OPENAI_BASE_URL") or None,
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY") or None,
        strands_url=os.environ.get("STRANDS_URL") or None,
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None,
        elevenlabs_voice_id=os.environ.get("ELEVENLABS_VOICE_ID") or None,
        elevenlabs_accounts=_elevenlabs_accounts(),
        overwatch_token=os.environ.get("OVERWATCH_TOKEN") or None,
    )
    cfg.secrets.elevenlabs_api_key = cfg.secrets.elevenlabs_accounts[0][1] if cfg.secrets.elevenlabs_accounts else None
    return cfg


def _elevenlabs_accounts() -> list[tuple[int, str]]:
    accts = []
    for n in range(1, 6):
        key = os.environ.get("ELEVENLABS_API_KEY" + ("" if n == 1 else str(n))) or None
        if key:
            accts.append((n, key))
    try:
        pref = int(os.environ.get("ELEVENLABS_ACCOUNT") or 1)
    except ValueError:
        pref = 1
    return sorted(accts, key=lambda a: (a[0] != pref, a[0]))
