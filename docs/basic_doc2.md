# Repo audit basic_doc2 (, 01:30)

What is in the Overwatch repository right now, what works, what was verified and how, what is stale, and what
comes next.

| | |
|---|---|
| Repository | `txhack_init_claude` (Overwatch), branch `main` |
| Commit | `ef7c29d` "test12 done" (Surya,  01:08), in sync with `origin/main` |
| Working tree | clean |
| Other worktree | `/home/surya/repos/txhack_hazards_3d`, branch `feat/fire-water-3d` at `a945754`, with **uncommitted** changes in 11 files (richer 3D flood and fire visuals) |
| Other branches | `origin/drone-v1` (contributor, ): 2 commits not on `main` ("Add Drone V1 drone movement controls and teammate handoff", "Show movement keyboard mapping before aircraft arming"), 9 commits behind it; not reviewed here |
| History | 57 commits,  02:56 to  01:08 |
| Method | A functionality audit from reading the code, the docs and the git history. **No tests or scenario runs were made for this audit.** Results quoted in section 5 come from the committed evaluation reports and from the test12 runs made earlier on  while building it. |
| Supersedes | [FUNCTIONALITY_AUDIT.md](FUNCTIONALITY_AUDIT.md) (, taken at `a945754`; still accurate on most subsystems, but predates test12, E7 and the Skydio phase A removal) |
| Related | [TEST12_MERGE_PLAN.md](TEST12_MERGE_PLAN.md): the agreed plan to merge test12 into the main platform |

---

## 1. In one paragraph

Overwatch is a HackTX project for drone-assisted incident response. A DJI Mini 2 (simulated here) finds and follows
a person, works out where they are on the ground, and tells a responder in plain words ("11 o'clock, about 18 m").

It has two parts that run side by side:
- **The main platform** (`backend/overwatch` + `dashboard`): an incident-command system with a 30 Hz simulator over six worlds (50 registered scenarios), perception and tracking, autonomous dispatch and follow, a bounded decision layer (D1 to D13, rules-first), incidents with persistence and replay, voice, a Tiger Data mirror, a phone joystick over a Cloudflare tunnel, a 3D view, and native iOS apps that are written but not built.
- **test12** (`test12/`): a self-contained police drone dispatch at `/test12`. It has its own engine, YOLO26n on its own rendered video at 30 fps, belief-based street search, follow, a 360° scan, a police car that pings its position, spoken callouts relative to the car, and three video views.

Everything is simulated and labelled so. The real-hardware paths (DJI bridge, responder phone, real video) are
written but not tested on hardware.

---

## 2. Size

| Area | Files | Lines |
|---|---|---|
| `backend/overwatch/` (Python) | 121 | 23,466 |
| `backend/tests/` | 35 test files | 3,194 (186 test functions) |
| `backend/scripts/` | | 1,482 |
| `test12/` | 18 Python + 3 web | 4,369 Python + 831 HTML/JS/CSS |
| `dashboard/src/` (TypeScript/React) | 44 | 9,741 |
| `ios/` (Swift) | 29 | 5,613 |
| `docs/` | 13 Markdown + report and pitch folders | |

---

## 3. History

| When (2026) | What | Commits |
|---|---|---|
|  02:56 to 05:41 | M0 to M10: plan, config, wire contracts, synthetic world simulator; perception, calibration, fusion, locks, rules, decisions, autonomy, runtime and API; ops console and responder page; recording and replay; benchmarks; iOS apps written | `95e97bb` … `5eefffa` |
|  11:38 to 19:06 | OpenAI Decisions verified live, ElevenLabs with account fallback, MuJoCo Skydio X2 and OW7, 3D decision view, status report v2, dashboard views as pages | `e79bb43` … `9015f3d` |
|  02:54 to 06:39 | Pitch and plan v2; Phases 0 to 17 (incident model and store, API, scene spec, video sources, dashboard, responder PWA, tracking, Search and Structure modes, calibration, recommendations, logging and replay, scene viewer, Follow mode, four worlds, aircraft profiles, near-IR, end-to-end demo) | `ce8eb1d` … `f04e0ab` |
|  12:42 to 17:55 | E1 to E6: larger worlds and object memory; autonomous pursuit; demo page at `/`; demo controls, dispatch, jointed people, rules-first pursuit, jev-drone stack; chases from the pad, city world, phone controller over a tunnel; Tiger Data mirror | `a7ea667` … `888d3a2` |
|  22:42 | test12, plus Skydio phase A: X2 aircraft profile and PX4 cross-check removed | `055f3e0` |
|  01:08 | "test12 done": test12's long chase, views, spawn, pings, callouts and voice; E7 (belief-based pursuit routing, live crew routing, voice commands, scenario stories); the  audit; the test12 evaluation | `ef7c29d` |

---

## 4. What is implemented

Status words used below:
- **Working**: runs in the simulator and was checked (how is stated).
- **Partial**: works with stated gaps.
- **Written**: code exists but has not been run or built here.
- **Not built**: absent.

### 4.1 Runtime and simulator — Working

