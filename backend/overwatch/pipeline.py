"""The single execution graph used in LIVE, REPLAY and SIMULATED modes (Rescue Copilot §14.1):

    drone frame -> detect -> CMC -> track -> calibrate -> project -> locks -> registration pairs
    phone obs   -> pose/registration -> occupancy/obstacles
    tick (10 Hz) -> responder pose -> kinematics -> rules -> arbiter (+D5) -> decisions (D1/D2/D4) -> narration
    autonomy tick (20 Hz) -> IBVS/FSM -> safety -> virtual stick

All scene state lives here behind one re-entrant lock; heavy pure work (detection, CMC, markers) runs outside it.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .autonomy.controller import AutonomyController, AutonomyInputs
from .autonomy.safety import DroneSafetyState
from .clock import Clock
from .config import Config
from .decisions.router import DecisionRouter
from .autonomy.belief import MAX_SUBJECT_MPS, Hypothesis, StreetBelief, StreetGraph
from .autonomy.pursuit import coverage_candidates, describes
from .autonomy.controller import DispatchPlan
from .autonomy.coverage import footprint_width_m, lawnmower
from .autonomy.reflex import decision_needed
from .decisions.tasks import d1_reacquire, d2_identity, d3_describe, d4_motion, d5_priority, d6_obstacle, d12_pursuit_route
from .decisions.types import DecisionJob
from .evaluation.metrics import Metrics, RateMeter
from .events.arbiter import AlertArbiter
from .events.narration import summary as narr_summary
from .events.rules import RuleEngine, RuleInput
from .faults import FaultInjector
from .geometry.calibration import CalibrationManager
from .geometry.camera import Intrinsics, heading_from_vector, wrap_deg
from .geometry.geo import latlon_to_local, local_to_latlon, true_to_local_heading
from .geometry.kalman import CVFilter
from .geometry.kinematics import KinematicsTracker, clock_position
from .geometry.occupancy import OccupancyGrid
from .geometry.phone_view import is_rotated, upright_hfov_deg
from .geometry.registration import PhoneRegistration, arkit_horizontal
from .ids import IdGen
from .schemas import (DispatchStatus, Alert, CalibrationStatus, DecisionRecord, DroneState, DroneTelemetry, Event, FrameOverlay,
                      FrameOverlayTrack, Guardrail, IncidentState, Obstacle, PhoneObservation, PhoneSummary,
                      PursuitCandidate, PursuitHypothesis, PursuitRoute, PursuitState, RelativeKinematics,
                      ResponderState, SourceHealth, Track, TranscriptLine, WorldState, Zone)
from .sources.base import FramePacket, PhonePacket, TelemetryPacket
from .tracking.lock import Lock, LockEvent, TrackInfo, short
from .tracking.memory import ObjectMemory, TrackView
from .vision.appearance import color_hist, crop_tile, dominant_color_name, parse_description, similarity
from .vision.cmc import CameraMotionCompensator
from .vision.detectors import Detection, Detector, NullDetector, SimBlobDetector, build_detector
from .vision.tracker import ByteTracker, Track as RawTrack

D1_HOLD_MS = 3000.0  # how long a decided search manoeuvre is flown before D1 is asked again
def compass_of(deg: float | None) -> str:
    return "?" if deg is None else "N NE E SE S SW W NW".split()[int((deg + 22.5) // 45) % 8]


D12_HOLD_MS = 2000.0  # a pursuit route is flown this long before D12 re-plans from the moved belief
BELIEF_SPAWN_S = 1.0  # the street-graph belief starts once the subject has been unseen this long
BELIEF_TICK_MS = 200.0  # belief update rate (5 Hz)
BELIEF_MAX_S = 120.0
LOOKALIKE_SIM = 0.5  # appearance similarity to the subject's gallery for a person to become an identity hypothesis
LOOKALIKE_NEAR_M = 9.0  # the subject's cloud within this of where a new person appears counts as "predicted there"
# (a weaker claim than a re-bind, locks.rebind_similarity 0.58: "could be the subject", never "is the subject")

log = logging.getLogger(__name__)


@dataclass
class SourceCounters:
    received: int = 0
    processed: int = 0
    dropped: int = 0
    last_rx_ms: float | None = None
    last_capture_ms: float | None = None
    provenance: str | None = None
    rate: RateMeter = field(default_factory=RateMeter)
    detail: str | None = None
    reconnects: int = 0


@dataclass
class VideoFrameOut:
    image: np.ndarray
    overlay: FrameOverlay
    seq: int


def _canopy_fraction(img: np.ndarray, box: tuple[float, float, float, float]) -> float:
    H, W = img.shape[:2]
    x0, y0, x1, y1 = box
    w, h = max(8.0, x1 - x0), max(8.0, y1 - y0)
    rx0, ry0 = int(max(0, x0 - w)), int(max(0, y0 - h))
    rx1, ry1 = int(min(W, x1 + w)), int(min(H, y1 + h))
    if rx1 <= rx0 or ry1 <= ry0:
        return 0.0
    hsv = cv2.cvtColor(img[ry0:ry1, rx0:rx1], cv2.COLOR_BGR2HSV)
    canopy = (hsv[..., 0] >= 35) & (hsv[..., 0] <= 85) & (hsv[..., 2] < 105) & (hsv[..., 1] > 60)
    return float(canopy.mean())


class Pipeline:
    def __init__(self, cfg: Config, clock: Clock, mode: str, faults: FaultInjector, metrics: Metrics,
                 router: DecisionRouter, bridge: Any, session_id: str) -> None:
        self.cfg = cfg
        self.clock = clock
        self.mode = mode
        self.faults = faults
        self.metrics = metrics
        self.router = router
        self.bridge = bridge
        self.session_id = session_id
        self.lock = threading.RLock()
        self.calib_lock = threading.Lock()
        self.profile = cfg.session.mission_profile
        dc = cfg.sources.drone
        self.drone_intr = Intrinsics.from_hfov(dc.width, dc.height, dc.hfov_deg)
        self.calib = CalibrationManager(cfg, self.drone_intr)
        self.cmc = CameraMotionCompensator(cfg.perception.cmc.max_corners, cfg.perception.cmc.min_inliers)
        self.tracker = ByteTracker(cfg.perception.tracker, "drone")
        self.detector: Detector | None = None
        self._detector_prov: str | None = None
        self.registration = PhoneRegistration(cfg.phone.registration)
        fc = cfg.fusion
        self.occupancy = OccupancyGrid(fc.occupancy_origin_m, fc.occupancy_size_m, fc.occupancy_cell_m)
        self.locks = {"subject": Lock("subject", cfg.locks), "responder": Lock("responder", cfg.locks)}
        self.kin = KinematicsTracker(cfg.kinematics.closing_window_s, cfg.kinematics.min_samples)
        self.rules = RuleEngine(cfg.alerts, cfg.phone)
        self.arbiter = AlertArbiter(cfg.alerts, self.profile, cfg.decisions.deadline_ms)
        self.autonomy = AutonomyController(cfg.autonomy, bridge)
        from .voice.commands import VoiceCommands
        from .missions.crew_routing import CrewRoutingService
        self.voice = VoiceCommands(self)  # operator commands (spoken or typed) to mission primitives
        self.crew = CrewRoutingService(self)  # ground crews routed live through flood water and fire
        self.decisions: Any = None  # DecisionService, attached by the runtime
        self.map_provider: Any = None  # callable -> street graph (map data) or None, attached by the runtime
        self.recorder: Any = None  # replay.recorder.Recorder while recording
        self.decision_hook: Any = None  # runtime callback (job, record) after a decision is applied
        self.zones: list[Zone] = []
        self.ids_tx = IdGen("t")
        self._inject_ids = IdGen("x")
        self.incident = IncidentState()
        if mode in ("SIMULATED", "REPLAY"):
            # synthetic participants, or a replay of a session recorded under consent
            prefix = "INC-SIM-" if mode == "SIMULATED" else "REPLAY-"
            self.incident = IncidentState(incident_id=prefix + session_id[-4:].upper(), active=True,
                                          started_ts_ms=0.0, consent_confirmed=True)
        self._reset_state()

    def _reset_state(self) -> None:
        self.memory = ObjectMemory()  # persistent objects above the tracker (Permanence ideas, reimplemented)
        self.world_kf: dict[str, CVFilter] = {}
        self.trails: dict[str, deque] = {}
        self.track_hist: dict[str, np.ndarray] = {}
        self.track_colors: dict[str, tuple[str, str]] = {}
        self.track_last_box: dict[str, tuple[float, float, float, float]] = {}
        self.track_ground_err: dict[str, float] = {}
        self.raw_tracks: list[RawTrack] = []
        self.last_frame: np.ndarray | None = None
        self.last_frame_id = 0
        self.last_frame_size = (self.drone_intr.width, self.drone_intr.height)
        self.reference_crop: np.ndarray | None = None
        self.counters: dict[str, SourceCounters] = {k: SourceCounters() for k in ("drone", "phone", "telemetry")}
        self.telemetry: DroneTelemetry | None = None
        self.phone_obs: PhoneObservation | None = None
        self.ark_hist: deque[tuple[float, np.ndarray, float]] = deque(maxlen=200)
        self.obstacles: deque[Obstacle] = deque(maxlen=600)
        self.pending_lock_events: list[LockEvent] = []
        self.pending_safety: list[dict] = []
        self.outbox: deque[dict] = deque(maxlen=2000)
        self.events: deque[Event] = deque(maxlen=500)
        self.decision_log: deque[DecisionRecord] = deque(maxlen=300)
        self.transcript: deque[TranscriptLine] = deque(maxlen=200)
        self.selected_track_id: str | None = None
        self.flight_confirmed_track_id: str | None = None
        self.latest_drone: VideoFrameOut | None = None
        self.latest_phone: VideoFrameOut | None = None
        self.kin_state = RelativeKinematics()
        self.responder = ResponderState()
        self.motion_label: dict | None = None
        self.describe_proposal: dict | None = None
        self.narration: str | None = None
        self._last_summary_ms = -1e12
        self._last_d4_ms = -1e12
        self._last_tick_ms: float | None = None
        self._state_seq = 0
        self._video_seq = 0
        self.crowd = False
        self.sim_status: dict | None = None
        self.calib_status = CalibrationStatus()
        self._cross_view: list[dict] = []
        self._phone_hfov: float | None = None
        self._injected: list[Event] = []
        self.dispatch_state: DispatchStatus | None = None
        self._dispatch_votes: dict[str, int] = {}
        self._dispatch_rejected: set[str] = set()
        self._dispatch_seen: set[str] = set()
        self._dispatch_report_ms = -1e12
        self.detector_override: str | None = None  # a scenario's detector choice (simulator)
        self.last_alert_latency_ms: float | None = None
        self.subject_last_capture_ms: float | None = None
        self.look_for: dict | None = None  # the operator's "look for" request (clothing words), this session only
        # pursuit: the street-graph belief while the subject is lost, its candidate viewpoints and the route flown
        self.belief: StreetBelief | None = None
        self._belief_graph: tuple[int, StreetGraph] | None = None
        self._belief_last_ms = -1e12
        self._belief_seq = 0
        self._belief_expired_for: float | None = None  # the loss (last-seen time) whose belief ran out of time
        self._subj_snap: dict[str, Any] | None = None
        # clothing colour words voted over many frames (one frame's reading of trousers at 28 m is a coin flip):
        # for the designated subject while the lock is verified, and for every remembered person; belief only
        self._subj_votes: tuple[float | None, Counter, Counter] = (None, Counter(), Counter())
        self._obj_votes: dict[str, tuple[Counter, Counter]] = {}
        self.pursuit_candidates: tuple[float, list[dict]] | None = None
        self.pursuit_route: dict[str, Any] | None = None
        self.pursuit_last: dict[str, Any] | None = None

    def reset_session(self, session_id: str | None = None) -> None:
        """New session / scenario restart: new tracker epoch, no inherited IDs, locks or galleries (S19)."""
        with self.lock:
            if session_id:
                self.session_id = session_id
            self.tracker.reset()
            self.cmc.reset()
            with self.calib_lock:
                self.calib.reset()
            self.registration.reset()
            self.occupancy.reset()
            for lk in self.locks.values():
                lk.reset()
            self.kin.reset()
            self.rules.reset()
            self.arbiter.reset()
            self.autonomy.reset()
            self.autonomy.audit.clear()
            self.voice.reset()
            self.crew.reset()
            if self.decisions is not None and hasattr(self.decisions, "reset"):
                self.decisions.reset()  # rate limits are in session time, which restarts at zero
            self._reset_state()

    # ===========================================================================================================
    # Ingest
    # ===========================================================================================================
    def _ensure_detector(self, provenance: str) -> None:
        choice = self.detector_override or self.cfg.perception.detector
        if self.detector is None or self._detector_prov != (provenance, choice):
            self.detector = build_detector(choice, provenance, self.cfg)
            self._detector_prov = (provenance, choice)  # origin provenance of the frames the detector was chosen for
            log.info("detector: %s for %s frames", self.detector.model_id, provenance)

    def process_drone_frame(self, pkt: FramePacket) -> FrameOverlay:
        t0 = time.perf_counter()
        c = self.counters["drone"]
        c.received += 1
        img = pkt.image
        H, W = img.shape[:2]
        if (W, H) != (self.drone_intr.width, self.drone_intr.height):
            self.drone_intr = self.drone_intr.scaled(W, H)
            with self.calib_lock:
                self.calib = CalibrationManager(self.cfg, self.drone_intr)
        if self.recorder is not None:
            self.recorder.drone_frame(pkt)
        self._ensure_detector(str(pkt.meta.get("origin_provenance") or pkt.provenance))
        det = self.detector or NullDetector()
        if isinstance(det, SimBlobDetector):
            det.miss_rate = float(self.faults.get("detector_miss_pct") or 0.0)
        td = time.perf_counter()
        dets: list[Detection] = det.detect(img)
        detect_ms = (time.perf_counter() - td) * 1000.0
        self.metrics.observe("detect_ms", detect_ms)
        tc = time.perf_counter()
        cmc = self.cmc.estimate(img, [d.box for d in dets]) if self.cfg.perception.cmc.enabled else None
        self.metrics.observe("cmc_ms", (time.perf_counter() - tc) * 1000.0)
        with self.lock:
            tel = self.telemetry
            tel_fresh = tel is not None and self.counters["telemetry"].last_rx_ms is not None and \
                (pkt.capture_ts_ms - (self.counters["telemetry"].last_rx_ms or 0)) <= self.cfg.sources.telemetry.max_age_ms
        tk = time.perf_counter()
        with self.calib_lock:
            cal = self.calib.update(img, pkt.capture_ts_ms, cmc, tel, tel_fresh)
        self.metrics.observe("calibration_ms", (time.perf_counter() - tk) * 1000.0)
        with self.lock:
            now = pkt.capture_ts_ms
            self.calib_status = cal
            tt = time.perf_counter()
            out = self.tracker.update(dets, now, cmc.H if (cmc is not None and cmc.ok) else None)
            self.metrics.observe("track_ms", (time.perf_counter() - tt) * 1000.0)
            self.raw_tracks = out.tracks
            for gone in out.expired:
                for dct in (self.world_kf, self.trails, self.track_hist, self.track_colors, self.track_last_box,
                            self.track_ground_err):
                    dct.pop(gone.track_id, None)
            err0 = self.calib.error_m if self.calib.valid else None
            infos: dict[str, TrackInfo] = {}
            overlay_tracks: list[FrameOverlayTrack] = []
            roles = {lk.track_id: role for role, lk in self.locks.items() if lk.active and lk.track_id}
            for tr in out.tracks:
                tid = tr.track_id
                box = tr.measured_box if (tr.matched_now and tr.measured_box) else tr.box
                world_xy = None
                gerr = None
                if tr.matched_now:
                    self.track_last_box[tid] = box
                    h = color_hist(img, box) if tr.state != "TENTATIVE" or tr.hits >= 2 else None
                    if h is not None:
                        prev = self.track_hist.get(tid)
                        self.track_hist[tid] = h if prev is None else 0.8 * prev + 0.2 * h
                    if tid not in self.track_colors or tr.hits % 15 == 0:
                        self.track_colors[tid] = (dominant_color_name(img, box, "torso"), dominant_color_name(img, box, "lower"))
                    if tr.hits % 5 == 0:
                        self._vote_colors(tid, (dominant_color_name(img, box, "torso"), dominant_color_name(img, box, "lower")))
                    if err0 is not None:
                        bc = np.array([[0.5 * (box[0] + box[2]), box[3]]])
                        proj = self.calib.project(bc)
                        if proj is not None and proj.ok[0]:
                            gsd = self.calib.gsd_at(bc[0, 0], bc[0, 1]) or 0.05
                            gerr = float(math.sqrt(err0 ** 2 + self.cfg.fusion.ground_contact_sigma_m ** 2 + (3 * gsd) ** 2))
                            world_xy = (float(proj.xy[0, 0]), float(proj.xy[0, 1]))
                            kf = self.world_kf.get(tid)
                            if kf is None:
                                self.world_kf[tid] = CVFilter(world_xy, now, gerr)
                            else:
                                kf.update(world_xy, now, gerr)
                            self.track_ground_err[tid] = gerr
                            trail = self.trails.setdefault(tid, deque(maxlen=int(self.cfg.perception.tracker.trail_seconds * 16)))
                            trail.append((round(world_xy[0], 2), round(world_xy[1], 2), now))
                    if roles.get(tid) == "subject":
                        self.subject_last_capture_ms = now
                kf = self.world_kf.get(tid)
                infos[tid] = TrackInfo(tid, tr.state, tr.matched_now, box, tr.conf,
                                       world_xy if world_xy is not None else None,
                                       gerr, self.track_hist.get(tid), tr.hits, tr.reacquired,
                                       kf.velocity if (kf is not None and kf.updates >= 5) else None)
                vx, vy = tr.kf.velocity
                label = f"{short(tid)} {tr.class_name} {tr.conf:.2f}"
                role = roles.get(tid)
                overlay_tracks.append(FrameOverlayTrack(
                    track_id=tid, box=tuple(round(v, 1) for v in box), class_name=tr.class_name,  # type: ignore[arg-type]
                    confidence=round(tr.conf, 3), state=tr.state if tr.matched_now or tr.state == "LOST" else "OCCLUDED",
                    role=role, label=label, velocity_px=(round(vx * 0.5, 1), round(vy * 0.5, 1)),
                    ground_xy_m=(round(kf.position[0], 2), round(kf.position[1], 2)) if (kf and world_xy) else None))
            # locks (subject excludes the responder's track and vice versa)
            subj, resp = self.locks["subject"], self.locks["responder"]
            ev = subj.update(infos, {resp.track_id} if resp.track_id else set(), now)
            ev += resp.update(infos, {subj.track_id} if subj.track_id else set(), now)
            self.pending_lock_events.extend(ev)
            if subj.active and subj.state == "LOCKED" and subj.track_id in infos and infos[subj.track_id].matched_now:
                b = infos[subj.track_id].box
                if (b[3] - b[1]) > 12 and subj.verified:
                    x0, y0, x1, y1 = (int(max(0, b[0] - 4)), int(max(0, b[1] - 4)), int(min(W, b[2] + 4)), int(min(H, b[3] + 4)))
                    if x1 > x0 and y1 > y0:
                        self.reference_crop = img[y0:y1, x0:x1].copy()
            self._update_memory(now, out.tracks, img, W, H)
            if self.dispatch_state is not None:
                self._dispatch_match(now, infos)
            # registration pairs: drone-observed responder vs ARKit trajectory at the same time
            if resp.active and resp.state == "LOCKED" and resp.track_id in infos:
                ri = infos[resp.track_id]
                if ri.matched_now and ri.world_xy is not None and self.ark_hist:
                    tp, ark_xy, _ = min(self.ark_hist, key=lambda a: abs(a[0] - now))
                    if abs(tp - now) <= 120.0:
                        self.registration.add_pair(now, ark_xy, ri.world_xy, ri.world_err or 0.5)
            self.last_frame = img
            self.last_frame_id = pkt.frame_id
            self.last_frame_size = (W, H)
            # overlay for this exact frame
            markers = [m for m in cal.markers] if (self.calib.frame_count % max(1, self.cfg.calibration.markers.detect_every_n_frames) == 1) else []
            zones_px = []
            if cal.valid:
                for z in self.zones:
                    pts = self.calib.ground_to_image(np.array(z.polygon_xy_m))
                    if pts is not None and np.isfinite(pts).all() and np.abs(pts).max() < 1e5:
                        zones_px.append({"zone_id": z.zone_id, "name": z.name, "kind": z.kind,
                                         "polygon": [(round(float(x), 1), round(float(y), 1)) for x, y in pts]})
            self._video_seq += 1
            overlay = FrameOverlay(
                source_id=pkt.source_id, frame_id=pkt.frame_id, capture_ts_ms=pkt.capture_ts_ms,
                server_ts_ms=self.clock.now_ms(), width=W, height=H, provenance=pkt.provenance, tracks=overlay_tracks,
                detections=len(dets), markers=markers, zones_px=zones_px,
                cmc={"ok": cmc.ok, "inliers": cmc.inliers, "shift_px": round(cmc.shift_px, 2)} if cmc else None,
                detector_ms=round(detect_ms, 2), model_id=det.model_id, calibration_valid=cal.valid,
                extra={"calibration_source": cal.source, "gimbal_pitch_deg": tel.gimbal_pitch_deg if tel else None,
                       "selected": self.selected_track_id,
                       "memory": [m for m in self._memory_view(now) if m.get("ghost_box") and m["state"] != "VISIBLE"]})
            self.latest_drone = VideoFrameOut(img, overlay, self._video_seq)
            c.processed += 1
            c.last_rx_ms = pkt.receive_ts_ms
            c.last_capture_ms = pkt.capture_ts_ms
            c.provenance = pkt.provenance
            c.rate.tick(pkt.receive_ts_ms)
            c.detail = det.model_id
        self.metrics.observe("frame_total_ms", (time.perf_counter() - t0) * 1000.0)
        self.metrics.inc("drone_frames_processed")
        return overlay

    def note_dropped(self, source: str, n: int = 1) -> None:
        self.counters[source].dropped += n
        self.metrics.inc(f"{source}_frames_dropped", n)

    def process_phone(self, pkt: PhonePacket) -> None:
        obs = pkt.observation
        if self.recorder is not None:
            self.recorder.phone(pkt)
        with self.lock:
            c = self.counters["phone"]
            c.received += 1
            c.processed += 1
            c.last_rx_ms = pkt.receive_ts_ms
            c.last_capture_ms = obs.capture_ts_ms
            c.provenance = pkt.provenance
            c.rate.tick(pkt.receive_ts_ms)
            self.phone_obs = obs
            res = obs.image_resolution or (0, 0)
            uw, uh = (res[1], res[0]) if is_rotated(obs.image_orientation) else res
            self._phone_hfov = upright_hfov_deg(obs.camera_intrinsics_3x3, obs.image_orientation, uw, uh,
                                                self.cfg.sources.phone.hfov_deg)
            if obs.camera_pose_4x4 is not None and obs.arkit_tracking_state == "normal":
                ark_xy, ark_head = arkit_horizontal(obs.camera_pose_4x4)
                self.ark_hist.append((obs.capture_ts_ms, ark_xy, ark_head))
                if self.registration.valid and obs.obstacle_points:
                    P = np.array(obs.obstacle_points, float)
                    pts = self.registration.to_local(np.column_stack([P[:, 0], -P[:, 2]]))
                    sensor = self.registration.to_local(ark_xy[None, :])
                    if pts is not None and sensor is not None:
                        self.occupancy.integrate((float(sensor[0, 0]), float(sensor[0, 1])), pts)
                        for x, y in pts[:: max(1, len(pts) // 60)]:
                            self.obstacles.append(Obstacle(xy_m=(round(float(x), 2), round(float(y), 2)), source="lidar",
                                                           confidence=0.7, ts_ms=obs.capture_ts_ms))
            if pkt.rgb is not None:
                self._video_seq += 1
                ov = self._phone_overlay(obs, pkt)
                self.latest_phone = VideoFrameOut(pkt.rgb, ov, self._video_seq)
        self.metrics.inc("phone_observations")

    def _phone_overlay(self, obs: PhoneObservation, pkt: PhonePacket) -> FrameOverlay:
        H, W = pkt.rgb.shape[:2]  # type: ignore[union-attr]
        tracks = []
        self._cross_view = []
        boxes = obs.vision_boxes or []
        ranges = obs.vision_ranges_m or [None] * len(boxes)
        # cross-view association by bearing: phone box azimuth vs. responder->track bearing (needs registration).
        # The uplinked frame is upright, so a portrait phone has the sensor's short-side FOV horizontally (S13).
        hfov = upright_hfov_deg(obs.camera_intrinsics_3x3, obs.image_orientation, W, H, self.cfg.sources.phone.hfov_deg)
        self._phone_hfov = hfov
        fx = (W / 2) / math.tan(math.radians(hfov / 2))
        resp = self.responder
        geo = []  # per box: pixel box, bearing, edge clip, ranked candidate drone tracks
        for b, rng in zip(boxes, ranges):
            x0, y0, x1, y1 = b[0] * W, b[1] * H, b[2] * W, b[3] * H
            az = math.degrees(math.atan(((x0 + x1) / 2 - W / 2) / fx))
            # a box cut by the left/right image edge only bounds the bearing: the person is further toward that edge
            clip = 1 if x1 >= W - 1 else (-1 if x0 <= 1 else 0)
            cands: list[tuple[float, str]] = []
            # Bearings need the responder in the same map frame as the drone tracks (drone lock or ARKit registration:
            # shared calibration error mostly cancels in the relative bearing). A GPS fix (±5 m, independent error)
            # makes bearings at these ranges meaningless, so no claim is made.
            same_frame = resp.position_source in ("drone_lock", "arkit_registered")
            if same_frame and resp.position_xy_m and resp.heading_deg is not None:
                for tid, kf in self.world_kf.items():
                    if tid == self.locks["responder"].track_id or obs.capture_ts_ms - kf.last_update_ms > 400.0:
                        continue  # only drone tracks observed right now can be the same person
                    px, py = kf.position
                    dx, dy = px - resp.position_xy_m[0], py - resp.position_xy_m[1]
                    d = math.hypot(dx, dy)
                    if d < 0.5 or d > 40:
                        continue
                    rel = wrap_deg(heading_from_vector(dx, dy) - resp.heading_deg)
                    off = (rel - az) * clip if clip else abs(rel - az)  # clipped: only the wrong side is penalised
                    if clip and off > 0:
                        off = 0.0 if off < 30.0 else off
                    off = abs(off)
                    if off < 7.0:
                        cands.append((off + (abs(d - rng) * 4 if rng else 0), tid))
            cands.sort()
            geo.append(((x0, y0, x1, y1), rng, az, clip, cands))
        # Never claim a match when unsure: a runner-up within 3 points makes the box ambiguous, and a drone track
        # claimed by two boxes goes to the better-scoring one only.
        claim: dict[str, tuple[float, int]] = {}
        picks: list[str | None] = []
        for i, (_, _, _, _, cands) in enumerate(geo):
            pick = cands[0][1] if cands and (len(cands) == 1 or cands[1][0] - cands[0][0] >= 3.0) else None
            if pick is not None and (pick not in claim or cands[0][0] < claim[pick][0]):
                claim[pick] = (cands[0][0], i)
            picks.append(pick)
        for i, ((x0, y0, x1, y1), rng, az, clip, cands) in enumerate(geo):
            match = picks[i] if picks[i] is not None and claim[picks[i]][1] == i else None
            role = None
            for r, lk in self.locks.items():
                if lk.track_id and lk.track_id == match:
                    role = r
            label = f"{'= ' + short(match) if match else 'person'}{f' {rng:.1f} m' if rng else ''}"
            tracks.append(FrameOverlayTrack(track_id=match or f"phone-{i}", box=(x0, y0, x1, y1), class_name="person",
                                            confidence=1.0, state="CONFIRMED", role=role, label=label))
            self._cross_view.append({"phone_box": i, "drone_track": match, "range_m": rng, "azimuth_deg": round(az, 2),
                                     "clipped": bool(clip), "ambiguous": bool(cands) and match is None})
        return FrameOverlay(source_id=obs.source_id, frame_id=obs.seq, capture_ts_ms=obs.capture_ts_ms,
                            server_ts_ms=self.clock.now_ms(), width=W, height=H, provenance=pkt.provenance,
                            tracks=tracks, detections=len(boxes), model_id="on-device vision",
                            calibration_valid=self.registration.valid,
                            extra={"sectors": [s.model_dump() for s in obs.sectors], "tracking": obs.arkit_tracking_state,
                                   "local_alert": obs.local_alert_active, "lidar": obs.lidar_supported,
                                   "depth_valid_fraction": obs.depth_valid_fraction})

    def inject_scripted_events(self, conflict_id: str, specs: list[dict], now_ms: float) -> int:
        """Scripted alert-priority conflict (scenario S16): informational events that reach the arbiter together on
        the next tick, labelled ``source="scripted"``. Simulation (and its replays) only; recorded like an operator
        command so a replay re-injects it at the same moment."""
        if self.mode not in ("SIMULATED", "REPLAY"):
            return 0
        self._log_operator("inject_conflict", conflict_id=conflict_id, specs=specs)
        with self.lock:
            for s in specs:
                self._injected.append(Event(
                    event_id=self._inject_ids(), kind=s["kind"], severity="info",
                    ts_ms=now_ms - 1000.0 * float(s.get("observed_s_ago") or 0.0), text=s["text"],
                    track_id=f"scripted:{conflict_id}", role="subject", source="scripted", ttl_ms=4000.0,
                    data={"range_m": s.get("range_m"), "closing_mps": s.get("closing_mps"),
                          "scripted_conflict": conflict_id}))
        return len(specs)

    def process_telemetry(self, pkt: TelemetryPacket) -> None:
        if self.recorder is not None:
            self.recorder.telemetry(pkt)
        with self.lock:
            c = self.counters["telemetry"]
            c.received += 1
            c.processed += 1
            c.last_rx_ms = pkt.receive_ts_ms
            c.last_capture_ms = pkt.telemetry.capture_ts_ms
            c.provenance = pkt.provenance
            c.rate.tick(pkt.receive_ts_ms)
            self.telemetry = pkt.telemetry

    # ===========================================================================================================
    # Derived state
    # ===========================================================================================================
    def _age(self, src: str, now: float) -> float | None:
        c = self.counters[src]
        return None if c.last_rx_ms is None else max(0.0, now - c.last_rx_ms)

    def _source_status(self, now: float) -> dict[str, str]:
        out = {}
        lim = {"drone": self.cfg.sources.drone.max_age_ms, "phone": self.cfg.sources.phone.max_age_ms * 3,
               "telemetry": self.cfg.sources.telemetry.max_age_ms}
        for src, c in self.counters.items():
            age = self._age(src, now)
            if age is None:
                out[src] = "NOT_CONFIGURED" if c.received == 0 else "OFFLINE"
            elif age > lim[src] * 4:
                out[src] = "OFFLINE"
            elif age > lim[src]:
                out[src] = "STALE"
            else:
                out[src] = "OK"
            if src == "phone" and out[src] == "OK" and self.phone_obs is not None:
                if self.phone_obs.arkit_tracking_state != "normal" or not self.phone_obs.lidar_supported:
                    out[src] = "DEGRADED"
        return out

    def drone_state(self, now: float) -> DroneState | None:
        tel = self.telemetry
        if tel is None:
            return None
        geo = self.cfg.world.geo_reference
        xy = None
        if tel.lat is not None and tel.lon is not None and geo is not None:
            x, y = latlon_to_local(tel.lat, tel.lon, geo, self.cfg.world.north_offset_deg)
            b = self.calib.tel_bias if self.calib.tel_bias_ms is not None else np.zeros(2)
            xy = (round(x + float(b[0]), 2), round(y + float(b[1]), 2))
        vel = None
        if tel.vel_ned:
            vel = (round(tel.vel_ned[1], 2), round(tel.vel_ned[0], 2))
        heading = true_to_local_heading(tel.yaw_deg, self.cfg.world.north_offset_deg) if tel.yaw_deg is not None else None
        fp = self.calib.footprint()
        return DroneState(position_xy_m=xy, alt_m=tel.alt_rel_m, yaw_deg=round(heading, 1) if heading is not None else None,
                          gimbal_pitch_deg=tel.gimbal_pitch_deg, battery_pct=tel.battery_pct, velocity_xy_mps=vel,
                          lat=tel.lat, lon=tel.lon, flying=tel.flying, vstick_enabled=tel.vstick_enabled, sim=tel.sim,
                          footprint_xy_m=[(round(x, 2), round(y, 2)) for x, y in fp] if fp else None,
                          telemetry_age_ms=self._age("telemetry", now))

    def _responder_state(self, now: float) -> ResponderState:
        lk = self.locks["responder"]
        cal_ok = self.calib.valid
        pos = None
        src = "none"
        err = None
        tid = None
        if lk.active and lk.kf is not None and cal_ok and lk.last_seen_ms is not None and now - lk.last_seen_ms < 1500:
            pos = lk.kf.position
            src, err, tid = "drone_lock", max(lk.kf.pos_sigma, lk.kf_err), lk.track_id
        obs = self.phone_obs
        phone_fresh = obs is not None and self._age("phone", now) is not None and self._age("phone", now) < self.cfg.sources.phone.max_age_ms * 3
        ark = self.ark_hist[-1] if self.ark_hist else None
        if pos is None and self.registration.valid and ark is not None and phone_fresh and obs.arkit_tracking_state == "normal":
            loc = self.registration.to_local(ark[1][None, :])
            if loc is not None:
                pos = (float(loc[0, 0]), float(loc[0, 1]))
                src, err = "arkit_registered", self.registration.error_m
        if pos is None and phone_fresh and obs.gps is not None and self.cfg.world.geo_reference is not None:
            pos = latlon_to_local(obs.gps.lat, obs.gps.lon, self.cfg.world.geo_reference, self.cfg.world.north_offset_deg)
            src, err = "gps", obs.gps.accuracy_m or 5.0
        heading = None
        hsrc = "none"
        if phone_fresh and obs is not None:
            if obs.heading_deg is not None and obs.heading_accuracy_deg is not None and 0 <= obs.heading_accuracy_deg <= 25:
                heading = true_to_local_heading(obs.heading_deg, self.cfg.world.north_offset_deg)
                hsrc = "compass"
                if obs.head_yaw_deg is not None:
                    heading = (heading + obs.head_yaw_deg) % 360.0
                    hsrc = "head_yaw"
            elif self.registration.valid and ark is not None:
                heading = self.registration.heading_to_local(ark[2])
                hsrc = "arkit"
        if heading is None and lk.kf is not None and lk.kf.speed > 0.6:
            heading = heading_from_vector(*lk.kf.velocity)
            hsrc = "motion"
        lat = lon = None
        if pos is not None and self.cfg.world.geo_reference is not None:
            lat, lon = local_to_latlon(pos[0], pos[1], self.cfg.world.geo_reference, self.cfg.world.north_offset_deg)
        return ResponderState(position_xy_m=(round(pos[0], 2), round(pos[1], 2)) if pos else None, position_source=src,  # type: ignore[arg-type]
                              position_error_m=round(err, 2) if err is not None else None,
                              heading_deg=round(heading, 1) if heading is not None else None, heading_source=hsrc,  # type: ignore[arg-type]
                              heading_valid=heading is not None, track_id=tid, lat=lat, lon=lon)

    def _phone_summary(self, now: float, status: str) -> PhoneSummary:
        obs = self.phone_obs
        if obs is None:
            return PhoneSummary(connected=False)
        nearest, sector = None, None
        for s in obs.sectors:
            if s.nearest_m is not None and s.confidence in ("high", "medium") and (nearest is None or s.nearest_m < nearest):
                nearest, sector = s.nearest_m, s.sector
        frustum = None
        if self.registration.valid and self.responder.position_xy_m and self.responder.heading_deg is not None:
            px, py = self.responder.position_xy_m
            hf = (self._phone_hfov or self.cfg.sources.phone.hfov_deg) / 2
            rng = self.cfg.phone.max_range_m
            pts = [(px, py)]
            for a in np.linspace(-hf, hf, 7):
                h = math.radians(self.responder.heading_deg + a)
                pts.append((round(px + rng * math.sin(h), 2), round(py + rng * math.cos(h), 2)))
            frustum = pts
        return PhoneSummary(connected=status in ("OK", "DEGRADED", "STALE"), provenance=self.counters["phone"].provenance,  # type: ignore[arg-type]
                            tracking_state=obs.arkit_tracking_state, lidar_supported=obs.lidar_supported,
                            depth_valid_fraction=obs.depth_valid_fraction, sectors=obs.sectors, nearest_m=nearest,
                            nearest_sector=sector, local_alert_active=obs.local_alert_active,
                            heading_deg=obs.heading_deg, heading_accuracy_deg=obs.heading_accuracy_deg,
                            age_ms=self._age("phone", now), frustum_xy_m=frustum)

    def _drone_current(self) -> bool:
        age = self._age("drone", self.clock.now_ms())
        return age is not None and age <= self.cfg.sources.drone.max_age_ms * 4

    def _calibration_now(self) -> CalibrationStatus:
        """The last calibration is only a current claim while drone frames keep arriving."""
        cs = self.calib_status
        if cs.valid and not self._drone_current():
            return cs.model_copy(update={"valid": False, "reason": f"no current drone frames (last: {cs.source})"})
        return cs

    def _map_mode(self) -> str:
        if not self._drone_current():
            return "PHONE_ONLY" if self.phone_obs is not None else "UNCALIBRATED"
        if self.calib.valid and self.registration.valid:
            return "REGISTERED_METRIC"
        if self.calib.valid:
            return "DRONE_LOCAL"
        if self.phone_obs is not None:
            return "PHONE_ONLY"
        return "UNCALIBRATED"

    # ===========================================================================================================
    # Tick (10 Hz)
    # ===========================================================================================================
    def _ground_uv(self, xy: tuple[float, float]) -> tuple[float, float] | None:
        if not self.calib.valid:
            return None
        pts = self.calib.ground_to_image(np.array([xy], float))
        if pts is None or not np.isfinite(pts).all():
            return None
        return float(pts[0][0]), float(pts[0][1])

    def _update_memory(self, now: float, tracks, img: np.ndarray, W: int, H: int) -> None:
        """Object memory: persistent people above the tracker, explained when unseen (out of view, canopy)."""
        views = []
        for tr in tracks:
            if tr.class_name != "person":
                continue
            kf = self.world_kf.get(tr.track_id)
            fresh_xy = tr.matched_now and self.calib.valid and kf is not None and tr.track_id in self.track_ground_err
            box = tr.measured_box if (tr.matched_now and tr.measured_box) else tr.box
            views.append(TrackView(tr.track_id, tr.class_name, tr.matched_now, tr.state == "CONFIRMED",
                                   tuple(kf.position) if fresh_xy else None,  # type: ignore[arg-type]
                                   self.track_ground_err.get(tr.track_id), box, self.track_hist.get(tr.track_id),
                                   self.track_colors.get(tr.track_id)))
        if not self.calib.valid:
            self.memory.update(now, views, None, None)
            return

        def in_view(xy: tuple[float, float]) -> bool:
            uv = self._ground_uv(xy)
            return uv is not None and 8 <= uv[0] < W - 8 and 8 <= uv[1] < H - 8

        def covered(obj) -> bool:
            uv = self._ground_uv((float(obj.xy[0]), float(obj.xy[1])))
            if uv is None or obj.box is None:
                return False
            w, h = obj.box[2] - obj.box[0], obj.box[3] - obj.box[1]
            return _canopy_fraction(img, (uv[0] - w, uv[1] - 1.5 * h, uv[0] + w, uv[1] + 0.3 * h)) > 0.3

        seq0 = self.memory.seq
        self.memory.update(now, views, in_view, covered)
        for e in self.memory.events:
            if e["seq"] > seq0:
                self.outbox.append({"type": "memory", **e})

    def _memory_view(self, now: float) -> list[dict]:
        return self.memory.snapshot(now, self._ground_uv)

    def tick(self, now: float) -> None:
        t0 = time.perf_counter()
        with self.lock:
            dt = 0.1 if self._last_tick_ms is None else max(0.0, (now - self._last_tick_ms) / 1000.0)
            self._last_tick_ms = now
            self.occupancy.decay(dt)
            self.registration.solve(now)
            status = self._source_status(now)
            self.responder = self._responder_state(now)
            subj = self.locks["subject"]
            calib_valid = self.calib.valid
            subj_fresh = subj.state == "LOCKED" and subj.last_seen_ms is not None and now - subj.last_seen_ms < 700
            settling = subj.state == "LOCKED" and not subj.settled(now)
            common = self.responder.position_source == "drone_lock"
            kin = self.kin.compute(now, subj.kf if subj.active else None, subj.kf_err if subj.kf else None,
                                   self.responder.position_xy_m, self.responder.position_error_m,
                                   self.responder.heading_deg, subj_fresh, common, calib_valid)
            if status.get("drone") in ("OFFLINE", "STALE") and kin.valid:
                kin = RelativeKinematics(valid=False, reason="drone video stale: no current position claim")
            elif settling and kin.valid:
                kin = RelativeKinematics(valid=False, reason="subject track settling after (re)bind: velocity not yet valid",
                                         range_m=kin.range_m, range_error_m=kin.range_error_m)
            self.kin_state = kin
            phone = self._phone_summary(now, status.get("phone", "NOT_CONFIGURED"))
            confirmed_people = [t for t in self.raw_tracks if t.state == "CONFIRMED" and t.class_name == "person"]
            self.crowd = len(confirmed_people) > self.cfg.privacy.crowd_threshold
            roles = {lk.track_id: role for role, lk in self.locks.items() if lk.active and lk.track_id}
            tracks_world = []
            for t in self.raw_tracks:
                kf = self.world_kf.get(t.track_id)
                if t.state == "CONFIRMED" and t.matched_now and kf is not None:
                    tracks_world.append((t.track_id, kf.position, roles.get(t.track_id)))
            evidence = [f"drone-frame:{self.last_frame_id}"]
            if self.calib.calibration_id:
                evidence.append(self.calib.calibration_id)
            inp = RuleInput(now, self.profile, kin, subj.state, subj.track_id, self.pending_lock_events, tracks_world,
                            self.zones, phone, {k: v for k, v in status.items() if v != "NOT_CONFIGURED"},
                            self.calib_status, self.router.ai_paused, self.router.pause_reason,
                            self.pending_safety, evidence, self.crowd)
            self.pending_lock_events = []
            self.pending_safety = []
            events = self.rules.evaluate(inp) + self._injected
            self._injected = []
            for e in events:
                self.events.append(e)
                self.outbox.append(e.model_dump())
                self.metrics.inc(f"event_{e.kind}")
            delivered, pending = self.arbiter.ingest(events, kin, now)
            delivered += self.arbiter.flush_expired(now)
            if self.arbiter.pending and not self.arbiter.d5_in_flight and self.decisions is not None:
                facts = {"pending": [{"event_id": p.event.event_id, "kind": p.event.kind, "text": p.event.text,
                                      "age_ms": round(now - p.created_ms), "severity": p.event.severity,
                                      "observed_s_ago": round((now - p.event.ts_ms) / 1000.0, 1),
                                      "range_m": p.event.data.get("range_m"), "closing_mps": p.event.data.get("closing_mps")}
                                     for p in self.arbiter.pending],
                         "calibration_valid": calib_valid,
                         "recent_alerts": [{"kind": a.kind, "age_s": round((now - a.ts_ms) / 1000.0, 1)}
                                           for a in list(self.arbiter.history)[-6:]],
                         "responder_busy_with_obstacle": phone.local_alert_active}
                job = d5_priority(facts)
                if self.decisions.submit(job):
                    self.arbiter.d5_in_flight = True
            self.arbiter.tick(now)
            for a in delivered:
                self._emit_alert(a, now)
            self._belief_tick(now)
            self.crew.tick(now)
            self._decision_jobs(now, kin)
            self._dispatch_report(now)
            if now - self._last_summary_ms >= self.cfg.voice.summary_interval_s * 1000.0:
                self._last_summary_ms = now
                d = self.drone_state(now)
                dn = (f"Drone {self.autonomy.mode.lower()} at {d.alt_m:.0f} m" if d and d.alt_m is not None
                      else "Drone telemetry unavailable")
                cs = self.calib_status
                cn = f"{cs.source} ±{cs.error_bound_m} m" if cs.valid else "invalid"
                self.narration = narr_summary(self.profile, kin, subj.state, dn, cn)
            if hasattr(self.decisions, "poll"):
                self.decisions.poll()
        self.metrics.observe("tick_ms", (time.perf_counter() - t0) * 1000.0)

    def _emit_alert(self, a: Alert, now: float) -> None:
        if a.kind in ("CLOSING_FAST", "APPROACH", "POSITION_CALLOUT") and self.subject_last_capture_ms is not None:
            lat = now - self.subject_last_capture_ms
            self.last_alert_latency_ms = lat
            self.metrics.observe(f"alert_latency_ms_{a.kind}", lat)
        self.metrics.inc(f"alert_{a.kind}")
        self.outbox.append(a.model_dump())
        if a.spoken_text and "phone_speech" in a.delivery:
            line = TranscriptLine(ts_ms=now, text=a.spoken_text, source=a.message_source, alert_id=a.alert_id)
            self.transcript.append(line)
            self.outbox.append(line.model_dump())

    def _decision_jobs(self, now: float, kin: RelativeKinematics) -> None:
        if self.decisions is None:
            return
        subj = self.locks["subject"]
        # D2: identity confirmation when the lock is uncertain
        if subj.state == "UNCERTAIN" and subj.needs_confirm and subj.candidates:
            labels = [chr(ord("A") + i) for i in range(len(subj.candidates))]
            cands = []
            boxes = []
            for lab, c in zip(labels, subj.candidates):
                col = self.track_colors.get(c.track_id, ("unknown", "unknown"))
                cands.append({"label": lab, "track": short(c.track_id), "appearance_similarity": c.similarity,
                              "distance_from_prediction_m": c.distance_m, "torso_color": col[0], "lower_color": col[1]})
                if c.track_id in self.track_last_box:
                    boxes.append(self.track_last_box[c.track_id])
            facts = {"reference_torso_color": self._subject_color(), "candidates": cands,
                     "time_since_loss_s": round((subj.snapshot(now).lost_ms or 0) / 1000.0, 1)}
            job = d2_identity(facts, labels)
            job.context = {"labels": dict(zip(labels, [c.track_id for c in subj.candidates]))}
            if self.cfg.decisions.allow_images and self.last_frame is not None and boxes:
                tile, _ = crop_tile(self.last_frame, self.reference_crop, boxes)
                ok, buf = cv2.imencode(".jpg", tile, [cv2.IMWRITE_JPEG_QUALITY, 85])
                job.image_jpeg = buf.tobytes() if ok else None
            self.decisions.submit(job)
        # D1: reacquire strategy while following and lost
        # A decided manoeuvre is flown for D1_HOLD_MS; the next decision is requested shortly before it ends.
        a = self.autonomy
        flying_decision = a.primitive_source.startswith(("decision", "rules (model")) and now < a.primitive_until_ms - 400.0
        streets = self.map_provider() if self.map_provider else None
        # the follow-mode tasks go to the rules unless the configuration asks for the model: pursuit does not
        # depend on an API (decisions.pursuit_engine)
        pursuit_engine = {"engine": "rules"} if self.cfg.decisions.pursuit_engine == "rules" else {}
        d12 = None
        if a.mode == "FOLLOW" and a.fsm == "REACQUIRE" and streets and not flying_decision:
            d12 = self._d12_job(now, streets)  # pursuit routing from the street-graph belief (map data)
            if d12 is not None:
                d12.context.update(pursuit_engine)
                d12.context["control_epoch"] = a.control_epoch
                self.decisions.submit(d12)
        if d12 is None and a.mode == "FOLLOW" and a.fsm in ("COAST", "REACQUIRE") and not flying_decision \
                and not (streets and self.belief is not None):
            facts = a.d1_facts(self._autonomy_inputs(now))
            if facts:
                job = d1_reacquire(facts)
                job.context.update(pursuit_engine)
                job.context["control_epoch"] = a.control_epoch
                self.decisions.submit(job)
        # D6: an obstacle in the low-altitude stack's path (the reflex keeps the veto whatever is answered). Asked
        # of Jev (TypeSafe) when its key is present, as in the jev-drone project; otherwise the configured engine.
        if a.scene and a.mode in ("FOLLOW", "DISPATCH") and decision_needed(a.scene) \
                and not (a.maneuver is not None and now < a.maneuver[1] - 300.0):
            sc = a.scene
            d = self.drone_state(now)
            closing = 0.0
            if d is not None and d.velocity_xy_mps and d.yaw_deg is not None:
                h = math.radians(d.yaw_deg)
                closing = d.velocity_xy_mps[0] * math.sin(h) + d.velocity_xy_mps[1] * math.cos(h)
            facts = {"obstacle_range_m": sc["path_ahead_m"], "closing_mps": round(closing, 2),
                     "clearance_left_m": sc["clearance_left_m"], "clearance_right_m": sc["clearance_right_m"],
                     "altitude_m": sc["altitude_m"], "ceiling_m": self.cfg.autonomy.max_altitude_m,
                     "obstacle_top_m": sc.get("obstacle_top_m"), "free_ahead_above_m": sc["free_ahead_above_m"],
                     "sector_range_m": sc["sector_range_m"], "scene_source": sc["source"]}
            job = d6_obstacle(facts)
            if self.router.engines.get("typesafe") is not None and self.router.engines["typesafe"].available()[0]:
                job.context["engine"] = "typesafe"
            self.decisions.submit(job)
        # D4: advisory motion label (observable motion only)
        if kin.valid and subj.state == "LOCKED" and now - self._last_d4_ms > 1500:
            self._last_d4_ms = now
            rel = None
            if kin.subject_heading_deg is not None and kin.bearing_abs_deg is not None:
                rel = round(wrap_deg(kin.subject_heading_deg - (kin.bearing_abs_deg + 180.0)), 0)
            job = d4_motion({"speed_mps": kin.subject_speed_mps, "closing_mps": kin.closing_mps,
                             "accel_mps2": kin.subject_accel_mps2, "range_m": kin.range_m,
                             "heading_relative_to_responder_deg": rel})
            job.context.update(pursuit_engine)
            self.decisions.submit(job)

    # ===========================================================================================================
    # Pursuit: the street-graph belief while the subject is lost (autonomy/belief.py), routed by coverage
    # ===========================================================================================================
    def _belief_provenance(self) -> str:
        from .incident.models import PROVENANCE_FROM_WIRE

        return PROVENANCE_FROM_WIRE.get(self.counters["drone"].provenance or "", "simulated")

    def _graph(self, streets: dict) -> StreetGraph:
        if self._belief_graph is None or self._belief_graph[0] != id(streets):
            self._belief_graph = (id(streets), StreetGraph(streets))
        return self._belief_graph[1]

    def _snapshot_subject(self, now: float) -> None:
        """While the subject is locked and verified, keep what the belief would start from: the last observed
        position and velocities (from the track's trail), its remembered object and its clothing."""
        subj = self.locks["subject"]
        if not (subj.active and subj.state == "LOCKED" and subj.verified and subj.track_id):
            return
        last, vel, prior = self._subject_motion_before_loss()
        if last is None:
            return
        obj = self.memory.object_for_track(subj.track_id)
        hist = np.mean(np.array(subj.gallery), axis=0) if subj.gallery else self.track_hist.get(subj.track_id)
        self._subj_snap = {"track_id": subj.track_id, "oid": obj.oid if obj else None, "xy": last, "vel": vel,
                           "prior": prior, "t_ms": subj.last_seen_ms or now, "err": float(subj.kf_err or 1.0),
                           "hist": hist, "colors": self._subject_colors() or self.track_colors.get(subj.track_id)}

    def _vote_colors(self, tid: str, cols: tuple[str, str]) -> None:
        subj = self.locks["subject"]
        if subj.active and subj.state == "LOCKED" and subj.verified and subj.track_id == tid:
            key, torso, lower = self._subj_votes
            if key != subj.since_ms:  # a new designation starts a new vote
                torso, lower = Counter(), Counter()
                self._subj_votes = (subj.since_ms, torso, lower)
            torso[cols[0]] += 1
            lower[cols[1]] += 1
        o = self.memory.object_for_track(tid)
        if o is not None:
            torso, lower = self._obj_votes.setdefault(o.oid, (Counter(), Counter()))
            torso[cols[0]] += 1
            lower[cols[1]] += 1

    @staticmethod
    def _voted(torso: Counter, lower: Counter) -> tuple[str, str] | None:
        if not torso and not lower:
            return None
        pick = lambda c: next((k for k, _ in c.most_common() if k != "unknown"), "unknown")  # noqa: E731
        return pick(torso), pick(lower)

    def _subject_colors(self) -> tuple[str, str] | None:
        key, torso, lower = self._subj_votes
        return self._voted(torso, lower) if key == self.locks["subject"].since_ms else None

    def _belief_tick(self, now: float) -> None:
        subj = self.locks["subject"]
        self._snapshot_subject(now)
        lost = subj.active and subj.state in ("COAST", "REACQUIRE", "UNCERTAIN", "HOLD")
        rebound = subj.active and subj.state == "LOCKED" and not subj.verified
        b = self.belief
        why_end = None
        if self.autonomy.mode != "FOLLOW":
            why_end = "autonomy left FOLLOW"
        elif not subj.active:
            why_end = "subject released"
        elif not (lost or rebound):
            why_end = "subject reacquired and confirmed" if subj.verified else "subject reacquired"
        elif b is not None and (now - b.started_ms) / 1000.0 > BELIEF_MAX_S:
            why_end = f"belief older than {BELIEF_MAX_S:.0f} s"
            self._belief_expired_for = b.lost_ms  # not restarted for the same loss: it would only be older still
        if why_end is not None:
            if b is not None:
                self._end_belief(now, why_end)
            return
        streets = self.map_provider() if self.map_provider else None
        if not streets or not self.calib.valid:
            return
        snap = self._subj_snap
        if b is None:
            if snap is None or snap["t_ms"] == self._belief_expired_for:
                return
            lost_s = (now - snap["t_ms"]) / 1000.0
            if lost_s < BELIEF_SPAWN_S and not rebound:
                return
            self._start_belief(now, streets, snap)
            return
        if now - self._belief_last_ms < BELIEF_TICK_MS - 1:
            return
        dt = max(0.05, (now - self._belief_last_ms) / 1000.0)
        self._belief_last_ms = now
        self._belief_hypotheses(now)
        d = self.drone_state(now)
        seen = d.footprint_xy_m if (d is not None and self._drone_current() and self.calib.valid) else None
        # people detected right now whom no hypothesis explains (not the responder, not a hypothesis's own person):
        # only near them could the subject be in view without the camera "seeing nobody"
        explained = {self.locks["responder"].track_id} | {h.track_id for h in b.hyps if h.visible}
        for h in b.hyps:
            o = self.memory.objects.get(h.object_id) if h.object_id else None
            if o is not None and o.track_id:
                explained.add(o.track_id)
        people = [tuple(kf.position) for tid, kf in self.world_kf.items()
                  if tid not in explained and any(t.track_id == tid and t.matched_now for t in self.raw_tracks)]
        visible = {}
        explained: set[str] = set()
        for h in b.hyps:
            o = self.memory.objects.get(h.object_id) if h.object_id else None
            if o is not None and o.state == "VISIBLE" and now - o.last_seen_ms < 300.0:
                visible[h.hid] = ((float(o.xy[0]), float(o.xy[1])), (float(o.vel[0]), float(o.vel[1])), float(o.err_m))
            elif h.hid != "H1" and o is not None and now - o.last_seen_ms < 10000.0 and \
                    (o.state in ("VISIBLE", "OUT_OF_VIEW") or math.hypot(*h.vel) > 1.0):
                # a look-alike seen moving out of view (the memory keeps their last spot, so it may read LOST): they
                # exist under every hypothesis, so their leaving the view says nothing about which one is true
                explained.add(h.hid)
        n_before = len(b.hyps)
        survived = b.step(dt, d.position_xy_m if d else None, seen, people, visible, now, explained)
        for h in b.hyps:  # one running "looked there, not seen" entry per hypothesis
            if h.hid in survived:
                ev = next((e for e in h.evidence if e["kind"] == "search"), None)
                if ev is None:
                    ev = {"kind": "search", "factor": 1.0, "provenance": self._belief_provenance()}
                    h.evidence.append(ev)
                ev["factor"] = round(ev["factor"] * survived[h.hid], 4)
                ev["ts_ms"] = now
        if len(b.hyps) < n_before:
            for dr in b.dropped[-(n_before - len(b.hyps)):]:
                self.outbox.append({"type": "pursuit", "kind": "belief_hypothesis_dropped", **dr})
        a = self.autonomy
        if a.fsm != "REACQUIRE" and (self.pursuit_candidates is None or now - self.pursuit_candidates[0] > 1000.0):
            self._candidates(now)  # for the reasoning card while the drone holds or coasts (D12 is not asked)

    def _start_belief(self, now: float, streets: dict, snap: dict) -> None:
        g = self._graph(streets)
        self._belief_seq += 1
        b = StreetBelief(g, now, snap["t_ms"], seed=1000 + self._belief_seq)
        vel = snap["vel"] or (0.0, 0.0)
        moving = math.hypot(*vel) >= 0.4
        h = Hypothesis("H1", "subject (continued, unseen)", snap["oid"], snap["track_id"], snap["xy"],
                       vel if moving else (0.0, 0.0), 1.0, "the designated subject's own track, last observed",
                       snap["colors"], snap["hist"], max(0.5, snap["err"]), None if moving else snap["prior"])
        b.add(h, now)
        if self.look_for and self.look_for.get("descriptor") is not None:
            b.apply_description(self.look_for["descriptor"], self.look_for["text"], now, self.look_for.get("by", "operator"))
        self.belief = b
        self._belief_last_ms = now
        self.pursuit_route = None
        self.pursuit_candidates = None
        self._belief_hypotheses(now)
        sp = math.hypot(*vel)
        self.outbox.append({"type": "pursuit", "kind": "belief_started", "ts_ms": now,
                            "lost_s": round((now - snap["t_ms"]) / 1000.0, 1),
                            "last_seen_xy": [round(snap["xy"][0], 1), round(snap["xy"][1], 1)],
                            "speed_mps": round(sp, 2), "particles": b.n_per, "provenance": self._belief_provenance(),
                            "map": g.source})

    def _belief_hypotheses(self, now: float) -> None:
        """Identity hypotheses from the object memory: anyone first seen after the loss, who looks like the subject
        and could have got there, may be the subject; so may the track the lock re-bound to without confirmation."""
        b = self.belief
        snap = self._subj_snap
        if b is None or snap is None:
            return
        subj = self.locks["subject"]
        resp = self.locks["responder"].track_id
        ref = snap["hist"]
        rebound_oid = None
        if subj.state == "LOCKED" and not subj.verified and subj.track_id:
            o = self.memory.object_for_track(subj.track_id)
            rebound_oid = o.oid if o else None
        for o in list(self.memory.objects.values()):
            if o.label != "person" or o.oid == snap["oid"] or b.find(o.oid) is not None or o.oid in b.rejected:
                continue
            if o.track_id is not None and o.track_id in subj.rejected:
                continue  # a person said this is not the subject
            if o.track_id is not None and o.track_id == resp:
                continue
            re_bound = o.oid == rebound_oid
            if o.first_ms < b.lost_ms - 300.0 and not re_bound:
                continue  # seen while the subject was still in view: someone else
            sim = similarity(o.hist, ref) if (o.hist is not None and ref is not None) else None
            if sim is None or (sim < LOOKALIKE_SIM and not re_bound):
                continue
            fx = o.first_xy or (float(o.xy[0]), float(o.xy[1]))
            dt_s = max(0.5, (o.first_ms - b.lost_ms) / 1000.0)
            need = math.dist(fx, snap["xy"]) / dt_s
            if need > MAX_SUBJECT_MPS * 1.25 and not re_bound:
                continue  # could not have got there in the time
            seen_mps = max(math.hypot(*(snap["vel"] or (0.0, 0.0))), math.hypot(*(snap["prior"] or (0.0, 0.0))), 1.4)
            reach = 1.0 if need <= 1.15 * seen_mps else max(0.25, 1.0 - (need - 1.15 * seen_mps) / 2.0)
            # data association: a person appearing where the subject's own cloud says the subject would be now is
            # stronger evidence than one appearing anywhere reachable; that share of the cloud moves to the new one
            near = 0.0
            h1 = b.find(snap["oid"]) or (b.hyps[0] if b.hyps else None)
            if h1 is not None:
                i1 = b.hyps.index(h1)
                xy_now = b.positions()
                m1 = b.k == i1
                d_near = np.hypot(xy_now[m1, 0] - o.xy[0], xy_now[m1, 1] - o.xy[1]) < LOOKALIKE_NEAR_M
                near = float(b.w[m1][d_near].sum())
            prior = 0.6 * sim * reach + 4.0 * near * sim  # relative to 1.0 for "the subject continued unseen"
            if h1 is not None and near > 0:
                h1.logw += math.log(max(1e-3, 1.0 - 0.8 * near))
                h1.evidence.append({"kind": "explained", "value": round(near, 3), "ts_ms": now,
                                    "why": f"{100 * near:.0f}% of this cloud was where {o.oid} appeared"})
            why = (f"the lock re-bound to it without confirmation; appearance {sim:.2f}" if re_bound else
                   f"first seen {dt_s:.0f} s after the loss, {need:.1f} m/s from the last sighting "
                   f"(the subject ran {seen_mps:.1f}); appearance {sim:.2f}; {100 * near:.0f}% of the subject's cloud "
                   f"was there")
            n = len(b.hyps) + len(b.dropped) + 1
            h = b.add(Hypothesis(f"H{n}", f"person seen after the loss ({o.oid})", o.oid, o.track_id,
                                 (float(o.xy[0]), float(o.xy[1])),
                                 (float(o.vel[0]), float(o.vel[1])), prior, f"memory: {why}",
                                 self._voted(*self._obj_votes[o.oid]) if o.oid in self._obj_votes else o.colors, o.hist,
                                 float(o.err_m)), now)
            self.outbox.append({"type": "pursuit", "kind": "belief_hypothesis_added", "ts_ms": now, "hypothesis": h.hid,
                                "object_id": o.oid, "track_id": o.track_id, "prior": round(prior, 3), "why": why,
                                "weights": {k: round(v, 3) for k, v in b.hyp_weights().items()},
                                "provenance": self._belief_provenance()})

    def _end_belief(self, now: float, why: str) -> None:
        b = self.belief
        if b is None:
            return
        self.pursuit_last = {"ended": why, "ts_ms": now, "started_ms": b.started_ms,
                             "hypotheses": [{"id": h.hid, "label": h.label, "weight": round(w, 3)}
                                            for h, w in zip(b.hyps, b.weights())],
                             "route": self.pursuit_route}
        self.outbox.append({"type": "pursuit", "kind": "belief_ended", "ts_ms": now, "why": why,
                            "duration_s": round((now - b.started_ms) / 1000.0, 1),
                            "weights": {k: round(v, 3) for k, v in b.hyp_weights().items()}})
        self.belief = None
        self.pursuit_candidates = None

    def _candidates(self, now: float) -> list[dict]:
        b = self.belief
        d = self.drone_state(now)
        if b is None or d is None or d.position_xy_m is None:
            return []
        snap = self._subj_snap or {}
        subj_edge = None
        if snap.get("xy") is not None:
            k, _, _ = b.g.snap(snap["xy"])
            subj_edge = b.g.und_key[k]
        people = [tuple(kf.position) for tid, kf in self.world_kf.items()
                  if any(t.track_id == tid and t.matched_now for t in self.raw_tracks)]
        current = None
        if self.pursuit_route and self.autonomy.primitive == f"route:{self.pursuit_route['candidate_id']}":
            current = next((c["key"] for c in (self.pursuit_candidates or (0, []))[1]
                            if c["id"] == self.pursuit_route["candidate_id"]), None)
        cands = coverage_candidates(b, d.position_xy_m, self.cfg.autonomy.altitude_m, self.drone_intr,
                                    self.cfg.autonomy.reacquire_max_speed_mps, self.autonomy.safety, people,
                                    list(self.cfg.autonomy.geofence) or None, subj_edge, current)
        self.pursuit_candidates = (now, cands)
        return cands

    def _d12_job(self, now: float, streets: dict):
        """D12: code scores viewpoints by the share of the belief the camera would cover there; the engine (or the
        rules) picks one of the top three, or hold."""
        b = self.belief
        if b is None:
            return None
        cands = self._candidates(now)
        if not cands:
            return None
        safe = [c for c in cands if not c["unsafe"]]
        top = safe[:3] + [c for c in cands if c["unsafe"]][:2]  # the removed ones stay visible as removed
        w = b.hyp_weights()
        facts = {"time_since_loss_s": round((now - b.lost_ms) / 1000.0, 1),
                 "belief": f"street-graph particles, {b.n_per} per identity hypothesis",
                 "hypotheses": [{"id": h.hid, "label": h.label, "weight": round(w[h.hid], 3), "visible": h.visible}
                                for h in b.hyps],
                 "map": streets.get("source", "map data"), "candidates": top,
                 "baseline_pick": safe[0]["id"] if safe else "hold"}
        job = d12_pursuit_route(facts)
        job.context = {"routes": {c["id"]: c for c in cands}}
        return job

    def _subject_motion_before_loss(self):
        """From the subject track's observed trail (not the coasting filter): the last observed position, the velocity
        over the last 2 s of observations, and the last clearly moving velocity in the 12 s before."""
        tid = self.locks["subject"].track_id
        trail = list(self.trails.get(tid, ())) if tid else []
        if len(trail) < 2:
            return None, None, None
        x1, y1, t1 = trail[-1]
        def vel_over(win_ms: float, end_t: float):
            pts = [p for p in trail if end_t - win_ms <= p[2] <= end_t]
            if len(pts) < 2 or pts[-1][2] - pts[0][2] < 600:
                return None
            dt = (pts[-1][2] - pts[0][2]) / 1000.0
            return ((pts[-1][0] - pts[0][0]) / dt, (pts[-1][1] - pts[0][1]) / dt)
        recent = vel_over(2000.0, t1)
        prior = None
        for back in range(0, 12000, 1000):  # the most recent 2 s window in which they were clearly moving
            v = vel_over(2000.0, t1 - back)
            if v is not None and math.hypot(*v) > 1.0:
                prior = v
                break
        return (x1, y1), recent, prior

    def set_look_for(self, text: str | None, by: str = "operator") -> dict | None:
        """The operator's "look for" request: clothing words, parsed into a clothing descriptor. They reach pursuit
        routing only as evidence on the belief's identity hypotheses (how well each one's clothing matches), never as
        text passed to a model."""
        with self.lock:
            now = self.clock.now_ms()
            t = " ".join((text or "").split())
            desc = parse_description(t) if t else None
            self.look_for = {"text": t, "since_ms": now, "by": by, "descriptor": desc,
                             "parsed": desc.text() if desc else None, "provenance": "manual"} if t else None
            self._log_operator("look_for", text=t)
            b = self.belief
            if b is not None:
                lk = b.apply_description(desc, t, now, by)
                self.outbox.append({"type": "pursuit", "kind": "belief_evidence", "ts_ms": now, "evidence": "description",
                                    "text": t, "parsed": desc.text() if desc else None, "likelihoods": lk,
                                    "weights": {k: round(v, 3) for k, v in b.hyp_weights().items()},
                                    "provenance": "manual"})
            return self.look_for

    def _subject_color(self) -> str:
        tid = self.locks["subject"].track_id
        return self.track_colors.get(tid or "", ("unknown", "unknown"))[0]

    # ===========================================================================================================
    # Autonomy (20 Hz)
    # ===========================================================================================================
    def _autonomy_inputs(self, now: float) -> AutonomyInputs:
        subj = self.locks["subject"]
        box = None
        if subj.state == "LOCKED" and subj.track_id:
            for t in self.raw_tracks:
                if t.track_id == subj.track_id and t.matched_now:
                    box = t.measured_box or t.box
        d = self.drone_state(now)
        people = [kf.position for tid, kf in self.world_kf.items()
                  if any(t.track_id == tid and t.matched_now for t in self.raw_tracks)]
        snap = subj.snapshot(now)
        occl = False
        if self.last_frame is not None and subj.track_id in self.track_last_box and subj.state != "LOCKED":
            occl = _canopy_fraction(self.last_frame, self.track_last_box[subj.track_id]) > 0.3
        W, H = self.last_frame_size
        return AutonomyInputs(
            now_ms=now, subject_state=subj.state, subject_box=box, image_size=(W, H), hfov_deg=self.drone_intr.hfov_deg,
            vfov_deg=self.drone_intr.vfov_deg, subject_xy=subj.kf.position if subj.kf else None,
            subject_vel=subj.kf.velocity if subj.kf else None, last_seen_xy=subj.last_seen_xy,
            lost_s=(snap.lost_ms or 0) / 1000.0 if snap.lost_ms is not None else None,
            drone=DroneSafetyState(d.position_xy_m if d else None, d.alt_m if d else None, d.yaw_deg if d else None,
                                   d.battery_pct if d else None, True),
            gimbal_pitch_deg=d.gimbal_pitch_deg if d else None, people_xy=people,
            calibration_valid=self.calib.valid, occluder_near_last_seen=occl,
            footprint=d.footprint_xy_m if d else None, responder_xy=self.responder.position_xy_m,
            subject_verified=bool(subj.verified) and (self.mode != "LIVE" or self.autonomy.target_is_sim
                                                     or subj.track_id == self.flight_confirmed_track_id),
            subject_track_id=subj.track_id,
            video_fresh=self._age("drone", now) is not None
                        and self._age("drone", now) <= self.cfg.sources.drone.max_age_ms)

    def autonomy_tick(self, now: float) -> None:
        with self.lock:
            if self.autonomy.mode in ("FOLLOW", "HOLD"):
                problem = self._real_flight_problem(now)
                if problem:
                    self.autonomy.set_mode("MANUAL", now, problem)
            st, events = self.autonomy.tick(self._autonomy_inputs(now))
            self.pending_safety.extend(events)

    def _real_flight_problem(self, now: float) -> str | None:
        if self.mode != "LIVE" or not self.cfg.autonomy.allow_real_flight:
            return None
        bridge = self.autonomy.bridge
        if not getattr(bridge, "control_capable", False) or getattr(bridge, "control_inhibited", True):
            return "connect the SDK stream, neutralize RC controls, then press Enable / re-arm on the website"
        for src, max_age in (("drone", self.cfg.sources.drone.max_age_ms),
                             ("telemetry", self.cfg.sources.telemetry.max_age_ms)):
            age = self._age(src, now)
            if age is None or age > max_age:
                return f"real-flight control stopped: {src} stale or missing"
        d = self.drone_state(now)
        if self.cfg.autonomy.motion_profile in ("camera_only", "visual_follow"):
            if self.cfg.autonomy.motion_profile == "visual_follow" and not getattr(bridge, "is_sim", False) and (
                not getattr(bridge, "translation_supported", False) or not getattr(bridge, "translation_armed", False)):
                return "movement permission not confirmed by the SDK; press Enable aircraft controls with Movement + camera selected"
            if d is None or any(v is None for v in (d.alt_m, d.yaw_deg, d.battery_pct, d.gimbal_pitch_deg)):
                return "camera follow requires altitude, heading, battery and gimbal telemetry"
            if not d.flying and not getattr(bridge, "is_sim", False):
                return "camera follow requires an airborne aircraft; use the DJI simulator for props-off testing"
            return None  # image-space rotation makes no world-position or distance claim
        if not getattr(bridge, "is_sim", False) and (not getattr(bridge, "translation_supported", False)
                                                    or not getattr(bridge, "translation_armed", False)):
            return "full-motion control requires the updated bridge and local Allow aircraft translation opt-in"
        if d is None or any(v is None for v in (d.position_xy_m, d.alt_m, d.yaw_deg, d.battery_pct)):
            return "real-flight control requires position, altitude, heading and battery telemetry"
        if not self.calib.valid:
            return "real-flight control requires valid ground calibration"
        if not getattr(bridge, "is_sim", False):
            from .autonomy.safety import fence_margin
            tel = self.telemetry
            if not d.flying or tel is None or (tel.gps_level or 0) < 4 or (tel.sats or 0) < 6:
                return "full-motion follow requires an airborne aircraft and a strong GPS fix"
            if self.cfg.world.geo_reference is None or len(self.cfg.autonomy.geofence) < 3:
                return "full-motion follow requires the actual site's GPS reference and geofence"
            if fence_margin(self.cfg.autonomy.geofence, *d.position_xy_m) < 2:
                return "aircraft must be at least 2 m inside the configured geofence"
            if not self.cfg.autonomy.min_altitude_m <= d.alt_m <= self.cfg.autonomy.max_altitude_m:
                return "pilot must place aircraft within the configured altitude range before full-motion follow"
        return None

    # ===========================================================================================================
    # Decisions coming back
    # ===========================================================================================================
    def apply_decision(self, job: DecisionJob, records: list[DecisionRecord]) -> None:
        with self.lock:
            now = self.clock.now_ms()
            obsolete_control = job.task in ("D1_reacquire", "D12_pursuit_route") and (
                self.autonomy.mode != "FOLLOW" or job.context.get("control_epoch") != self.autonomy.control_epoch)
            if obsolete_control:
                for record in records:
                    record.acted = False
                    record.facts = {**record.facts, "_dropped": "autonomy control session changed"}
                    record.reason = "ignored: pilot takeover or autonomy mode changed while decision was pending"
            for r in records:
                self.decision_log.append(r)
                self.outbox.append(r.model_dump())
                self.metrics.inc(f"decision_{r.engine}_{r.status}")
                if r.latency_ms is not None and not r.shadow:
                    self.metrics.observe(f"decision_ms_{r.engine}", r.latency_ms)
            rec = next((r for r in records if not r.shadow), None)
            if rec is None:
                return
            if obsolete_control:
                return  # a late model response must never resurrect a plan after pilot takeover
            if job.task == "D5_priority":
                for a in self.arbiter.on_decision(rec, now):
                    self._emit_alert(a, now)
            elif job.task == "D12_pursuit_route" and not rec.facts.get("_dropped"):
                routes = job.context.get("routes", {})
                pick, source = (rec.selected, f"decision:{rec.engine}") if rec.acted else \
                    (rec.facts.get("_rules_baseline"), "rules (model below gate)" if rec.engine != "rules" else "rules")
                r = routes.get(pick or "")
                if r is not None:
                    self.autonomy.set_route(pick, r["waypoints"], f"{r['label']} (covers {100 * r['coverage']:.0f}% of "
                                            f"the belief)", source, now, D12_HOLD_MS, look_at=r.get("target_xy"))
                elif pick == "hold":
                    self.autonomy.set_primitive("hold_and_alert", source, now, D12_HOLD_MS)
                ans = next((x for x in rec.answers if x.type == "choice"), None)
                prev = self.pursuit_route
                if prev is None or prev.get("label") != (r["label"] if r else None) or prev.get("source") != source:
                    # the incident log keeps what each pick meant (the decision event alone only names its id)
                    self.outbox.append({"type": "pursuit", "kind": "pursuit_route_changed", "ts_ms": now,
                                        "candidate_id": pick or "hold", "label": r["label"] if r else "hold",
                                        "coverage": r.get("coverage") if r else None,
                                        "by_hypothesis": r.get("by_hypothesis") if r else None,
                                        "score": r.get("score") if r else None, "eta_s": r.get("eta_s") if r else None,
                                        "source": source, "decision_id": rec.decision_id,
                                        "provenance": self._belief_provenance()})
                self.pursuit_route = {"candidate_id": pick or "hold",
                                      "label": r["label"] if r else "hold: the search goes to the operator",
                                      "waypoints": r["waypoints"] if r else [], "source": source, "engine": rec.engine,
                                      "model_pick": rec.selected if rec.engine != "rules" else None,
                                      "baseline_pick": rec.facts.get("_rules_baseline"),
                                      "confidence": ans.confidence if ans else None, "acted": rec.acted,
                                      "reason": rec.reason, "decision_id": rec.decision_id, "ts_ms": now,
                                      "provenance": self._belief_provenance()}
            elif job.task == "D6_obstacle" and not rec.facts.get("_dropped"):
                pick, source = (rec.selected, f"decision:{rec.engine}") if rec.acted else \
                    (rec.facts.get("_rules_baseline"), "rules (model below gate)" if rec.engine != "rules" else "rules")
                if pick:
                    self.autonomy.set_maneuver(str(pick), source, now)
            elif job.task == "D13_intent":
                self.voice.on_intent(job, rec if not rec.facts.get("_dropped") else None)
            elif job.task == "D1_reacquire" and not rec.facts.get("_dropped"):
                if rec.acted and rec.selected:
                    self.autonomy.set_primitive(rec.selected, f"decision:{rec.engine}", now, D1_HOLD_MS)
                elif rec.facts.get("_rules_baseline"):  # below the gate or refused: the rules' pick is flown instead
                    self.autonomy.set_primitive(str(rec.facts["_rules_baseline"]), "rules (model below gate)", now, D1_HOLD_MS)
            if self.decision_hook is not None:
                self.decision_hook(job, rec)
            if job.task == "D2_identity":
                subj = self.locks["subject"]
                tid = job.context.get("labels", {}).get(rec.selected or "")
                if rec.acted and tid and subj.state == "UNCERTAIN":
                    info = self._track_info(tid)
                    if info is not None:
                        evs = subj.confirm_candidate(info, now, f"decision {rec.engine}", self._conf(rec))
                        self.pending_lock_events.extend(evs)
            elif job.task == "D3_describe" and not job.context.get("person_mark_request"):
                tid = job.context.get("labels", {}).get(rec.selected or "")
                self.describe_proposal = {"decision_id": rec.decision_id, "description": job.facts.get("description"),
                                          "track_id": tid, "label": rec.selected, "confidence": self._conf(rec),
                                          "engine": rec.engine, "acted": rec.acted, "reason": rec.reason,
                                          "needs_operator_confirmation": True}
                self.outbox.append({"type": "describe_proposal", **self.describe_proposal})
            elif job.task == "D4_motion" and rec.status in ("success", "fallback"):
                self.motion_label = {"label": rec.selected, "confidence": self._conf(rec), "engine": rec.engine,
                                     "acted": rec.acted, "ts_ms": now, "decision_id": rec.decision_id}

    @staticmethod
    def _conf(rec: DecisionRecord) -> float | None:
        a = next((x for x in rec.answers if x.type == "choice"), None)
        return a.confidence if a else None

    def _track_info(self, tid: str) -> TrackInfo | None:
        for t in self.raw_tracks:
            if t.track_id == tid:
                kf = self.world_kf.get(tid)
                return TrackInfo(tid, t.state, t.matched_now, t.measured_box or t.box, t.conf,
                                 kf.position if kf else None, self.track_ground_err.get(tid), self.track_hist.get(tid), t.hits,
                                 False, kf.velocity if (kf is not None and kf.updates >= 5) else None)
        return None

    # ===========================================================================================================
    # Operator commands
    # ===========================================================================================================
    def _log_operator(self, action: str, **kw: Any) -> None:
        if self.recorder is not None:
            self.recorder.operator({"ts_ms": self.clock.now_ms(), "action": action, **kw})

    def select_track(self, track_id: str | None) -> None:
        with self.lock:
            self.selected_track_id = track_id

    def click(self, u: float, v: float) -> str | None:
        """Track under an image click (drone pixel coords); nearest box centre within 25 px otherwise."""
        with self.lock:
            best, bd = None, 1e9
            for t in self.raw_tracks:
                if t.state == "TENTATIVE":
                    continue
                x0, y0, x1, y1 = t.measured_box if (t.matched_now and t.measured_box) else t.box
                if x0 - 6 <= u <= x1 + 6 and y0 - 6 <= v <= y1 + 6:
                    d = 0.0
                else:
                    d = math.hypot(u - (x0 + x1) / 2, v - (y0 + y1) / 2)
                if d < bd:
                    best, bd = t.track_id, d
            return best if bd <= 25 else None

    def lock_track(self, role: str, track_id: str, locked_by: str = "operator", description: str | None = None) -> list[str]:
        with self.lock:
            now = self.clock.now_ms()
            if role not in self.locks:
                raise ValueError("role must be subject or responder")
            if self.cfg.privacy.incident_required_for_lock and not self.incident.active:
                raise PermissionError("start an incident (with participant consent) before locking")
            if role == "subject" and self.crowd:
                raise PermissionError("crowd scene: subject locks are disabled by design")
            info = self._track_info(track_id)
            if info is None:
                raise KeyError(f"no such track {track_id}")
            other = "responder" if role == "subject" else "subject"
            if self.locks[other].track_id == track_id:
                self.pending_lock_events.extend(self.locks[other].release(now, f"track re-assigned to {role}"))
            evs = self.locks[role].engage(info, now, locked_by, description)
            self.pending_lock_events.extend(evs)
            b = info.box
            # the click position is what the operator did; track IDs carry a session epoch and may differ on replay
            self._log_operator("lock", role=role, track_id=track_id, u=round((b[0] + b[2]) / 2, 1), v=round((b[1] + b[3]) / 2, 1),
                               locked_by=locked_by, description=description)
            if role == "subject":
                self.reference_crop = None
                self.flight_confirmed_track_id = track_id
            self.selected_track_id = track_id
            return [e.text for e in evs]

    def unlock(self, role: str) -> None:
        with self.lock:
            now = self.clock.now_ms()
            tid = self.locks[role].track_id
            self.pending_lock_events.extend(self.locks[role].release(now, "operator"))
            if role == "subject":
                self.flight_confirmed_track_id = None
            if role == "subject" and self.autonomy.mode in ("FOLLOW", "HOLD"):
                self.autonomy.set_mode("MANUAL", now, "operator released target")
            self._log_operator("unlock", role=role)
            ds = self.dispatch_state
            if role == "subject" and ds is not None and ds.phase == "TRACKING":
                # the operator rejected the match: never that track again, back to the search legs
                if tid:
                    self._dispatch_rejected.add(tid)
                ds.rejected += 1
                ds.matched_track, ds.matched_by, ds.phase = None, None, "SEARCH"
                self._dispatch_votes.clear()
                self.autonomy.resume_search(now, "operator rejected the match")
                self._inject("DISPATCH_REJECTED", "attention", now, "Match rejected by the operator: the drone resumes "
                             "the search legs", data={"track_id": tid})

    def reject_rebind(self, by: str = "operator") -> list[str]:
        """'Not them': the subject lock's unverified re-binding is the wrong person. The lock searches again and the
        belief drops that person's hypothesis (evidence from a person, provenance manual)."""
        with self.lock:
            now = self.clock.now_ms()
            subj = self.locks["subject"]
            wrong = subj.track_id
            evs = subj.reject_rebind(now, by)
            if not evs:
                raise ValueError("no unverified re-binding to reject")
            self.pending_lock_events.extend(evs)
            self._log_operator("reject_rebind", track_id=wrong, by=by)
            b = self.belief
            o = self.memory.object_for_track(wrong) if wrong else None
            if b is not None and o is not None:
                b.rejected.add(o.oid)
            h = b.find(o.oid) if (b is not None and o is not None) else None
            if h is not None:
                b.drop(h.hid, f"{by}: not the subject", now)
                self.outbox.append({"type": "pursuit", "kind": "belief_hypothesis_dropped", "ts_ms": now, "hid": h.hid,
                                    "why": f"{by}: not the subject", "provenance": "manual"})
            return [e.text for e in evs]

    def confirm_candidate(self, track_id: str) -> None:
        with self.lock:
            info = self._track_info(track_id)
            if info is None:
                raise KeyError(track_id)
            self.pending_lock_events.extend(self.locks["subject"].confirm_candidate(info, self.clock.now_ms(), "operator"))
            self.flight_confirmed_track_id = track_id
            self._log_operator("confirm_candidate", track_id=track_id)

    def describe(self, text: str, context: dict | None = None) -> bool:
        with self.lock:
            cands = [t for t in self.raw_tracks if t.state == "CONFIRMED" and t.matched_now and t.class_name == "person"
                     and t.track_id != self.locks["responder"].track_id][:8]
            if not cands or self.decisions is None:
                return False
            labels = [chr(ord("A") + i) for i in range(len(cands))]
            facts = {"description": text, "candidates": [
                {"label": lab, "track": short(t.track_id), "torso_color": self.track_colors.get(t.track_id, ("unknown",) * 2)[0],
                 "lower_color": self.track_colors.get(t.track_id, ("unknown",) * 2)[1],
                 "moving": bool(self.world_kf.get(t.track_id) and self.world_kf[t.track_id].speed > 0.4)}
                for lab, t in zip(labels, cands)]}
            job = d3_describe(facts, labels)
            job.context = {"labels": dict(zip(labels, [t.track_id for t in cands])), **(context or {})}
            if self.cfg.decisions.allow_images and self.last_frame is not None:
                tile, _ = crop_tile(self.last_frame, None, [t.measured_box or t.box for t in cands])
                ok, buf = cv2.imencode(".jpg", tile, [cv2.IMWRITE_JPEG_QUALITY, 85])
                job.image_jpeg = buf.tobytes() if ok else None
            self.decisions.last_fp.pop("D3_describe", None)
            return bool(self.decisions.submit(job))

    # ===========================================================================================================
    # Dispatch (simulator only): launch from the pad, search for a described person, track, report
    # ===========================================================================================================
    def _inject(self, kind: str, severity: str, now: float, text: str, track_id: str | None = None,
                data: dict | None = None, ttl_ms: float = 4000.0) -> None:
        """An event from the dispatch logic, delivered with the next tick's events."""
        self._injected.append(Event(event_id=self._inject_ids(), kind=kind, severity=severity, ts_ms=now, text=text,  # type: ignore[arg-type]
                                    track_id=track_id, role="subject" if track_id else None, source="dispatch",
                                    ttl_ms=ttl_ms, data=data or {}))

    def goto(self, xy: tuple[float, float], label: str, by: str = "operator") -> None:
        """Fly to a place along the streets (when there is a street map) and look at it: an operator's GOTO."""
        with self.lock:
            now = self.clock.now_ms()
            d = self.drone_state(now)
            if d is None or d.position_xy_m is None:
                raise ValueError("no drone position yet: wait for telemetry")
            if self.dispatch_state is not None:
                self.cancel_dispatch(by=by)
            streets = self.map_provider() if self.map_provider else None
            path = [tuple(xy)]
            if streets:
                g = self._graph(streets)
                fence = list(self.cfg.autonomy.geofence)
                from .autonomy.safety import fence_margin
                ok = np.array([fence_margin(fence, float(x), float(y)) >= 2.0 for x, y in g.xy]) if fence else None
                r = g.route(d.position_xy_m, tuple(xy), ok)
                if r is not None:
                    path = r[0][:-1] + [tuple(xy)]
            from .autonomy.pursuit import look_back_m
            last = np.array(path[-2] if len(path) >= 2 else d.position_xy_m, float)
            tgt = np.array(xy, float)
            u = tgt - last
            n = float(np.linalg.norm(u))
            hover = tgt - (u / n) * look_back_m(self.cfg.autonomy.altitude_m) if n > 1e-6 else tgt
            path = path[:-1] + [(float(hover[0]), float(hover[1]))]
            self._log_operator("goto", xy=list(xy), label=label, by=by)
            self.autonomy.start_goto(path, (float(xy[0]), float(xy[1])), label, now, by)

    def search_area(self, polygon: list[tuple[float, float]], label: str, by: str = "operator") -> DispatchStatus:
        """Sweep an area (a sector or the ground around a place): the dispatch's search legs with no description to
        match. People the detector sees are flagged for review by Search as usual; nothing is locked."""
        with self.lock:
            self.autonomy.plan = None
            alt = max(self.cfg.autonomy.altitude_m, self.cfg.autonomy.min_altitude_m)
            st = self.dispatch("", area=polygon, altitude_m=alt, by=by, sweep_label=label)
            return st

    def dispatch(self, description: str, last_seen_xy: tuple[float, float] | None = None, radius_m: float = 60.0,
                 area: list[tuple[float, float]] | None = None, altitude_m: float = 28.0,
                 by: str = "operator", sweep_label: str | None = None) -> DispatchStatus:
        """Dispatch the drone from where it is: take off, fly to the search area, fly its legs; people seen on the
        way are matched against the description by clothing colour words (never a face), the first clear match is
        tracked and its position reported relative to the launch point. Simulator only: a real aircraft gets this
        as an advisory (the virtual stick stays disabled)."""
        desc = " ".join(description.lower().split())
        if not desc and sweep_label is None:
            raise ValueError("describe who to look for (clothing words)")
        with self.lock:
            now = self.clock.now_ms()
            d = self.drone_state(now)
            if d is None or d.position_xy_m is None:
                raise ValueError("no drone position yet: wait for telemetry")
            if self.cfg.privacy.incident_required_for_lock and not self.incident.active:
                raise PermissionError("start an incident (with participant consent) before a dispatch")
            pad = (float(d.position_xy_m[0]), float(d.position_xy_m[1]))
            fence = list(self.cfg.autonomy.geofence)
            centre = tuple(last_seen_xy) if last_seen_xy else pad
            poly = [tuple(p) for p in area] if area else [(centre[0] - radius_m, centre[1] - radius_m),
                                                           (centre[0] + radius_m, centre[1] - radius_m),
                                                           (centre[0] + radius_m, centre[1] + radius_m),
                                                           (centre[0] - radius_m, centre[1] + radius_m)]
            if fence:  # the search area is clipped to the geofence's bounding box
                xs, ys = [p[0] for p in fence], [p[1] for p in fence]
                poly = [(min(max(x, min(xs) + 2), max(xs) - 2), min(max(y, min(ys) + 2), max(ys) - 2)) for x, y in poly]
            gimbal = -72.0
            spacing = footprint_width_m(altitude_m, self.drone_intr.hfov_deg, gimbal) * 0.6
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            heading = 90.0 if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else 0.0  # legs along the longer side
            legs = lawnmower(poly, spacing, heading)
            if not legs:
                raise ValueError("the search area is empty")
            # start the legs at the end nearest the pad
            if math.dist(legs[-1], pad) < math.dist(legs[0], pad):
                legs = legs[::-1]
            plan = DispatchPlan(desc, pad, list(legs), altitude_m, self.cfg.autonomy.reacquire_max_speed_mps, gimbal)
            self.autonomy.start_dispatch(plan, now, by)
            self._dispatch_votes.clear()
            self._dispatch_rejected.clear()
            self._dispatch_seen.clear()
            self._dispatch_report_ms = -1e12
            self.dispatch_state = DispatchStatus(description=desc, phase="TAKEOFF", pad_xy=pad, started_ms=now,
                                                 legs=len(legs), altitude_m=altitude_m)
            self._log_operator("dispatch", description=desc, last_seen_xy=centre, radius_m=radius_m, area=area,
                               altitude_m=altitude_m, by=by)
            self._inject("DISPATCH_STARTED", "attention", now,
                         f"Search by {by}: sweeping {sweep_label} over {len(legs)} legs at {altitude_m:.0f} m" if sweep_label
                         else f"Dispatched by {by}: looking for '{desc}' over {len(legs)} legs at {altitude_m:.0f} m, "
                         f"launching from ({pad[0]:.0f}, {pad[1]:.0f}) m")
            return self.dispatch_state

    def cancel_dispatch(self, by: str = "operator") -> None:
        with self.lock:
            now = self.clock.now_ms()
            if self.dispatch_state is not None:
                self._inject("DISPATCH_CANCELLED", "attention", now, f"Dispatch cancelled by {by}: holding")
            self.dispatch_state = None
            self.autonomy.plan = None
            if self.autonomy.mode == "DISPATCH":
                self.autonomy.set_mode("HOLD", now, by)
            self._log_operator("dispatch_cancel", by=by)

    def _dispatch_match(self, now: float, infos: dict[str, TrackInfo]) -> None:
        """Match the people in view against the description. A match needs the same answer on several frames
        (the colour names settle as the track grows) and nobody else matching at the same time."""
        ds = self.dispatch_state
        if ds is None:
            return
        plan = self.autonomy.plan
        if not ds.description:  # a sweep (operator SEARCH): nothing to match, the legs are flown
            if plan is not None:
                ds.phase = plan.phase  # type: ignore[assignment]
                ds.leg = max(0, plan.wp_i - plan.transit_legs + 1)
            return
        if plan is not None and ds.phase in ("TAKEOFF", "TRANSIT", "SEARCH", "COVERED"):
            ds.phase = plan.phase if plan.phase != "COVERED" else "COVERED"  # type: ignore[assignment]
            ds.leg = max(0, plan.wp_i - plan.transit_legs + 1)
        subj = self.locks["subject"]
        if ds.phase == "TRACKING":
            if not subj.active:
                ds.phase = "LOST"
            return
        if subj.active and subj.state == "LOCKED":
            return  # someone locked by hand: the operator has it
        resp = self.locks["responder"].track_id
        matches: list[tuple[float, str]] = []
        for tid, ti in infos.items():
            if not ti.matched_now or ti.state != "CONFIRMED" or tid == resp or tid in self._dispatch_rejected:
                continue
            tr = next((t for t in self.raw_tracks if t.track_id == tid), None)
            if tr is None or tr.class_name != "person":
                continue
            colors = self.track_colors.get(tid)
            score = describes(ds.description, colors) if colors else 0.0
            # one note per remembered person who does not match (the detector saw them; the words did not fit)
            obj = self.memory.object_for_track(tid)
            key = obj.oid if obj is not None else tid
            if key not in self._dispatch_seen and tr.hits >= 6 and colors:
                self._dispatch_seen.add(key)
                ds.people_seen += 1
                if score < 1.0:
                    self._inject("DISPATCH_SEEN", "info", now, f"Person seen ({colors[0]} top, {colors[1]} lower): "
                                 f"does not match '{ds.description}'", track_id=tid, data={"colors": colors})
            if score >= 1.0:
                self._dispatch_votes[tid] = self._dispatch_votes.get(tid, 0) + 1
                matches.append((score + 0.01 * self._dispatch_votes[tid], tid))
            else:
                self._dispatch_votes.pop(tid, None)
        if not matches:
            return
        matches.sort(reverse=True)
        best_score, best = matches[0]
        if self._dispatch_votes.get(best, 0) < 5:
            return
        if len(matches) > 1 and matches[1][0] >= best_score - 0.02:
            if "ambiguous" not in self._dispatch_seen:
                self._dispatch_seen.add("ambiguous")
                self._inject("DISPATCH_AMBIGUOUS", "warning", now, f"{len(matches)} people match '{ds.description}' at "
                             "once: not choosing between them; the operator designates", data={"tracks": [m[1] for m in matches]})
            return
        colors = self.track_colors.get(best, ("?", "?"))
        who = f"autonomy: matched '{ds.description}' by clothing colours ({colors[0]} top, {colors[1]} lower)"
        try:
            info = infos[best]
            evs = subj.engage(info, now, who, ds.description)
            self.pending_lock_events.extend(evs)
        except Exception as e:  # noqa: BLE001
            log.info("dispatch match lock failed: %s", e)
            return
        self.selected_track_id = best
        self.reference_crop = None
        ds.matched_track, ds.matched_by, ds.phase = best, who, "TRACKING"
        self.autonomy.set_mode("FOLLOW", now, "dispatch: matched the description")
        self._log_operator("dispatch_match", track_id=best, colors=colors, description=ds.description)
        self._inject("DISPATCH_MATCH", "warning", now, f"Match: track {short(best)} ({colors[0]} top, {colors[1]} lower) "
                     f"fits '{ds.description}'. Tracking it; reject with Release if it is the wrong person",
                     track_id=best, data={"colors": colors, "votes": self._dispatch_votes.get(best)})

    def _dispatch_report(self, now: float) -> None:
        """Every few seconds while tracking: where the target is relative to the launch point."""
        ds = self.dispatch_state
        if ds is None or ds.phase != "TRACKING" or now - self._dispatch_report_ms < 4000.0:
            return
        subj = self.locks["subject"]
        if not subj.active or subj.kf is None or subj.state not in ("LOCKED", "COAST"):
            return
        x, y = (float(v) for v in subj.kf.position)
        err = float(subj.kf_err) if subj.kf_err is not None else None
        dx, dy = x - ds.pad_xy[0], y - ds.pad_xy[1]
        dist = math.hypot(dx, dy)
        brg = heading_from_vector(dx, dy) % 360.0
        compass = "N NE E SE S SW W NW".split()[int((brg + 22.5) // 45) % 8]
        vx, vy = subj.kf.velocity
        speed = math.hypot(vx, vy)
        rep: dict[str, Any] = {"ts_ms": now, "x_m": round(x, 1), "y_m": round(y, 1), "distance_m": round(dist, 0),
                               "bearing_deg": round(brg, 0), "compass": compass, "speed_mps": round(speed, 1),
                               "heading_deg": round(heading_from_vector(vx, vy) % 360.0, 0) if speed > 0.4 else None,
                               "uncertainty_m": round(err, 1) if err is not None else None,
                               "state": subj.state, "relative_to": "launch point"}
        geo = self.cfg.world.geo_reference
        if geo is not None:
            lat, lon = local_to_latlon(x, y, geo, self.cfg.world.north_offset_deg)
            rep["lat"], rep["lon"] = round(lat, 6), round(lon, 6)
        ds.last_report = rep
        self._dispatch_report_ms = now
        moving = f", moving {compass_of(rep['heading_deg'])} at {speed:.1f} m/s" if rep["heading_deg"] is not None else ", stationary"
        text = (f"Target {dist:.0f} m from the launch point, bearing {brg:03.0f} ({compass}){moving}; "
                f"position ±{err:.0f} m" if err is not None else
                f"Target {dist:.0f} m from the launch point, bearing {brg:03.0f} ({compass}){moving}")
        if "lat" in rep:
            text += f"; {rep['lat']:.5f}, {rep['lon']:.5f}"
        self._inject("POSITION_REPORT", "info", now, text, track_id=subj.track_id, data=rep, ttl_ms=4500.0)

    def ack_alert(self, alert_id: str) -> Alert | None:
        with self.lock:
            a = self.arbiter.ack(alert_id, self.clock.now_ms())
            if a is not None:
                self.outbox.append({"type": "ack", "alert_id": alert_id, "ts_ms": a.acked_ts_ms})
                self._log_operator("ack", alert_kind=a.kind)
            return a

    def set_autonomy_mode(self, mode: str, source: str = "operator") -> str:
        with self.lock:
            if mode.upper() in ("FOLLOW", "HOLD"):
                problem = self._real_flight_problem(self.clock.now_ms())
                if problem:
                    raise ValueError(problem)
            self._log_operator("autonomy", mode=mode, source=source)
            return self.autonomy.set_mode(mode, self.clock.now_ms(), source)

    def start_incident(self, incident_id: str | None, consent: bool) -> IncidentState:
        with self.lock:
            if not consent:
                raise PermissionError("participant consent must be confirmed to start tracking")
            self.incident = IncidentState(incident_id=incident_id or f"INC-{int(time.time()) % 100000:05d}", active=True,
                                          started_ts_ms=self.clock.now_ms(), consent_confirmed=True,
                                          recording=self.incident.recording)
            return self.incident

    def stop_incident(self) -> IncidentState:
        with self.lock:
            for lk in self.locks.values():
                self.pending_lock_events.extend(lk.release(self.clock.now_ms(), "incident closed"))
                lk.gallery.clear()
            self.incident = IncidentState(incident_id=self.incident.incident_id, active=False)
            return self.incident

    # ===========================================================================================================
    # Snapshots
    # ===========================================================================================================
    def guardrails(self) -> list[Guardrail]:
        c = self.cfg
        g = [
            Guardrail(id="no_biometrics", label="No face recognition or biometric identity", status="enforced",
                      detail="Re-acquisition uses session-scoped clothing colour histograms, discarded at session end"),
            Guardrail(id="human_selects", label="Human selects the subject", status="enforced",
                      detail="Locks come only from an operator click or an operator-confirmed text match"),
            Guardrail(id="incident", label="Active incident required",
                      status="active" if self.incident.active else "warning",
                      detail=f"Incident {self.incident.incident_id}" if self.incident.active else "No active incident: locks disabled"),
            Guardrail(id="no_intent", label="No intent or threat inference", status="enforced",
                      detail="Alerts are kinematic facts (range, closing speed, time-to-contact) from validated geometry"),
            Guardrail(id="ai_bounded", label="AI chooses bounded options only", status="enforced",
                      detail="Rules own critical alerts, geofence, altitude and speed limits; models never emit velocities"),
            Guardrail(id="real_flight", label="Real-flight autonomy",
                      status="active" if c.autonomy.allow_real_flight else "enforced",
                      detail="Virtual-stick commands to a real aircraft are " + ("ENABLED" if c.autonomy.allow_real_flight else "disabled (simulator only)")),
            Guardrail(id="pilot_override", label="Pilot override", status="enforced",
                      detail="Any stick input exits virtual stick; 300 ms command TTL; 2 s link loss returns control"),
            Guardrail(id="crowd", label="No crowd monitoring", status="warning" if self.crowd else "enforced",
                      detail=f"More than {c.privacy.crowd_threshold} people disables subject locks"),
            Guardrail(id="retention", label="Retention off by default",
                      status="active" if self.incident.recording else "enforced",
                      detail="Recording only with consent; metadata audit log only otherwise"),
            Guardrail(id="images_cloud", label="Images to cloud models",
                      status="active" if (c.decisions.allow_images and c.privacy.send_images_to_cloud) else "enforced",
                      detail="Crop tiles sent (consented participants)" if c.decisions.allow_images else "Structured facts only; no images leave the Mac"),
        ]
        return g

    def tracks_snapshot(self, now: float) -> list[Track]:
        out = []
        roles = {lk.track_id: role for role, lk in self.locks.items() if lk.active and lk.track_id}
        cal_id = self.calib.calibration_id
        model = self.detector.model_id if self.detector else "none"
        geo = self.cfg.world.geo_reference
        for t in self.raw_tracks:
            if t.state == "TENTATIVE":
                continue
            kf = self.world_kf.get(t.track_id)
            fresh = kf is not None and now - kf.last_update_ms < self.cfg.fusion.track_ttl_ms and self.calib.valid
            xy = kf.position if (kf and fresh) else None
            vel = kf.velocity if (kf and fresh and kf.updates >= 5) else None
            lat = lon = None
            if xy and geo is not None:
                lat, lon = local_to_latlon(xy[0], xy[1], geo, self.cfg.world.north_offset_deg)
            trail = list(self.trails.get(t.track_id, []))[-120::2]
            out.append(Track(
                track_id=t.track_id, class_name=t.class_name, confidence=round(t.conf, 3), state=t.state,  # type: ignore[arg-type]
                visibility=t.visibility, source="drone", model_id=model,  # type: ignore[arg-type]
                box_xyxy_px=tuple(round(v, 1) for v in (t.measured_box if (t.matched_now and t.measured_box) else t.box)),  # type: ignore[arg-type]
                first_seen_ts_ms=t.first_ms, last_seen_ts_ms=t.last_ms, hits=t.hits,
                ground_xy_m=(round(xy[0], 2), round(xy[1], 2)) if xy else None,
                ground_error_m=round(self.track_ground_err.get(t.track_id, 0.0), 2) if xy else None,
                velocity_xy_mps=(round(vel[0], 2), round(vel[1], 2)) if vel else None,
                speed_mps=round(math.hypot(*vel), 2) if vel else None,
                heading_deg=round(heading_from_vector(*vel), 1) if vel and math.hypot(*vel) > 0.3 else None,
                velocity_valid=vel is not None, calibration_id=cal_id if xy else None, lat=lat, lon=lon,
                role=roles.get(t.track_id), trail=trail))  # type: ignore[arg-type]
        return out

    def snapshot(self, now: float) -> WorldState:
        with self.lock:
            self._state_seq += 1
            status = self._source_status(now)
            sources = []
            for src, c in self.counters.items():
                sources.append(SourceHealth(source_id=src, kind=src, status=status[src], provenance=c.provenance,  # type: ignore[arg-type]
                                            last_age_ms=round(self._age(src, now), 1) if self._age(src, now) is not None else None,
                                            fps=round(c.rate.rate(now), 1), received=c.received, processed=c.processed,
                                            dropped=c.dropped, reconnects=c.reconnects, detail=c.detail))
            det_status = "OK" if (self.detector and not isinstance(self.detector, NullDetector)) else "NOT_CONFIGURED"
            sources.append(SourceHealth(source_id="detector", kind="detector", status=det_status,  # type: ignore[arg-type]
                                        detail=self.detector.model_id if self.detector else "not started"))
            dstat = self.router.status()
            sources.append(SourceHealth(source_id="decisions", kind="decisions",
                                        status="DEGRADED" if dstat.ai_paused else "OK",
                                        detail=f"{dstat.active}" + (" (MOCK)" if dstat.active == "mock" else "")))
            prov_counts = [c.provenance for c in self.counters.values() if c.provenance]
            prov = "SYNTHETIC" if self.mode == "SIMULATED" else ("RECORDED" if self.mode == "REPLAY" else "LIVE")
            if prov_counts and all(p == prov_counts[0] for p in prov_counts):
                prov = prov_counts[0]
            notices = []
            if self.crowd:
                notices.append("Crowd scene: subject locks disabled (not supported)")
            if not self.incident.active:
                notices.append("No active incident: start one (with consent) to enable locks")
            if self.detector is not None and getattr(self.detector, "valid_on", "any") == "synthetic" and self._detector_prov != "SYNTHETIC":
                notices.append("Synthetic-only detector running on non-synthetic video")
            snap = self.metrics.snapshot(now)
            metrics_small = {"counters": {k: v for k, v in snap["counters"].items()},
                             "latencies": {k: v for k, v in snap["latencies"].items()},
                             "rates": {k: v for k, v in snap["rates"].items()}}
            ws = WorldState(
                session_id=self.session_id, seq=self._state_seq, server_ts_ms=now, mode=self.mode,  # type: ignore[arg-type]
                provenance=prov, mission_profile=self.profile, map_mode=self._map_mode(),  # type: ignore[arg-type]
                sources=sources, calibration=self._calibration_now(), registration=self.registration.status,
                drone=self.drone_state(now), phone=self._phone_summary(now, status.get("phone", "NOT_CONFIGURED")),
                responder=self.responder, tracks=self.tracks_snapshot(now), selected_track_id=self.selected_track_id,
                memory=self._memory_view(now), locks={k: v.snapshot(now) for k, v in self.locks.items()}, kinematics=self.kin_state,
                obstacles=[(o.xy_m[0], o.xy_m[1], round(now - o.ts_ms)) for o in self.obstacles if now - o.ts_ms < 8000][-220:],
                zones=self.zones,
                geofence=list(self.cfg.autonomy.geofence), autonomy=self.autonomy.state(), dispatch=self.dispatch_state,
                pursuit=self._pursuit_view(now), voice=self.voice.view(), crew_routing=self.crew.view(), decisions=dstat,
                alerts_active=self.arbiter.active_alerts()[:8], metrics=metrics_small,  # type: ignore[arg-type]
                incident=self.incident, guardrails=self.guardrails(), notices=notices, narration=self.narration)
            if self.sim_status is not None:
                from .schemas import SimStatus
                ws.sim = SimStatus(**self.sim_status, faults=self.faults.active())
            return ws

    def _pursuit_view(self, now: float) -> PursuitState | None:
        b = self.belief
        if b is None:
            last = self.pursuit_last
            if last is None:
                return None
            return PursuitState(active=False, ended=last["ended"], started_ms=last["started_ms"],
                                hypotheses=[PursuitHypothesis(id=h["id"], label=h["label"], weight=h["weight"])
                                            for h in last["hypotheses"]],
                                route=PursuitRoute(**last["route"]) if last.get("route") else None)
        w = b.hyp_weights()
        ts, cands = self.pursuit_candidates if self.pursuit_candidates else (None, [])
        keys = PursuitCandidate.model_fields.keys()
        desc = b.description
        return PursuitState(
            active=True, provenance=self._belief_provenance(), map_source=b.g.source,  # type: ignore[arg-type]
            started_ms=b.started_ms, lost_ms=b.lost_ms, lost_s=round((now - b.lost_ms) / 1000.0, 1),
            particles_per_hypothesis=b.n_per,
            hypotheses=[PursuitHypothesis(id=h.hid, label=h.label, object_id=h.object_id, track_id=h.track_id,
                                          weight=round(w[h.hid], 3), visible=h.visible,
                                          colors=list(h.colors) if h.colors else None, particles=int((b.k == i).sum()),
                                          evidence=[{k: v for k, v in e.items() if k != "active"} for e in h.evidence])
                        for i, h in enumerate(b.hyps)],
            particles=b.sample(), candidates=[PursuitCandidate(**{k: c[k] for k in keys if k in c}) for c in cands[:8]],
            candidates_ts_ms=ts, route=PursuitRoute(**self.pursuit_route) if self.pursuit_route else None,
            description={"text": desc["text"], "parsed": desc["descriptor"].text(), "likelihoods": desc["likelihoods"],
                         "by": desc["by"], "provenance": "manual"} if desc else None,
            dropped=list(b.dropped),
            holding="the lock re-bound to a track nobody has confirmed: the drone holds until a person decides"
            if self.autonomy.fsm == "VERIFY" else None)

    def extras(self, now: float) -> dict[str, Any]:
        """Low-rate extras: occupancy grid, motion label, describe proposal, cross-view, audit logs."""
        with self.lock:
            return {
                "type": "extras",
                "occupancy": {"png_b64": self.occupancy.encode_png_b64(), **self.occupancy.meta()} if self.occupancy.updates else None,
                "motion_label": self.motion_label,
                "describe_proposal": self.describe_proposal,
                "look_for": self.look_for,
                "cross_view": self._cross_view,
                "autonomy_audit": list(self.autonomy.audit)[-20:],
                "bridge_audit": list(getattr(self.bridge, "audit", []))[-20:],
                "bridge_control": {
                    "connected": bool(self.bridge and self.bridge.connected(now)),
                    "legacy": bool(getattr(self.bridge, "legacy", False)),
                    "capable": bool(getattr(self.bridge, "control_capable", False)),
                    "inhibited": bool(getattr(self.bridge, "control_inhibited", True)),
                    "motion_profile": getattr(self.bridge, "motion_profile", None),
                    "web_control_supported": bool(getattr(self.bridge, "web_control_supported", False)),
                    "pilot_reason": getattr(self.bridge, "pilot_reason", None),
                    "error": getattr(self.bridge, "control_error", None),
                } if self.mode == "LIVE" else None,
                "suppressed": list(self.arbiter.suppressed)[-20:],
                "alert_latency_ms": self.last_alert_latency_ms,
                "series": {k: v[-120:] for k, v in self.metrics.series_snapshot().items()},
            }

    def drain_outbox(self) -> list[dict]:
        with self.lock:
            out = list(self.outbox)
            self.outbox.clear()
            return out
