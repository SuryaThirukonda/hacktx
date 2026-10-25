# Eye in the Sky

Eye in the Sky combines a drone camera, an operator workspace and a responder phone in one incident. It detects and tracks people, retains a labelled last-known position through temporary loss, supports human review of assistance candidates, and delivers assessed guidance with acknowledgment. Simulation, live observations and predictions are kept distinct.

This repository contains the repaired `hacktx` platform plus the manually synchronized persistent-memory/SAR and Windows detector-tool work. The full feature inventory, repairs, validation evidence and remaining limits are in [6am.md](6am.md).

## Start locally

Python 3.12, Node.js and npm are required. The simulator works without aircraft or cloud keys. From the repository root:

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e './backend[dev]'
npm --prefix dashboard install
npm --prefix dashboard run build
PYTHONPATH=backend backend/.venv/bin/python -m uvicorn overwatch.main:app --host 127.0.0.1 --port 8000
```

With `uv` installed, `make setup`, `make build` and `make backend` provide the existing shortcuts. `make backend` binds the LAN interface; configure `OVERWATCH_TOKEN` before exposing it. Copy `.env.example` to `.env` only when configuring optional services or hardware. Secrets remain on the backend.

The City engine and scenarios that explicitly select YOLO need the `vision` extra and a checkpoint. `sim3d` enables the optional MuJoCo physics presets; `h264` enables DJI H.264 decoding:

```sh
backend/.venv/bin/python -m pip install -e './backend[dev,vision,sim3d,h264]'
```

For the local rules-only demonstration, including City's existing 30 FPS render workers:

```sh
PYTHONPATH=backend:. backend/.venv/bin/python backend/scripts/run_sim_demo.py --port 8012
```

Open `http://127.0.0.1:8012/dashboard`. This launcher supports multiprocessing safely. Simulation presentation targets 30 FPS while main detection retains its configured 15 FPS and City detection retains 5 Hz; actual rendering depends on machine load. 3D motion is smoothed between pose updates. Extra display frames do not advance detection freshness or change real-flight permissions.

## Pages

| Route | Purpose |
|---|---|
| `/`, `/dashboard` | Situation picker and simulation workspace: drone POV, shared 3D, survey map, callouts, review and diagnostics. In LIVE mode, `/dashboard` shows aircraft/incident controls. |
| `/command` | Full incident command: evidence, reviews, target memory, recommendations, alerts, calibration, replay and chat. |
| `/demo` | Classic demonstration and dispatch, phone-drive, tunnel and metrics tools. |
| `/ops` | Sensor, fusion and fault console; focused drone/phone/map views remain available. |
| `/responder` | Responder briefs, voice, GPS and acknowledgment. |
| `/responder/sensors` | Phone sensor/proximity view. |
| `/drive` | Phone control of a simulated person. |
| `/3d` | Dedicated simulator decision/physics view. |
| `/test12` | Standalone City development view. |

## Implemented capabilities

- **Perception and positioning:** optional YOLO, synthetic pixel detection, high/low association tracking, camera-motion compensation, measurement-quality checks, ArUco/telemetry/manual ground calibration, metric uncertainty and registered phone observations.
- **Tracking and memory:** operator subject/responder locks, clothing galleries, persistent logical subject IDs, separately labelled measured/predicted/last-seen positions, conservative reattachment, rejected tracks, session/incident isolation, history and event replay.
- **Pursuit:** motion-aware object memory, street-graph particle belief, cover-aware negative evidence, safe viewpoint scoring, bounded route decisions, operator clothing descriptions and rejection of wrong rebindings. City retains its isolated detector, police pings and reports; loss recovery progresses through a bounded scan, cover orbit and road search.
- **Search and rescue:** interpreted hazard evidence, continuous clothing acquisition, human confirmation/rejection, observation-linked beacons, simulated responder position, assessed access/approach routes, alert delivery and idempotent ACK.
- **Coverage visualization:** camera footprints and a fading blue grid of observed ground. Roof/canopy occlusion leaves cells unsurveyed. Coverage is display history, never proof that an area is clear or safe.
- **Mission tools:** Search, Follow, Structure and Indoor; structure sides/openings, smoke uncertainty, scene meshes/point clouds/splats, frame alignment, map markers and review queues.
- **Decision and voice services:** deterministic rules with optional OpenAI, TypeSafe/Jev and Strands adapters; confidence, freshness and budget gates; D13 voice/text intents, grounded entities, ambiguity questions, approval, optional ElevenLabs/Gemini and browser speech fallback.
- **Devices and control:** native DJI bridge and ARKit/LiDAR responder source, live JPEG/H.264 or URL video, manual/keyboard/camera/visual-follow profiles, explicit physical permission, pilot override, command epochs and bridge watchdogs. Native DJI code was preserved during this sync.
- **Persistence and evaluation:** local SQLite/event history, optional Tiger Data mirroring, replay, recording, deterministic scenarios, detector/tiling benchmarks and offline small-person scoring.