- `runtime.py`:
  - one 30 Hz simulator step (`SimulationDriver`), the pipeline at 10 Hz, autonomy at 20 Hz;
  - `WorldState` at 10 Hz;
  - drone video 960 × 540 at 15 fps (`video.target_fps: 15`) over `/ws/video/drone`;
  - phone and telemetry packets at 10 Hz;
  - `SimClock` makes runs exact and deterministic: pause, 2× and 4×, frame steps.
- Scenarios switch with `rt.control("restart", id)` (`POST /api/simulation/control`).
- The scripted operator (`OperatorEmulator`) clicks the same way headless and live.
- `sim/render.py`: a CPU pinhole renderer.
  - Ground mipmaps warped by homography, sun shadows, box faces, trees, 15-part jointed people.
  - An id buffer gives pixel-exact ground-truth visibility.
- Aircraft: the DJI Mini 2 stand-in, a kinematic first-order model with spec-sheet values, Dryden wind and an energy battery. The MuJoCo Skydio X2 model is still in the tree (see 6.1).

### 4.2 Worlds and scenarios — Working

50 scenarios are registered (`sim/scenarios.py`, counted from the registry):

| World | Module | Size | Scenarios | Notes |
|---|---|---|---|---|
| Training area A | `sim/world.py`, `worlds/training.py` | 65 × 44 m | 27: demo, DEMO-1, S01 to S17, S20, OW1 to OW7 | ArUco markers, a building with sides A to D. The demo page shows DEMO-1, and lists OW1 to OW7 under Police pursuit. |
| Police pursuit | `worlds/pursuit.py` | 400 × 300 m | 12: PP0 to PP8, PJ1, PJ2, VC1 | 3 × 3 street grid as map data, the station pad, a 56 m covered walkway (PP5 to PP7), carports, street trees |
| City chase | `worlds/city.py` | 400 × 400 m | 2: CT1, CT2 | downtown towers 12 to 48 m, base pad at (−172, −172); **to be replaced by test12** (merge plan) |
| Search and rescue | `worlds/sar.py` | 1.5 × 1.5 km | 5: SR1 to SR4, VC2 | ~15,900 trees, 9 sectors with a probability-of-area prior, lawnmower sweeps, a person under canopy |
| Flood rescue | `worlds/flood.py` | 500 × 500 m | 2: FR1, FR2 | road graph with elevations, bathtub flood model (time ×240), roads closed above 0.3 m, stranded people, a ground crew at staging (0, 82) |
| Firefighter routing | `worlds/fire.py` | 200 × 200 m | 2: FF1, FF2 | two-storey house with openings A to D, grass fire spread, smoke, keep-out zones, fire engine, crew at staging (40, 45), occupant outside (FF2) |

Scenario stories (`sim/worlds/manifests/*.json`) put timed captions over the video or 3D view for PP0, PP5 to PP8,
SR1 to SR3, FR1, FR2, FF1 and FF2.

### 4.3 Perception and tracking — Working (simulator); real-video accuracy not measured

- **Detectors** (`vision/detectors.py`):
  - `sim-blob-v1`: HSV colour blobs, valid on synthetic frames only, and labelled so. It is the default in the simulator.
  - YOLO26n/s for real video, or when a scenario asks for it.
  - A tiled detector for small overhead targets.
- **Tracking** (`vision/tracker.py`): ByteTracker with symmetric buffered IoU and appearance histograms.
- **Supporting pieces**:
  - camera-motion compensation (LK flow + RANSAC homography);
  - calibration hierarchy: markers → chained → operator points → telemetry ray → invalid;
  - ground projection with a per-track Kalman filter.
- **Object memory**: persistent person ids across occlusion, explained absences, a crossing guard, and since E7 no merge behind a runner.
- **Locks**: subject and responder, LOCKED → COAST (0.7 s) → REACQUIRE (2 s) → HOLD (20 s). Automatic re-bindings are unverified until a human confirms.
- **Clothing description parsing** (`vision/appearance.py`). Since E7, colours are voted over frames.

### 4.4 Autonomy — Working in the simulator; real flight hard-disabled

- **Modes** (`autonomy/controller.py`): MANUAL, FOLLOW, HOLD, RTL, ESTOP, DISPATCH, GOTO. Virtual-stick commands go out at 20 Hz with a 300 ms lease.
- **Dispatch FSM**: TAKEOFF → TRANSIT → lawnmower SEARCH → clothing match → TRACK, with position reports relative to the pad (PP0, PP8, SR4, CT1).
- **Follow**:
  - image-based visual servoing at a 12 m standoff, ≤ 4 m/s (≤ 6 m/s reacquiring), 45°/s yaw;
  - VERIFY holds translation on unverified re-bindings.
- **Reacquire**:
  - D1 primitives (continue, yaw scans, climb and widen, orbit, hold) where there is no street map;
  - on street maps, since E7, **belief-based routing** (`autonomy/belief.py`, `autonomy/pursuit.py`): 500 particles per identity hypothesis on the street graph, coverage-scored viewpoints and a safe route, with D12 choosing among the top three (rules baseline).
- **jev-drone low-altitude stack**: 5-sector obstacle reflex and D6, used by PJ1 and PJ2 with the MuJoCo X2. Due for removal (6.1).
- **Safety supervisor**: per-world geofence, altitude band, speed caps, people-overhead check, RTL on low battery. `autonomy.allow_real_flight: false`.

