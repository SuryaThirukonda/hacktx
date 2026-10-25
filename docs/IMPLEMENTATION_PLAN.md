# Overwatch — Unified Implementation Plan

**Merges:** *Rescue Copilot: End-to-End Implementation, Simulation, Dashboard, and Model Evaluation Plan* and
*Overwatch: First Responder Aerial Copilot Implementation Plan* (both dated ).
**Status of this document:** the build plan that this repository implements. Section 9 tracks what is
implemented, what is simulated, and what still needs real hardware or API keys.

---

## 1. Product in one sentence

**Overwatch fuses a drone's overhead view and a responder's phone (camera + LiDAR) into one calibrated local
scene, keeps a human-selected subject in view, and delivers responder-relative, evidence-backed alerts
("Subject 2 o'clock, 12 meters, closing 3 meters per second") to the responder's phone — with every metric claim
gated on calibration validity and data freshness.**

From Overwatch we keep: subject lock + re-acquisition, responder-relative geometry (range, clock bearing,
closing speed, time-to-contact), body-delivered alerts (haptics + voice), the decision-model layer (OpenAI
Decisions primary, Jev and Strands peers), simulator-driven autonomy (IBVS follow + reacquire FSM + safety
supervisor), narration, and the hybrid live demo.

From Rescue Copilot we keep: dual-source ingest with independent health, a *genuine* fused local metric map with
provenance and uncertainty, calibration with **held-out** reprojection error and quality gates, truthful
DEGRADED/UNKNOWN states, deterministic local fallbacks, one execution graph for live/replay/simulation,
recording + deterministic replay, and an evaluation harness that never reports unmeasured numbers.

## 2. Conflict resolutions between the two plans

| Topic | Overwatch plan | Rescue Copilot plan | Unified decision |
|---|---|---|---|
| Autonomous flight | IBVS follow via DJI virtual stick | excluded | Full autonomy stack runs against the **simulated drone** (SIL) and the **DJI simulator** (HIL, props off). Real-flight virtual stick requires `autonomy.allow_real_flight: true` **and** operator arming; pilot stick input always overrides. Decision models only pick bounded primitives, never raw velocities. |
| Subject framing | "POI", threat logic | neutral, no intent | "Subject" + kinematic facts only (range, closing speed, TTC). No hostility/intent labels; the D4 motion label is restricted to observable motion classes. Severity = `info / attention / warning / critical` + `sensor_fault` + `local_proximity`. |
| Identity | ReID gallery rebind | ephemeral IDs, no unverified identity | Session-scoped appearance gallery (color histograms; optional ReID embeddings), "never switch when unsure", rebinds labelled `REACQUIRED (UNVERIFIED)` unless confirmed by D2 ≥ 0.75 or the operator. No face recognition. Gallery discarded at session end. |
| Map | MapLibre + satellite | local metric map | Local metric ENU map (meters) as primary; the simulator supplies an orthophoto-like background; lat/lon readouts when a geo reference is configured. |
| Decision cadence | 2–4 Hz tactical | event-driven ≤ 1–2 Hz | Event-driven with per-task rate caps (≤ 3 Hz), scene-fingerprint dedupe, one in flight per task, stale-after 1.5 s. Urgent alerts are never waiting on the cloud. |
| Voice | ElevenLabs + Gemini | AVSpeech/local | Local speech always works (browser `speechSynthesis`, iOS `AVSpeechSynthesizer`); ElevenLabs TTS and Gemini summaries are optional adapters behind keys. |
| Video transport | MediaMTX/WebRTC | MJPEG/WS MVP | WebSocket binary JPEG with **frame-exact overlay headers** (detections that belong to that exact frame). MJPEG endpoints for embedding. RTSP/RTMP (e.g. MediaMTX) ingest via OpenCV URL source. |
| Tracker | Ultralytics BoT-SORT + CMC | ByteTrack/BoT-SORT | In-repo ByteTrack-style tracker with sparse-optical-flow camera-motion compensation and explicit lifecycle (`tentative → confirmed → lost → expired`, tracker epochs). Ultralytics tracking usable when YOLO is installed. |
| Calibration | telemetry ray projection | ArUco homography + holdout | Calibration manager picks the best *valid* source: (1) fresh ArUco homography with holdout error, (2) homography chained through camera-motion estimates (age-limited), (3) telemetry pose projection (larger error bound). Otherwise metric claims are suppressed. |
| Simulation | MuJoCo + noise bridge | synthetic fixtures S01–S20 | In-repo OpenCV 3D renderer of a synthetic field (markers, trees, cars, people with walking animation, shadows), simulated drone dynamics with wind/GPS noise, synthetic ARKit depth; noise bridge (latency, drops, wobble, blur, low light). Scenario suites S01–S20 (S18/S19 as integration tests) + OW1–OW6. |