## Rescue walkthroughs

Select Flood rescue → **FR3**, Firefighter routing → **FF3**, or Search and rescue → **SR5**, then launch. The simulated drone takes off from its pad and searches the reported area. After the detector finds a supported clothing match, it places a pending beacon and holds visual contact.

Scroll to **Evidence and operator decisions**, inspect the assistance card and choose **Confirm & send guidance**. The simulated crew moves only after approval and uses assessed ground. Open `/responder` to acknowledge the alert. Rejecting a pending candidate produces no crew dispatch. The drone holds after the crew reaches the assessed approach within location uncertainty, then returns to base. Stop/pilot override pauses the rescue; restart explicitly to resume it.

The original FR1/FR2, FF1/FF2, SR1–SR4 and pursuit scenarios remain available. Their existing behavior is retained alongside the added base-launch variants.

For a compact deterministic case in which the target is inside flood water and guidance stops at dry ground:

```sh
make sar-demo
```

Open `http://127.0.0.1:8011/command` for the review workflow, or `/dashboard` for the situation workspace. The responder page is `/responder`. This helper uses synthetic sensors, rules, a separate local database and no physical-flight permission.

## Quick demo check

Use the FR3 walkthrough above as the normal check: its person is deliberately placed in an accessible, known location. Launch, confirm the measured beacon, watch the crew approach and acknowledge the alert. The new map should show the blue camera-coverage grid, beacon and assessed route. There is no need to run seed sweeps or a full scenario matrix for this demo.

`make build` includes TypeScript checking. Existing backend and browser suites remain available as optional developer tools when relevant code changes; they are not required before each demo. Recorded results and their exact scope are in [6am.md](6am.md). Browser runners use Chrome by default; set `E2E_CHROME` for another compatible Chromium executable.

## Detector configuration

The configured default is **YOLO26n, image size 960, confidence 0.25**, with automatic CPU/MPS/CUDA device selection. Synthetic frames use the simulator detector when `detector: auto`. The existing tabletop YAML can explicitly select YOLO26s at 960. Check configuration/environment overrides and the reported loaded model ID; checkpoint fallback is retained.

The Windows additions fix checkpoint basename metadata and support UTF-8 benchmark reports outside the checkout. `backend/scripts/small_target_sweep.py` provides frozen-manifest, ignore-aware aerial person scoring and a CUDA sweep. Scoring utilities can be imported without GPU dependencies; CUDA inference requires the vision dependencies, dataset, manifest and weights on a CUDA host.

There is **no automatic live Search/Follow model switch**. YOLO26s at 1280 remains a candidate profile that needs representative Mac footage and performance validation.

## Live hardware and limits

Use `make backend-live` or the existing `make drone-live` launcher for real device protocols. Native setup and operating guidance remain in [HARDWARE_INTEGRATION.md](HARDWARE_INTEGRATION.md), [docs/DRONE_LIVE_INTEGRATION.md](docs/DRONE_LIVE_INTEGRATION.md) and [docs/operating_guide.md](docs/operating_guide.md).

Start with [DRONE_V1_HANDOFF.md](DRONE_V1_HANDOFF.md) for the retained SDK sample/website workflow and current validation boundaries; older integration reports describe earlier build evidence.

Real-flight permission starts off. A current DJI stream, supported/re-armed bridge, fresh telemetry, local SDK permission and explicit target confirmation remain prerequisites. Marking a person does not start follow. Stop, reconnect, stale data and a changed incident invalidate relevant control state.

Hazard maps, rescue movement, coverage and beacons in these demonstrations are simulated. Clothing continuity is not identity verification; a candidate is not a medical finding. Routes are bounded assessments and may return blocked or unavailable. Real DJI flight, real rescue navigation, real LiDAR accuracy, Mac detector accuracy and Windows CUDA performance on this Mac are not established by simulator tests.