### 4.5 Decisions — Working (rules by default)

- **Tasks**:

  | Tasks | Area |
  |---|---|
  | D1 reacquire, D2 identity, D3 describe, D4 motion | locating and identifying the subject |
  | D5 alert priority, D6 obstacle | alerts and flight safety |
  | D7 lead triage, D8 next sector | search |
  | D9 crew route, D10 dispatch, D11 opening | crews and structures |
  | D12 pursuit route | pursuit |
  | D13 voice-command intent | voice commands (since E7) |

- **Router**: removes unsafe options before any engine sees them, and answers single-option jobs itself. It validates choices and probabilities, gates on confidence, drops stale answers, and enforces cost guards.
- **Engines**: OpenAI Decisions (verified live on ), TypeSafe, Strands (not run), a mock, and deterministic rules. `decisions.pursuit_engine: rules` and `decisions.voice_engine: rules` keep the demo independent of the API.
- **Recommendations**: eight templated mission actions, each needing human approval.

### 4.6 Events, callouts and voice — Working

- **Rules and alerts**: the rule engine and alert arbiter produce APPROACH and CLOSING_FAST alerts and POSITION_CALLOUT every 8 s or on a 30° bearing change, with a 4 s cooldown and at most 12 spoken a minute. On `/` they show as text only; speech is off by default (`overwatch.speak`).
- **Voice output**:
  - ElevenLabs TTS (`voice/tts.py`, `GET /api/tts`): ordered accounts with automatic fallback, memory and disk caches. Used by `/responder/sensors` and by test12.
  - Gemini narration: optional, off by default.
- **Voice commands** (E7, `voice/commands.py`, `voice/grounding.py`, D13):
  - Recognised: FOLLOW, SEARCH, GOTO, HOLD, RTL, FIND. ORBIT and LOOK are recognised but declined.
  - Input from browser speech or typed text, a keyword grammar by default, grounding against named places and people.
  - Every step is logged as an incident event, behind an approval policy.

### 4.7 Incidents, persistence and integrations — Working

- **SQLite store** (WAL): an append-only incident event log, raw and reviewed observations kept apart, and deterministic log replay that reproduces the live metrics.
- **Tiger Data / TimescaleDB mirror** (E6): hypertables (`ow_events`, `ow_telemetry`, `ow_detections`), 1-minute and 10-second continuous aggregates, and compression after a day.
  - It runs when a URL is found: `TIGER_DATABASE_URL` or `CONNECTION_STRING`, then `TIGER_CREDENTIALS_FILE`, then any `tiger-cloud-*credentials*.txt` at the repo root (`config.py`).
  - A live-metrics panel on `/` shows it.
  - The service test passed against the real database (`888d3a2`).
- **Cloudflare quick tunnel** (`api/tunnel.py`): it serves the configured port (`OVERWATCH_PORT`, default 8000), and `/` shows a QR code for the phone joystick `/drive`.
- **Live crew routing** (E7, `missions/crew_routing.py`): time-dependent safe routes for crews, through flood water (closed above 0.3 m) and fire (keep-out two compressed minutes ahead, smoke costs extra). It re-plans every 2 s and is offered to D9. **It proposes routes only; no crew moves.**
- **Keys configured in `.env`** (names only): `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY` (+ account, agent ids), `TYPESAFE_API_KEY`, `STRANDS_URL`, `TIGER_DATABASE_URL`, `CONNECTION_STRING`, `OVERWATCH_TOKEN`.
  - `.env` is git-ignored.
  - So is `tiger-cloud-hacktx-credentials.txt` at the repo root (checked: not tracked).

### 4.8 HTTP and WebSocket API — Working

There are 117 HTTP routes: `api/app.py` 57, `api/incidents.py` 58, `api/sim3d.py` 2. On top of those:
- **8 WebSockets.**
- **The dashboard** (the SPA catch-all `/{path:path}` and `/assets`, both served from `dashboard/dist`).
- **test12's 12 routes**: 10 under `/api/test12/`, plus `/test12` and `/test12/{name}`. `main.py` splices them in just before the catch-all, and only `overwatch.main:app` has them. Tests that call `create_app()` do not.

