# Architecture

## One execution graph

Every mode — `SIMULATED`, `LIVE`, `REPLAY` — feeds the same `Pipeline` object
(`backend/overwatch/pipeline.py`). Only the *sources* change:

| Mode | Drone frames + telemetry | Responder phone | Clock |
|---|---|---|---|
| SIMULATED | `sim/driver.py` renders the field, flies a simulated Mini 2 | `sim/phone_sim.py` (ARKit-style pose, z-buffer LiDAR) | `SimClock` (pausable, speed 0.25–4×) |
| LIVE | `/ws/bridge` (DJI bridge iPhone: JPEG or raw H.264 + telemetry) or any OpenCV URL (RTSP/RTMP/webcam/file) | `/ws/phone` (native app) | `WallClock` + per-device NTP-style offset |
| REPLAY | `sessions/<name>/drone.mp4` + JSONL | `phone_observations.jsonl` + `phone.mp4` | `SimClock` driven by recorded timestamps |

```
 sources ──► process_drone_frame ─► detect ─► camera-motion comp. ─► track ─► calibrate ─► project ─► locks
         ──► process_phone ───────► ARKit pose ─► registration pairs ─► occupancy / obstacles
         ──► process_telemetry ───► drone pose (GPS→local + learned bias)
 tick 10 Hz ─► responder pose ─► kinematics ─► rules ─► arbiter (+ D5) ─► alerts ─► WebSockets / phone
             └─► decision jobs D1 (reacquire) · D2 (identity) · D4 (motion label) ─► DecisionService
 autonomy 20 Hz ─► IBVS / reacquire FSM ─► safety supervisor ─► virtual stick ─► bridge (sim or real)
```

## Threads and locks

* The asyncio event loop only does I/O (FastAPI, WebSockets, decision HTTP calls).
* A 3-worker thread pool runs simulation steps, pipeline calls, state snapshots and JPEG encoding.
* All scene state lives in `Pipeline` behind one re-entrant lock. Detection, camera-motion estimation and marker
  detection are pure functions of the frame and run *outside* the lock; calibration has its own lock.
* Decision calls are asynchronous: the pipeline emits a `DecisionJob`, the `DecisionService` gates it (one in flight
  per task, per-task rate cap, scene-fingerprint dedupe), the router calls engines with a hard deadline, and the
  result comes back through `Pipeline.apply_decision`. Urgent alerts never wait on this path.

## Clocks and timestamps

* Every packet carries the source's capture timestamp converted to the pipeline clock; arrival time is used (and
  labelled `timestamp_quality: arrival`) only until a device clock offset has been estimated.
* Freshness gates use those timestamps: stale drone video invalidates calibration claims, track positions and
  kinematics; a stale phone stops proximity claims; telemetry older than 1 s is not used for projection.
* In SIMULATED and REPLAY the ticks run on the simulated/replayed clock, so pausing, speeding up and seeking are
  exact and evaluation is deterministic.

## Geometry chain

1. **Detection** → boxes. The bottom-centre of a box is the ground-contact estimate (σ ≈ 0.35 m, larger at nadir).
2. **Calibration** (`geometry/calibration.py`) picks the best valid image→ground mapping per frame:
   markers (holdout-verified) → chained through camera-motion homographies (drift-bounded) → telemetry ray
   projection (GPS bias learned while markers were visible) → none.
3. **Per-track ground filters** (constant velocity) give positions, speeds and headings with error bounds.
4. **Locks** follow human-selected tracks; constant-acceleration filters per locked entity.
5. **Registration** aligns the phone's ARKit frame to the map using the responder's own trajectory as seen by the
   drone (2D Kabsch, residual-gated); it lets the phone's position and LiDAR points join the map.
6. **Kinematics** (`geometry/kinematics.py`): range, clock bearing (relative to the responder's heading),
   subject-induced closing speed, time to contact, all with propagated uncertainty and validity reasons.
7. **Cross-view labels** (`pipeline._phone_overlay`): each on-device phone box gets a bearing from the upright
   image's field of view (`geometry/phone_view.py`, orientation-aware) and is matched to a currently observed drone
   track by bearing (+ LiDAR range when available). Only with the responder in the drone's map frame (drone lock or
   ARKit registration), never on a runner-up within 3°, one box per track; edge-clipped boxes are gated one-sided.
   The result labels the phone overlay ("= #12 2.6 m") and drives nothing else.

## Wire protocol

See `docs/contracts.md`. Pydantic models in `backend/overwatch/schemas.py` are the source of truth;
`scripts/export_schemas.py` writes JSON Schemas to `schemas/`.

## Module map

| Path | Responsibility |
|---|---|
| `api/app.py` | REST, WebSockets, MJPEG, serves the built dashboard |
| `runtime.py` | owns pipeline + sources, sim/replay scheduling, pub/sub fan-out, recording |
| `pipeline.py` | the execution graph and all scene state |
| `vision/` | detectors (sim-blob, YOLO, RF-DETR), tracker, CMC, ArUco, appearance |
| `geometry/` | camera, homography+holdout, calibration manager, phone depth, registration, Kalman, kinematics, occupancy |
| `tracking/lock.py` | subject/responder locks, handover, gallery re-acquisition, crossing guard |
| `events/` | rules, alert arbiter, narration templates |
| `decisions/` | engines (OpenAI Decisions, TypeSafe Jev, Strands, mock, rules), router, services, D1–D5 |
| `autonomy/` | IBVS follow, search primitives, safety supervisor, DJI axis mapping |
| `sim/` | world, texture, renderer, actors, drone dynamics, sim bridge, phone sim, scenarios, driver |
| `replay/` | recorder, player |
| `evaluation/` | metrics registry, headless runner, ground-truth evaluation |
| `voice/` | ElevenLabs TTS, Gemini narrator |