## 3. Architecture

```
            ┌──────────────────────── sources (same contract in LIVE / REPLAY / SIMULATED) ────────────────────────┐
DJI bridge  │ BridgeWS (JPEG/H.264 + telemetry) │ OpenCV URL (RTSP/RTMP/webcam/file) │ Replay (MP4+JSONL) │ SimWorld │
Phone       │ PhoneWS (ARKit summary + JPEG)                                       │ Replay              │ SimPhone │
            └───────────────┬──────────────────────────────────────────────────────────────────┬────────────────┘
                            ▼                                                                  ▼
         Perception: detector (sim-blob │ YOLO │ RF-DETR) → CMC → tracker      Phone: depth sectors, obstacles, pose
                            ▼                                                                  ▼
         Calibration manager (ArUco/holdout │ chained │ telemetry) → ground projection   Registration (ARKit→local)
                            └──────────────────────────────┬───────────────────────────────────┘
                                                           ▼
                     Fusion → WorldState (tracks, responder, obstacles, occupancy, footprint, map mode)
                                                           ▼
                     Locks (subject / responder) → Kalman kinematics → range, clock bearing, closing, TTC
                                                           ▼
             Deterministic rules → events ──► Alert arbiter ◄── Decision router (OpenAI │ Jev │ Strands │ rules)
                                                           ▼
             Autonomy (IBVS + reacquire FSM + safety supervisor) → virtual-stick commands → bridge (sim/real)
                                                           ▼
             Publish (/ws/state, /ws/events, /ws/alerts, /ws/video/*) · Recorder (JSONL + MP4) · Metrics
```

* One synchronous `Pipeline` object owns all scene state; sources, the simulator and API handlers call into it.
  Heavy pure work (rendering, detection) runs outside the state lock.
* The same `Pipeline` drives the real-time server and the headless scenario runner (sim clock, no pacing), so
  evaluation exercises production code.

## 4. Repository layout

```
backend/overwatch/   FastAPI app + pipeline (python package)
  api/               REST + WebSocket + MJPEG endpoints
  sim/               synthetic world, renderer, actors, drone dynamics, phone sim, noise, scenarios
  sources/           frame/phone/telemetry sources (synthetic, bridge WS, phone WS, OpenCV URL, file, replay)
  vision/            detectors, tracker, camera-motion compensation, ArUco markers, appearance features
  geometry/          camera model, homography + holdout, telemetry projection, calibration manager, phone depth,
                     registration, occupancy grid, Kalman filters, kinematics, fusion, geo conversions
  tracking/          subject/responder locks with gallery + reacquire state
  events/            rule engine, alert arbiter, narration templates
  decisions/         unified question/answer types, OpenAI Decisions, TypeSafe Jev, Strands, mock, rules, router, D1–D5
  autonomy/          PID, IBVS follow, reacquire FSM, safety supervisor, virtual-stick mapping
  voice/             ElevenLabs TTS + Gemini summary adapters (optional)
  replay/            session recorder + player
  evaluation/        metrics registry, ground-truth evaluation, headless scenario runner
backend/tests/       pytest unit + integration tests
backend/scripts/     run_scenarios, benchmark_models, benchmark_decisions, fake_devices, make_markers, export_schemas
dashboard/           React 19 + Vite + TypeScript + Tailwind operations console and /responder phone page
ios/                 native Swift apps: OverwatchResponder (ARKit LiDAR, haptics, voice), OverwatchDroneBridge (DJI MSDK)
config/              default.yaml, tabletop.yaml
docs/                architecture, contracts, calibration, operating guide, guardrails, decision engines, evaluation, demo script
evaluation/          generated reports
```

## 5. Interfaces