| Group | Routes (abridged) |
|---|---|
| Status and config | `GET /api/health`, `/api/config` (with `keys_present`), `/api/session`, `/api/scenarios`, `/api/usecases`, `/api/metrics`; `POST /api/session` |
| Simulation | `GET /api/simulation/faults`, `/api/simulation/people`; `POST /api/simulation/config` (faults), `/api/simulation/people/{id}` (goto, drive, stop, resume), `/api/simulation/control` (pause, resume, speed, restart, pilot override) |
| Tracks and locks | `GET /api/tracks`; `POST /api/click`, `/api/tracks/{id}/select`, `/api/locks/{role}` (+ DELETE), `/api/locks/subject/confirm` and `/reject`, `/api/locks/describe` (unreachable, 6.6) |
| Autonomy | `POST /api/autonomy/mode`, `/dispatch` (search by clothing words from the pad), `/dispatch/cancel`, `/estop`, `/geofence` |
| Decisions and logs | `POST /api/decisions/engine`; `GET /api/decisions`, `/api/events`, `/api/alerts`, `/api/transcript`; `POST /api/alerts/ack` |
| Incidents (`/api/incidents/{iid}/…`) | create and list; observations and reviews; tracks; alerts with acknowledge and delivered; Follow (designate, describe, confirm, reject, release, look-for); mission actions with approve and reject; responder position and brief; scene (anchors, transforms, georef, structure side A, assets upload, sim export, sample indoor scan, overlay); sources; replay (start, pause, seek, restart); events; metrics |
| Calibration and registration | `GET /api/calibration/status`; `POST /api/calibration/ground-points`, `/api/registration/manual` |
| Recording and replay | `GET`/`POST /api/recordings`; `POST /api/replay/control` (REPLAY mode) |
| Voice | `GET /api/tts?text=` (ElevenLabs); `GET /api/voice`; `POST /api/voice/command`, `/answer`, `/primitives/{id}/approve` and `/reject`, `/settings` |
| World, video, 3D | `GET /api/world/meta`, `/ortho.jpg`, `/hazards`, `/thermal.jpg` (SIMULATED); `/streams/{drone,phone}.mjpg` and `.jpg`; `GET /api/sim3d/world`, `/api/sim3d/asset/{x2.obj,x2.png}` |
| Integrations | `GET /api/tiger`, `/api/incidents/{iid}/tiger/metrics`; `GET /api/tunnel`, `POST /api/tunnel/start` and `/stop`; near-IR preview and enhancement |
| WebSockets | `/ws/state`, `/ws/events`, `/ws/alerts`, `/ws/video/{src}` (binary JPEG with overlay header), `/ws/sim3d` (20 Hz), `/ws/incidents/{iid}`; LIVE uplinks `/ws/phone` and `/ws/bridge` |

Write routes carry `Depends(auth)`, but the check is skipped when `OVERWATCH_TOKEN` is empty (see 6.7).

### 4.9 Dashboard — Working in source; the backend serves a stale build

React 19, Vite 7, Tailwind 4, three.js 0.169, zustand, recharts, and `@mkkellogg/gaussian-splats-3d`.
- **Size:** 44 files, 9,741 lines.
- **Routing:** `src/main.tsx` picks the page by path prefix, and any other path falls through to the demo. `lib/router.ts` only handles the `/ops` sub-pages.
- **Dev server:** Vite on :5188, proxying `/api`, `/streams` and `/ws` to the backend.

| Route | Page | Lines | What it does |
|---|---|---|---|
| `/` (and unmatched paths) | `pages/Demo.tsx` | 762 | The simulation demo (below) |
| `/dashboard` | `pages/Mission.tsx` | 91 | Incident command: mission mode and world picker, video, 3D and scene tabs, map, review queue, Follow, Structure A to D, recommendations, timeline replay, audit, source health, calibration |
| `/ops`, `/ops/drone`, `/ops/phone`, `/ops/map` | `Console.tsx`, `ViewPage.tsx` | 52, 56 | The original sensor and fusion console with fault injection and the decision-engine switch |
| `/3d` | `pages/Sim3D.tsx` | 292 | Full-screen 3D view with the decision HUD |
| `/drive` | `pages/Drive.tsx` | 129 | Phone joystick for a simulated person (4 Hz commands, 0.6 s lease) |
| `/responder` | `pages/Responder.tsx` | 327 | Responder PWA: live alerts, speech, vibration and flash, one-tap ACK, grounded clock bearings |
| `/responder/sensors` | `pages/ResponderSensors.tsx` | 208 | Phone-sensor proximity view (uses `/api/tts`) |
| `/test12` | served by the backend from `test12/web` | 831 (HTML, JS, CSS) | test12's page (4.10) |

**The demo page `/`**:
- **Left column:** "Start here" (PP0) and "All simulations", which lists each use case's scenarios.
  - Picking one creates an incident (`POST /api/incidents`), or restarts in place if it is the same world (`…/replay/restart`).
- **Centre:**
  - a "Drone camera" tab (`/ws/video/drone`, with overlays) and a "3D world" tab;
  - a playback bar;
  - the scenario story captions.
- **Right column:**
  - "Now playing" (plain-words status);
  - the route-reasoning card (pursuit belief) and the voice-command panel, when present;
  - "Dispatch from the launchpad" (clothing words, a last-seen point picked on the map, a 20 to 200 m radius, Wrong person, Cancel);
  - people control (send to a point, stop, resume, arrow keys);
  - "Drive the target from a phone" (tunnel and QR);
  - Tiger live metrics, when enabled;
  - the map, and the last 8 events.
- The page's own comment and `DEMO_RUNBOOK.md` call it read-only apart from choose, pause and restart. It is not: it dispatches the drone, drives people, opens a public tunnel and approves voice primitives.

**The 3D view** (`lib/sim3d.ts`, 886 lines; the same controller serves `/3d` and the embedded tabs):
- **Coordinates:** ENU mapped to three.js as (x, z, −y).
- **Cameras:**
  - chase: 11 m behind on a smoothed heading and 5 m up, looking 9 m ahead and below; the drone drawn at 2×;
  - top: 85 m over the drone; the drone drawn at 5×;
  - overview: orbit controls; the drone drawn at 5×.
- **What it draws:**
  - the ground ortho texture, trees, boxes, zones and geofence walls;
  - the drone mesh (the Skydio X2 OBJ, labelled "Skydio X2"), with its camera footprint and rays and a trail of up to 6,000 points coloured by who was in control;
  - jointed, animated people;
  - the lock estimate, last-seen point and search radius;
  - the pursuit belief (particles per hypothesis, candidate viewpoints with coverage, the route);
  - story focus rings, hazards (water sheet, fire, keep-out, smoke, crew route) and decision pins.
- **Data:** it reads the world and the 20 Hz frames from `/ws/sim3d` only. The world arrives as a message on connect and on every scenario change.

**The build the backend serves is stale.**
- `dashboard/dist` was built on  at 17:47 and does not contain the E7 UI (no `voice/command` in the bundle).
- So `/` and `/dashboard` served by the backend on :8000 and :8012, and through the tunnel, show the older UI. The voice panel, route card, stories and 3D hazards appear only on the Vite dev server until `npm run build` is run.
- `make build` runs `vite build` without the type check, and `dashboard/tsconfig.tsbuildinfo` is committed.

### 4.10 test12 — Working (simulated); verified headless and live on 

**Story**:
1. A 911 call reports a red-shirted runner, at a random spot 90 to 250 m from the station or one picked on the map.
2. A DJI Mini 2 stand-in climbs from the station pad to 30 m.
3. It searches a 1,500-particle belief over the sidewalks with a predictive planner that does not circle, and a survey log shown on the map.
4. It finds the person with **pretrained YOLO26n at 5 Hz** plus a torso-colour check (3 sightings in 2.5 s).
5. It follows 18 m behind at 28 m (1.1 s⁻¹ gain on the gap, ≤ 15 m/s) and reports "base +x m E, +y m N" by dead reckoning every 2 s.
6. Lost for 3.5 s: hover over the last-seen spot and turn 360° circles until found. This is about to change to 360°, orbit 10 s, road search (merge plan WP1).
7. The police car leaves at the first report and pings its position (with course and speed) every second.
8. Every report then becomes a callout relative to the car: distance, compass, clock position, street.
9. Callouts are read out with ElevenLabs on the page, with the browser voice as a fallback. Urgent ones play straight away, routine ones about every 8 s.
10. The suspect flees and hides from the car (one hide a minute at most), and nothing ends the run but Stop or 30 minutes.
11. The first light-bar sighting is checked against the car's own ping.

**Video and views**:
- 1280 × 720 at 30 fps from a pool of 3 render processes.
- Views: drone camera, rendered third-person (3.2 m behind), and bird's-eye (95 m up, north up) with a live drone-camera inset.
- The drone is drawn to scale.
- The idle picture shows before the first run.

**Process layout**:
- The web server only relays: it plays frames out at an even 30 fps and serves the API.
- A spawned child process runs the simulation, YOLO on a thread and the render pool.
- Runs are deterministic per seed.
- The main simulation is paused while a run is going.

**Files**:

| File | Lines | What |
|---|---|---|
| `sim.py` | 804 | one run: phases, reports, callouts, pings, scoring |
| `search.py` | 552 | belief, survey log, planner |
| `world.py` | 504 | the map and hiding spots |
| `render.py` | 477 | depth-tested renderer, patrol car, drone model |
| `server.py` | 375 | API, playout, voice relay |
| `people.py` | 317 | suspect and bystanders |
| `live.py` | 181 | the run's child process |
| `vision.py` | 170 | YOLO, red-shirt check, light-bar detector |
| `follow.py` | 171 | filter, matcher, follow, scan, search flight |
| `streets.py` | 150 | street graph |
| `run_headless.py` | 121 | scored batch runner |
| `drone.py` | 112 | Mini 2 flight model, dead reckoning |
| `police.py` | 112 | patrol car and ping |
| `hud.py` | 96 | overlays |
| `views.py` | 73 | rendered chase and top cameras |
| `frames.py` | 71 | render pool |
| `los.py` | 64 | line of sight |
| `web/app.js` | 608 | the page |

### 4.11 iOS apps — Written, not built

| App | Swift files / lines | Main parts |
|---|---|---|
| OverwatchResponder (`com.overwatch.responder`) | 16 / 2,720 | ResponderModel (coordinator), DepthProcessor (a port of `phone_depth.py`), AlertFeedback (haptics, speech, spatial earcon), ObservationPipeline, ARSessionManager, SensorHub (compass, GPS, AirPods), CaptureTransport (`/ws/phone`), Views |
| OverwatchDroneBridge (`com.overwatch.dronebridge`) | 11 / 2,332 | VirtualStickController (20 Hz loop, 300 ms command lease then hover, 2 s silence hands back to the pilot), BridgeController (DJI to `/ws/bridge`), VideoForwarder (H.264), TelemetryCollector, FPV preview |
| Shared | 2 / 561 | ReconnectingWebSocket, WireProtocol |