All wire messages carry `schema_version`, `session_id`, `seq`, `source_id` (where applicable) and timestamps
(`capture_ts_ms` in source clock, `server_ts_ms` in server clock). Full schemas: `docs/contracts.md` and
`backend/overwatch/schemas.py` (exported to `schemas/*.json`).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | server vs. per-sensor health, versions |
| `GET /api/config` | active config without secrets |
| `POST /api/session` | start session (`SIMULATED`, `REPLAY`, `LIVE`), scenario, incident ID |
| `GET /api/tracks`, `GET /api/tracks/{id}` | tracks + history/evidence |
| `POST /api/tracks/{id}/select`, `POST /api/locks/subject`, `POST /api/locks/responder`, `DELETE /api/locks/{role}` | operator selection and locks (human-in-the-loop) |
| `POST /api/locks/describe` | text-described subject selection (D3), always requires operator confirmation |
| `POST /api/calibration/ground-points`, `GET /api/calibration/status`, `POST /api/registration/manual` | manual calibration and phone registration |
| `POST /api/autonomy/mode`, `POST /api/autonomy/estop`, `POST /api/autonomy/geofence` | autonomy control |
| `POST /api/decisions/engine` | choose primary/shadow engines |
| `POST /api/alerts/ack` | acknowledge |
| `POST /api/simulation/config`, `POST /api/simulation/control` | fault injection, scenario, pause/speed |
| `GET/POST /api/recordings`, `POST /api/replay/control` | recording and replay |
| `GET /api/metrics`, `GET /api/evaluation/latest` | metrics with denominators, last evaluation report |
| `GET /streams/{drone,phone}.mjpg` | annotated MJPEG |
| `WS /ws/state` | 10 Hz world state |
| `WS /ws/events` | ordered events, decisions, alerts, transcript |
| `WS /ws/video/{drone,phone}` | binary JPEG + JSON header with frame-exact overlays |
| `WS /ws/alerts` | responder alert channel (web + native) |
| `WS /ws/phone` | native responder app sensor uplink (ARKit summary, pose, heading, JPEG) |
| `WS /ws/bridge` | DJI bridge app: video + telemetry up, virtual-stick/gimbal/mode commands down |

## 6. Key algorithms (implemented)

* **Detection.** `SimBlobDetector` (classical HSV segmentation on rendered pixels, honest stand-in valid only on
  synthetic imagery), `UltralyticsDetector` (YOLO26/YOLO11 weights, explicit `name → class` map),
  `RFDETRDetector` (optional). Detector choice is recorded in every track (`model_id`).
* **Tracking.** Kalman box tracker, two-stage IoU association (high/low confidence), CMC homography from
  Shi-Tomasi + pyramidal LK outside detections with RANSAC, lifecycle states, epoch-tagged IDs.
* **Calibration.** ArUco 4×4 markers at surveyed world positions; RANSAC homography on fit markers, RMSE on
  **held-out** markers in meters; gates: ≥4 markers, spread, holdout RMSE ≤ threshold, age ≤ TTL.
* **Telemetry projection.** Pixel → camera ray → world via yaw/gimbal pitch → intersect ground (z=0); camera
  footprint polygon from image corners; error bound grows with altitude and attitude uncertainty.
* **Phone depth.** Reject invalid/low-confidence depth, estimate floor height, 3D corridor ROI, azimuth sectors,
  robust percentile range, persistence + hysteresis; obstacle points for the occupancy grid.
* **Registration.** 2D rigid Kabsch alignment between ARKit trajectory and drone-observed responder track (auto),
  compass + single point (assisted), manual two-point (low confidence). Residual gates.
* **Kinematics.** Constant-acceleration Kalman per locked entity in the local frame; range, closing speed by
  regression over a 0.8 s window, TTC, alignment cos θ, relative bearing → clock position.
* **Rules.** `CLOSING_FAST` (critical), `APPROACH` (warning), `POSITION_CALLOUT` (info), `TRACK_LOST`/`REACQUIRED`,
  `ZONE_ENTRY`, `LIDAR_PROXIMITY` (local, cloud-independent), sensor faults. Hysteresis + cooldowns.
* **Arbiter.** Safety override → freshness/TTL → dedupe/cooldown → rate limit → optional AI prioritisation (D5).
* **Decisions.** Unified `Question/Answer` model translated to OpenAI Decisions (`POST /v1/decisions`, list of
  `{type,name,instructions,choices|levels}` → `answers[]`), TypeSafe Jev (`POST /v1/systemone`, `questions{}` with
  `noul/choice/score`, `criteria`), Strands Decider (local `/v1/systemone`), rules, mock (clearly labelled).
  Router: deadline, fallback chain, shadow engines for comparison, JSONL log. Tasks: D1 reacquire strategy, D2
  identity confirmation (crop tile), D3 text-described selection, D4 motion label (observable classes only), D5
  alert priority / notification choice.
* **Autonomy.** IBVS yaw from pixel error, forward from ground range vs standoff, velocity feed-forward,
  altitude hold, gimbal tilt to centre target; reacquire FSM `TRACKING → COAST → REACQUIRE → HOLD`; safety
  supervisor (geofence, altitude floor/ceiling, speed caps, battery, link watchdog, people-over, E-STOP, audit).