- **Build setup:** XcodeGen `project.yml` (iOS 17), a Podfile (DJI SDK 4.16.2), and `ios/README.md` with 8 VERIFY items.
- **Signing:** the DJI app key in the bridge's Info.plist is a placeholder.
- **Never compiled:** this machine has no Xcode. The last iOS change was on .

### 4.12 Tests, evaluation and tooling

- **Unit and integration tests**: 186 test functions in 35 files (`backend/tests`).
  - The  audit reports "215+ passing, 1 skipped" (parametrised cases).
  - **Not run since E7**: E7's own notes say `tests/unit/test_pursuit.py` and others may need updating.
- **Scenario evaluation** (`backend/scripts/run_scenarios.py`): the last full batch,  20:55 to 21:12, ran 37 scenarios and all 34 with a verdict passed.
  - Reports: `evaluation/reports/run-210803` (PP0 to PP8), `-211256` (PJ, CT, VC), `-205515` (demo, OW), `-210211` (SR, FR, FF).
- **End-to-end**: a headless-Chrome Playwright run of the full incident lifecycle, twice with identical outcomes (`evaluation/e2e`, ).
- **test12**: `test12/run_headless.py` with results in `evaluation/test12/`:
  - `run-223115`: 20 seeds of the original "ends when the police arrive" version;
  - `run-004758`: 12 seeds × 240 s of the current version.
- **Tooling:**
  - **Makefile targets:** setup, backend (SIMULATED on :8000), backend-live, replay, devices (fake DJI bridge and phone over the real protocols), dashboard (:5188), build, test, eval, eval-quick, tunnel, markers, schemas, bench-models, bench-small, bench-decisions, e2e, bench-incident.
  - **`backend/scripts`** (10 files): scenario runner, model, decision and incident benchmarks, the end-to-end demo, fake devices, marker and schema and fixture export, and a world-decisions report.
  - **`scripts/`:** `tunnel.sh`, and reconstruction scripts (COLMAP, splatfacto) never run here for lack of a GPU.
  - **Optional pyproject extras:** `vision` (ultralytics), `rfdetr`, `h264` (av), `voice` (google-genai), `sim3d` (mujoco), `timeseries` (psycopg), `dev`.
- **Other repo contents:**
  - **Tracked:** 419 files, of which:
    - `schemas/`: 46 JSON Schemas, last exported , so `WorldState.json` lacks pursuit, crew routing, voice and dispatch;
    - `fixtures/demo_incident/`;
    - `config/default.yaml` and `config/tabletop.yaml`.
  - **Not tracked:** `data/` (about 119 MB: the incident database, evidence images, scene files, test12's ground-texture cache).

---

## 5. Verification status

| What | How it was checked | Result |
|---|---|---|
| Platform scenarios | headless batch,  20:55 to 21:12 | 37 run, all 34 with a verdict pass; PP5 to PP7 re-find the subject 19 to 31 s after the loss (E7) |
| Platform live | E7 live checks on :8010 and :5190 | PP6, PP7 played end to end. On a loaded machine the live server falls behind real time and some runs chased the look-alike. |
| Platform unit tests | not run in this audit (by request) or in E7 | may need updating after E7 |
| test12, live () | browser on :8012 and through the tunnel | 30 fps in all three views, 0 dropped frames, sim 1.0×, YOLO ~50 ms a pass; spawn picking, pings, callouts and ElevenLabs audio (HTTP 200) checked; lost → circle → found again → police on scene seen in seed 9 |
| test12, headless (, 12 seeds × 240 s) | `evaluation/test12/run-004758` | all found, 0 wrong matches; drone 18.2 m median behind the suspect (≈21 m at the 90th percentile); reports 0.6 m off on average; callouts 1.3 m off on average (up to 11 m when the car is fast between 1 Hz pings) |
| test12 loss behaviour | same batch vs an earlier batch with one 360° then the cover search | **circle until found**: following a median 34% of the time, 25 of 33 losses found again, 3 of 12 runs never re-found. **One 360° then search**: 61%, 58 of 63. The earlier batch's suspect also hid twice as often. |
| Real hardware | none | iOS apps not built; DJI bridge, responder phone and real-video detection untested |

---

## 6. Known issues, gaps and stale items

### 6.1 Skydio removal, phase B (decided : DJI only; not done)

Phase A (`055f3e0`) removed the X2 aircraft profile and the PX4 cross-check. Still present:
- `backend/third_party/skydio_x2/` and `sim/mujoco_drone.py`;
- the jev-x2 low-altitude stack;
- scenarios OW7, PJ1 and PJ2;
- the X2 mesh in `api/sim3d.py` and `dashboard/src/lib/sim3d.ts`, so the 3D view draws a Skydio, at 2 to 5 times its size;
- mentions in docs and the pitch.

It waited on another session's uncommitted work, which `ef7c29d` committed, so it is now unblocked.

### 6.2 test12

- The current loss behaviour (circle until found) loses the suspect for good in about a quarter of runs. The merge plan's WP1 replaces it.
- Callouts can be about 11 m off when the police car is fast, because the ping is 1 Hz and the car turns between pings.
- ElevenLabs usage is about 700 characters a minute per page listening with voice on.
- test12 is a separate engine: nothing it does reaches the incident log, Tiger Data or the decision log.
- The 3D tab does not show it (merge plan WP3).

### 6.3 Platform

- The richer 3D flood and fire visuals are uncommitted in the `feat/fire-water-3d` worktree, behind `main`, and conflict with it in six files.
- Live runs on a loaded machine fall behind real time.
- ORBIT and LOOK voice commands are declined.
- D12 and D13 have not been reported against a real model engine.
- Crews never move.
- The follow speed cap (4 m/s) is below a sprinting runner. test12's suspect runs 3 to 6 m/s; whether PP scenarios hit the cap has not been measured.

### 6.4 Docs

- [FUNCTIONALITY_AUDIT.md](FUNCTIONALITY_AUDIT.md) describes `a945754` plus the worktree. It predates test12 and E7, and it still presents the Skydio X2 and jev-x2 as current.
- **test12 is missing from the main docs.** Only `DEMO_RUNBOOK.md` (one section) and `test12/README.md` describe it. `README.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_STATUS.md` and `LIMITATIONS.md` do not mention it.
- **Skydio, X2 or PX4 mentions remain** (counted case-insensitively; some may be "removed" notes): `README.md` 5, `IMPLEMENTATION_STATUS.md` 3, `LIMITATIONS.md` 2 plus 1 PX4, `DEMO_RUNBOOK.md` 1, `docs/operating_guide.md` 2. They are part of phase B (6.1).
- **Other stale statements:**

  | File | What is out of date |
  |---|---|
  | `README.md` | calls `/` the mission page (it is the demo; `/dashboard` is the console); "four worlds" (six); "192 tests" and "38 scenarios"; the hero image shows the old console |
  | `ARCHITECTURE.md` | "D1 to D11" (D12 and D13 exist); its worlds list omits the city; its frontend section predates `/`, `/dashboard`, `/drive` and `/3d` |
  | `IMPLEMENTATION_STATUS.md` | the X2 "mass from the model file" note refers to code removed in `055f3e0`; "192 passed" has not been re-run since |
  | `LIMITATIONS.md` | X2-vs-Mini 2 numbers from the deleted cross-check; a PX4 note about removed scripts; "24 world scenarios" with 23 listed |
  | `DEMO_RUNBOOK.md` | "four worlds"; "Three things" lists four; calls the demo page read-only apart from choose, pause and restart |
  | `progress_2p.md` | superseded: says routing is broken and water and fire are unbuilt; both have since been built |
  | `docs/FUNCTIONALITY_AUDIT.md` | says the 3D hazards exist only on the worktree (`main` has a simpler version) and that PP5 to PP7 all pick the same route (E7 shows they differ) |
  | `docs/contracts.md`, `docs/decision_engines.md`, `docs/evaluation.md` | no `/ws/incidents/{iid}` or `/ws/sim3d`; D1 to D5 only; "all 26 scenarios" |
  | `docs/operating_guide.md`, `docs/demo_script.md` | built around the old console on :8000 |
  | `docs/pitch/overwatch_pitch.md` | frames the demo on the MuJoCo X2 and keeps a PX4 roadmap, against the DJI-only decision |
  | `docs/report/` (generated status report) | 17 of 26 scenarios, 69 tests, 4.6k lines of TypeScript |


### 6.5 Hardware boundary (unchanged)

- The native iOS apps need Xcode on macOS and the devices.
- Detector accuracy on real footage needs labelled recordings.
- Scene-reconstruction training needs a CUDA GPU.

### 6.6 Functional defects found by reading the code (nothing was run)

1. **`POST /api/locks/describe` cannot be reached.**
   - `POST /api/locks/{role}` (`api/app.py:279`) is registered before it (`:312`), so "describe" is taken as a role and the request fails validation.
   - The `/ops` track inspector's describe-then-confirm (`TrackInspector.tsx:213`) therefore cannot work.
   - The Follow panel's `/api/incidents/{iid}/follow/describe` is a separate route and is unaffected.
2. **`tests/unit/test_worlds.py` will likely fail.** It expects 27 scenarios outside the worlds (`:33`), but `VC1` and `VC2` (E7) are not in its `WORLD_PREFIXES`, which makes 29. E7 changed no tests.
3. **The backend serves a stale dashboard build** (4.9).
4. **3D view issues** (from reading only, not seen on screen):
   - The label styles live only in `Sim3D.tsx`, so labels in the embedded tabs render unstyled.
   - The right-hand offset meant for `/3d`'s side panel also shifts the embedded tab's scene.
   - `/3d`'s "Back to the console" link goes to `/`, which is now the demo page.
   - It labels the drone "Skydio X2".
5. **The `/ops` decision panel names only D1 to D5** (`DecisionPanel.tsx`), so rows for D6 to D13 show no task name.
6. **Some decision tasks are never asked live.**
   - D7 (lead triage), D8 (next sector) and D10 (crew dispatch) are only submitted by `scripts/worlds_decisions_report.py`.
   - D11 runs as rules inside the fire world's evaluation.
   - The live triggers described for them in `SIM_TASKS` are never fired.

### 6.7 Exposure and cost

- **Auth is off unless `OVERWATCH_TOKEN` is set, and on the dev servers it is not in effect.**
  - An unauthenticated `POST /api/tunnel/start` succeeded on :8012 on .
  - Even with a token, 6 POST routes and 6 of the 8 WebSockets skip the check.
  - CORS is `*`.
- **`GET /api/tts?text=` has no auth and speaks any text up to 400 characters** with the ElevenLabs key. Behind the public tunnel, anyone with the address can spend the account's characters.
- **test12's API has no auth.** Anyone with the tunnel address can start and stop runs, switch the view, and have the current run's callouts spoken. Speech is limited to those callouts, one at a time.
- **The tunnel publishes the server** at a public `trycloudflare.com` address while it runs. The demo page's phone QR code also puts the stored access token in the `/drive` link (`Demo.tsx:640`).
- **Tests may reach real services** (from reading; not run).
  - `conftest.py` isolates only the SQLite database.
  - Tests that call `create_app()` or `load_config()` read the repo's `.env`, and the config also falls back to a `tiger-cloud-*credentials*.txt` at the repo root.
  - So a test run on this machine may mirror to Tiger Cloud or use paid keys.

### 6.8 Code and config hygiene (minor)

- **Stale docstrings:**
  - `decisions/tasks.py` says "D1-D5" (13 tasks exist);
  - `sim/scenarios.py` says "OW1…OW6";
  - `autonomy/controller.py` lists 5 modes (7 exist);
  - `worlds/sar.py` says four sectors (9 are built).
- **Config keys never read:** `session.recording_enabled`, `session.consenting_participants_only`, `calibration.max_age_s`, `alerts.track_lost_ms`, `alerts.local_proximity_independent_of_ai`, `alerts.allow_identity_claims`, `privacy.retain_video`, `voice.tts`. `min_markers` defaults to 4 in code but 3 in the YAML.
- **Unused or overlapping paths:**
  - the per-scenario detector override (no scenario sets it);
  - `aircraft.PROFILES`;
  - the template narrator and Gemini both writing `pipeline.narration` on separate timers.
- **Dependencies and build:**
  - `pydantic-settings`, `google-genai` and `hypothesis` are declared but never imported.
  - `make setup` installs only `.[dev]`, though the venv also has ultralytics, mujoco, psycopg, av and torch.
  - The Makefile declares a `clean` target that does not exist.
- **Duplicate API surfaces:** `/health` and `/api/health`; `/api/alerts/ack` and `/api/alerts/{aid}/acknowledge`; `/api/incident` and `/api/incidents`.
- **Performance and settings:**
  - The MJPEG streams hard-code 15 fps and JPEG quality 75 instead of the config values.
  - `all_scenarios()` is not cached, and SR3 builds the whole SAR world on every call (`/api/scenarios`, `/api/usecases`).
- **No markers:** there are no TODO, FIXME or HACK markers in the backend.

---

## 7. Next steps

1. **Make the drone pursuit work better.** This is the next priority.
   - **test12 first** (it has the metrics):
     - replace "circle until found" with one 360°, then a 10 s orbit of the vanishing point, then the road search (merge plan WP1);
     - target: no run that never re-finds the suspect, and following back above 60% of the time after the match;
     - then cut the time out of sight before a re-find (about 22 s median in the final batch, 7 to 158 s);
     - and keep the 18 m standoff steady (90th percentile about 21 m today). The worst cases, 29 to 53 m, are mostly the approach after a match made from up to 55 m away and the reversals.
   - **Then the main platform's Police pursuit**:
     - measure following time and distance the same way;
     - check whether the 4 m/s follow cap keeps up with a runner;
     - re-run PP5 to PP7 live on an idle machine (E7 saw the look-alike chased under load);
     - consider YOLO in place of the colour-blob stand-in for the pursuit scenarios, as test12 does.
   - Report every change with the same batch numbers: following %, standoff, time out of sight, never-re-found runs, wrong matches.
2. **Merge test12 into the main platform**, as agreed: City chase becomes test12, shown in the main 3D views; flood and wildfire get search → beacon → hold until the crew arrives, with crew pings and callouts. See [TEST12_MERGE_PLAN.md](TEST12_MERGE_PLAN.md) (about 2½ to 3 days).
3. **Quick fixes this audit found** (each under an hour):
   - rebuild `dashboard/dist` so the backend and the tunnel serve the current UI;
   - before the next public tunnel, put `GET /api/tts` and test12's API behind the access token and set `OVERWATCH_TOKEN` (6.7);
   - move `/api/locks/describe` above `/api/locks/{role}`; add `VC` to the test's world prefixes; name D6 to D13 in the decision panel (6.6).
4. **Skydio removal, phase B** (6.1). The DJI-style quad in the 3D view is part of the merge's WP0.
5. **Decide what happens to the `feat/fire-water-3d` worktree**: commit and merge it, or drop the richer visuals in favour of `main`'s.
6. **Decide on the teammate branch `origin/drone-v1`**: 2 commits of drone movement controls, 9 behind `main`.
7. **Refresh the docs** that this audit marks stale (6.4).