## 7. Milestones (each is a commit)

| # | Milestone | Done when | Status |
|---|---|---|---|
| M0 | Plan, repo, scaffolding | this file, tooling, configs | done (95e97bb) |
| M1 | Backend core + synthetic world | renderer produces drone/phone frames; FastAPI serves health + video | done (95e97bb) |
| M2 | Perception + calibration | detections/tracks on sim frames; ArUco holdout calibration; unit tests | done (441ab5c) |
| M3 | Fusion, locks, kinematics, events, alerts | world state with metric tracks; alerts fire on scripted approach | done (441ab5c) |
| M4 | Decision layer | engines + router + D1–D5 with mock/rules offline; adapters tested against recorded shapes | done (441ab5c); real engines untested without keys |
| M5 | Autonomy | follow + reacquire in closed-loop SIL; safety unit tests (axis mapping, geofence, watchdog) | done (441ab5c) |
| M6 | Dashboard | all panels live against the simulator; visual tests at desktop + mobile widths | done (0c1ae24) |
| M7 | Responder clients | `/responder` web app; native iOS Responder + DJI Bridge sources | web done (0c1ae24); iOS sources written (db10e2b), not compiled |
| M8 | Replay, faults, evaluation | recorder/player, fault injection, scenario runner + reports | done (68cd868, 9b9a7f2); S09/S13/S16 added (7005eff, b1312a6, 86b77fa) |
| M9 | Voice + guardrails + polish | narration, TTS adapters, guardrail/privacy panels | done (9b9a7f2, 6599dcb) |
| M10 | Docs + final QA | operating guide, demo script, key setup, full visual pass | done (this commit) |

## 8. Testing strategy

* **Unit (pytest):** camera/homography math (nadir centre → drone ground point, oblique centre 20 m ahead at −45°
  and 20 m), holdout error, closing speed, TTC, clock mapping, hysteresis, virtual-stick axis mapping, geofence
  clamp, watchdog, router timeout fallback, decision answer parsing for all engines, depth sector floor rejection,
  registration recovery of a known transform.
* **Integration:** headless scenario runs through the full pipeline with GT comparison (detection P/R, ID
  switches, projection error, event precision/recall, alert latency in sim time, autonomy time-in-frame,
  geofence violations); replay determinism (S18); new session resets tracker IDs (S19).
* **Live-path tests without hardware:** `scripts/fake_devices.py` streams into `/ws/bridge` and
  `/ws/phone` exactly like the native apps.
* **Visual:** browser checks of every dashboard panel in nominal and degraded states (drone offline, phone offline,
  calibration lost, AI paused, replay) at 1440×900 and phone width.
* **Labelled honesty:** every report states `synthetic`, `replayed real`, or `live`; models that did not run are
  `NOT RUN`, never zero.

## 9. Real vs simulated (kept current in README)

| Capability | In this repo (tested how) | Needs from you |
|---|---|---|
| Drone video | simulator; bridge WebSocket (H.264/JPEG) and OpenCV URL ingest; tested with `scripts/fake_devices.py` over the real protocol | build `ios/OverwatchDroneBridge`, or point `OVERWATCH_DRONE_URL` at your existing bridge / MediaMTX |
| Drone telemetry + virtual stick | simulator + bridge protocol with phone-side watchdogs; closed-loop FOLLOW tested against the simulated aircraft | DJI App Key; first runs in the DJI simulator (props off) |
| Responder LiDAR/pose/vision | synthetic ARKit depth through the same algorithm as the Swift port (parity on 121 frames); portrait/landscape (S13) | build `ios/OverwatchResponder` on a LiDAR iPhone |
| Detection | sim detector on synthetic frames; YOLO26 n/s run on CPU (real photos, composite small-target test); tiled inference | real footage + labels to measure accuracy |
| OpenAI Decisions / Jev / Strands | OpenAI verified live (p50 ~185 ms; designed cases and S16 in the loop); Jev and Strands adapters only | `TYPESAFE_API_KEY`, local Strands server |
| ElevenLabs / Gemini | ElevenLabs verified live with account fallback; Gemini adapter only; browser and iOS speech always work | `GEMINI_API_KEY`; a valid second ElevenLabs key |
| 3D decision view | MuJoCo Skydio X2 rigid body flown by the autonomy stack in OW7, three.js view of decisions | - |
| iOS apps | ~6,000 lines of Swift; wire keys checked against the pydantic models | Xcode build, the VERIFY items in `ios/README.md` |
