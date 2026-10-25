# Implementation status

Checkpoint for the sequential build run defined in `docs/WORLDS_AND_DASHBOARD_PLAN.md` (phases 0 to 17). After any
interruption: read this file and `git log --oneline -20`, then continue from the first unfinished phase.

**Current phase:** complete (all 18 phases); see the final report at the end

## Environment (checked )

| Item | Value |
|---|---|
| OS | Linux |
| Python | 3.12.14 (`backend/.venv`) |
| Node, npm | 24.20.0, 11.19.0 |
| GPU | none (no `nvidia-smi`): detection on CPU; no Gaussian-splat training here |
| Xcode | none (Linux): iOS apps are written but cannot be built here |
| Docker | 29.9.2 |
| Browser | Google Chrome 156, headless works |
| CPU, memory | 16 cores, 22 GB |

## What exists before this run

- FastAPI backend with REST and WebSockets, an in-memory incident, events, alerts with acknowledgment, decisions D1
  to D5 (OpenAI Decisions live, rules fallback), calibration, registration, autonomy (simulation only), recording and
  deterministic replay.
- Simulator: one 65 by 44 m field, OpenCV renderer, kinematic Mini 2 stand-in, MuJoCo Skydio X2, 26 scenarios.
- Dashboard: console, `/drone`, `/phone`, `/map`, `/3d`, `/responder` (speech, vibration, acknowledgment, reconnect).
- iOS apps (responder with ARKit/LiDAR; DJI Mini 2 bridge with MSDK 4.16.2): written, never run on hardware.

## What is missing (to build in this run)

SQLite persistence and an incident-centered data model; incident-scoped routes and stream; a review queue; Search,
Structure, Indoor and Follow modes as defined in the brief; a video-source interface with near-IR; the dark mission
dashboard; a responder PWA (manifest, service worker); a scene viewer; the four large simulation worlds; aircraft
profiles with fit tests; handoff documents; an end-to-end demo test.

## Phase log

### Phase 0: inspect and baseline (done)

Results, run on :

- `make test`: `69 passed in 31.15s`; dashboard `tsc --noEmit` clean.
- `npm run build` (dashboard): built in 2.32 s (Sim3D chunk 536.57 kB, main chunk 732.30 kB).
- `make eval-quick` (demo, OW1, OW4, S05): every expected event passed; report `evaluation/reports/run-032319`.
  - demo: recall 0.952, lock correct 1.0.
  - OW1: recall 0.996, 0 ID switches.
  - OW4: measurement only; lock correct 0.978.
  - S05: TRACK_LOST and REACQUIRED in their windows.

Earlier claims about Overwatch and FlyBy results are treated as claims; the numbers above are the ones re-run here.

### Phase 1: contracts, frames, persistence (done)

- `backend/overwatch/incident/models.py`: Incident, SensorSource, Observation (+ Review, ObservationView), Track,
  SceneAnchor, Alert, MissionAction, IncidentEvent, Transform, Position, BoundingBox. UTC-only timestamps; named
  frames (`wgs84`, `enu:<id>`, `camera:<id>`, `image:<id>`, `arkit:<id>`, `scene:<id>`); positions need uncertainty
  and a method; manual positions need manual provenance; boxes must be in their source's image frame;
  `Transform.apply` raises `FrameMismatch` on the wrong frame.
- `backend/overwatch/incident/store.py`: SQLite (WAL); raw observations and reviews in separate tables (latest
  review wins); append-only event log with sequence numbers and listeners.
- `make schemas` now also writes `schemas/incident/*.json` and generates `dashboard/src/types/incident.ts`.
- Gate: 14 new tests; `make test` 83 passed; dashboard build OK; `make eval-quick` unchanged (demo, OW1, S05 pass).

### Phase 2: incident API and event stream (done)

- `backend/overwatch/api/incidents.py`: `GET /health`; incidents CRUD; mission switching; observations (list,
  ingest); `POST /api/observations/{id}/review`; tracks; alerts (list, create, acknowledge, delivered); responder
  position; scene (anchors, transforms); replay start, pause, seek, restart (over the existing `Runtime.control`);
  events (`?after=`); metrics; `WS /ws/incidents/{id}` with a backlog and sequence numbers (`?after=` on reconnect).
  Errors use `{"error": {"code", "message", "details"}}`.
- `backend/overwatch/incident/service.py`: creating an incident also starts the pipeline incident; pipeline alerts,
  acknowledgments, events and decisions are mirrored into the store (same alert ids, so either side can ack).
- `backend/overwatch/incident/metrics.py`: metrics computed only from the log and records.
- Database: `data/overwatch.db` (gitignored; `OVERWATCH_DB` overrides; tests use `:memory:`).
- Gate: 10 new API tests; `make test` 93 passed; build OK; `make eval-quick` unchanged. Live check on :8000:
  `/health` OK, incident created, pipeline events, alerts and decisions mirrored into SQLite.

### Phase 3: demo world and deterministic replay (done)

- `backend/overwatch/sim/worlds/spec.py`: scene spec v1 (buildings with openings by side A to D, supplies, people,
  drone start) building the simulator boxes, opening decals and ground truth. Sides follow the incident-command
  convention (A front, then clockwise from above).
- `backend/overwatch/sim/worlds/training.py`: training area A as scenario `DEMO-1`: one building (front door A,
  window B, rear door C, blocked garage D), a supply cache, a volunteer playing a missing person, one responder.
  The renderer now draws doors and windows (`OpeningDecal`).
- `fixtures/demo_incident/`: `scene.json`, `ground_truth.json`, `expected.json` (from `scripts/export_fixtures.py`).
- Replay: same seed gives the same events and tracks (state hash test). The simulated colour-blob detector flags
  the volunteer and, realistically, the orange supply crate (a false positive for the operator to reject).
- Gate: 4 new tests; `make test` 97 passed; build OK; full `make eval`: 27 scenarios, no expected event failed
  (report `evaluation/reports/run-034614`).

### Phase 4: video sources (done)

- `backend/overwatch/sources/video.py`: one manager over simulated, file, webcam, RTSP, DJI Mini 2 bridge and near-IR
  sources, with honest states: the DJI source is `connected` only after a real bridge hello and fresh data; a
  synthetic test harness is reported as simulated; real video in a SIMULATED backend returns a clear error instead
  of pretending to switch.
- `URLFrameSource`: files use media timestamps (frame index / fps, `timestamp_quality="media"`, provenance
  RECORDED); streams keep arrival timestamps; reconnects are counted into `SourceHealth.reconnects`.
- API: `GET/POST /api/incidents/{id}/sources` (switches are logged as `source_changed`; refusals as `error`).
- `HARDWARE_INTEGRATION.md`: DJI Mini 2 section (MSDK V4 only, manual flight, what to verify on hardware).
- Gate: 7 new tests; `make test` 104 passed; build OK; `make eval-quick` unchanged. Live check: LIVE backend on a
  rendered DEMO-1 clip shows the file source `connected` (replay) and the DJI bridge `disconnected`.

### Phase 5: mission dashboard (done)

- `/` is now the dark incident-command dashboard (`dashboard/src/pages/Mission.tsx`); the previous console moved to
  `/ops` (`/ops/drone`, `/ops/phone`, `/ops/map`).
  - **Left:** mission selector (Search, Follow, Structure, Indoor); world and scenario selector with honest status
    (training area ready, pursuit partial, others planned); sensors with connection states; source selector;
    incident metadata.
  - **Center:** drone view with boxes and track ids, click to select, "Flag as possible person" (Search) or
    "Designate volunteer" (Follow), play, pause and restart, source-mode badge; a 3D world tab (simulator ground
    truth, labelled SIMULATED).
  - **Right:** incident map with pins (confirmed filled, pending hollow, MANUAL diamonds, uncertainty rings) and
    click-to-place manual markers; review queue with evidence thumbnails, confirm, reject and evidence;
    Structure panel organised by side A to D with manual tags (for example a blocked entrance); a "Brief
    responder" panel with deterministic messages; alerts with acknowledgment.
  - **Bottom:** a timeline replay over the incident event log (the map and queue show the state at the cursor),
    audit log, source health, metrics.
- Backend: `POST /api/incidents/{id}/observations/from-track` (bbox in `image:drone`, ground position only with
  calibration and its error, evidence JPEG saved), `GET /api/evidence/{name}`, `GET /api/usecases`; starting an
  incident with a scenario loads it in the simulator; unknown `/api/` paths now return 404 instead of the SPA page.
- Browser walkthrough at 1440 px:
  - Started the demo incident (DEMO-1 loaded); selected the volunteer and flagged it (the queue shows evidence,
    SIMULATED, pending, ±3.0 m); confirmed it (the pin turns solid).
  - Sent a briefing ("Confirmed person marker. Position plus or minus 3.0 metres. Last observed 10 seconds ago.")
    and acknowledged it.
  - Switched to Structure and tagged a blocked entrance on side D.
  - Opened the evidence view: the estimate (-18.6, 13.0) m sits 1.5 m from truth (-20, 12.5), inside ±3.0 m.
  - Opened the 3D tab and replayed the timeline; reload restores everything from SQLite.
- Fixed during the check: a selector loop in the evidence view; rails collapsing at 768 px. At 768 px everything
  stacks without overlap.
- Gate: 2 new API tests; `make test` 106 passed; build OK; `make eval-quick` unchanged.

### Phase 6: responder PWA (done)

- `/responder` is a new mobile-first responder app (`dashboard/src/pages/Responder.tsx`):
  - joins the current incident (or one chosen from a list) and shows its mission and provenance;
  - receives alerts in real time over `/ws/incidents/{id}`, marks them delivered, and acknowledges them with a large
    ACKNOWLEDGE button;
  - speaks alerts after one tap on "Enable voice" (through the phone's current audio route, so AirPods work);
  - vibrates where the browser supports it, otherwise flashes the screen (iOS Safari has no vibration API);
  - shows reconnecting status, and works with no API key.
  - The older phone-sensor view moved to `/responder/sensors`.
- Positions: browser geolocation and compass when available (needs HTTPS; the app says so otherwise); in a SIMULATED
  backend, a labelled simulated responder position from simulator truth.
- `backend/overwatch/incident/briefing.py` + `GET /api/incidents/{id}/responder-brief`:
  - range and bearing only when both the target and the responder are grounded in the same frame;
  - a clock direction only with a trusted heading (accuracy 20° or better), otherwise compass points;
  - uncertainty always attached; only confirmed or manual targets are listed;
  - GPS is placed on the map only when the incident has a geographic origin.
- PWA: `manifest.webmanifest`, icons, `sw.js` (network-first app shell; registers only in a secure context).
- Backend: the latest active incident (and its scenario) is restored after a backend restart; pipeline alert ids
  are namespaced by session (they restart at `a-1` every session and would otherwise collide).
- Browser check at the mobile preset:
  - a briefing arrived in real time; with the simulated position the target read "11 o'clock, about 18 metres,
    plus or minus 3";
  - ACKNOWLEDGE recorded `acknowledged_by=responder`; incident metrics show acknowledgment latency (n=2).
- Fixed during the check: the simulated position used a wrong actor attribute (now covered by an API test);
  `/health` is not proxied by the Vite dev server (the app uses `/api/health`).
- Gate: 8 new tests; `make test` 114 passed; build OK; `make eval-quick` unchanged.

### Phase 7: detection and tracking (done)

- `GET /api/perception`: the active detector, what is installed (sim blob; Ultralytics 8.4.184 with `yolo26n.pt` and
  `yolo26s.pt` present; RF-DETR not installed), license notes (Ultralytics is AGPL-3.0 unless commercially
  licensed), and the reminder that scores are model confidences and no detection does not mean clear.
- Missing models fall back to a `NullDetector` named `none (<detector> unavailable: ...)` that produces no
  detections (tested); `auto` uses the simulated detector only on synthetic frames.
- Detection history: person tracks that were CONFIRMED are persisted per incident about once a second
  (`IncidentService.sync_tracks`), with positions only when calibrated, image boxes, identity `unverified` unless an
  operator designated the track, and `track_started`, `track_lost`, `track_reacquired`, `track_ended` and
  `track_designated` events.
- **Tracker bug found and fixed:** the buffered IoU enlarged only the track box, so after a short occlusion a
  detection exactly on a lost track scored about 0.2 IoU and a new ID was created. Buffering is now symmetric
  (C-BIoU style).
- Full `make eval` before → after (report `evaluation/reports/run-042600`), with no expected event failing:

  | Scenario | Measure | Before | After |
  |---|---|---|---|
  | S05 | track recall | 0.876 | 0.995 |
  | OW2 | track recall | 0.876 | 0.995 |
  | S04 | track recall | 0.811 | 0.965 |
  | OW4 | subject lock correct | 0.978 | 1.0 |
  | OW4 | subject ID switches | 2 | 0 |

  Worse: responder ID switches in S04 (28 → 32), S14 (12 → 18) and OW5 (6 → 11); OW7 recall 0.92 → 0.898.
- Gate: 4 new tests; `make test` 118 passed; build OK; full `make eval` as above.

### Phase 8: Search and Structure modes (done)

- `backend/overwatch/missions/search.py`: in Search, every confirmed person track becomes one "possible person"
  candidate (never per frame; tracks locked as the responder are skipped). A later track whose verified position is
  within the combined uncertainty plus 1.5 m of a recent candidate is recorded as a re-sighting (`attributes.group`)
  rather than a new person; image-only sightings are never grouped. Nothing declares an area clear.
- `backend/overwatch/missions/structure.py`:
  - Buildings come from map data. In the simulator that is the scene spec footprint, labelled as such and
    registered as an anchor.
  - The operator sets which compass face is side A (`POST /api/incidents/{id}/structure/side-a`); B, C, D follow
    clockwise. `GET /api/incidents/{id}/structure` groups openings and hazards by side, with wording such as
    "possible doorway, Side A, pending review".
  - In SIMULATED incidents a deterministic fixture proposes the openings in view of the drone camera
    (`sim-openings-v1`, labelled as a fixture, not a vision model; no confidence). Blocked entrances are only
    marked by the operator.
- Dashboard: the Structure panel uses the server view with a "Side A faces" selector; the queue shows re-sightings
  as "+N re-sightings" on the first sighting.
- Not implemented: open-vocabulary door and window detection (OWLv2, Grounding DINO). It needs `transformers`
  plus roughly 600 MB of weights and was not installed; Structure works with manual tags and the simulation fixture.
- Browser check: started the demo, and Search flagged the volunteer and the supply crate. Switched to Structure:
  the side A door and side D garage door were proposed. Set side A to south, then tagged a blocked entrance on D;
  the panel lists both sides correctly.
- Gate: 2 new integration tests; `make test` 120 passed; build OK; `make eval-quick` unchanged.

### Phase 9: positions, georeference, calibration panel (done)

- `backend/overwatch/incident/geo.py`: haversine distance, initial bearing, ENU ↔ WGS84 through the incident's
  coordinate reference (including its north offset), root-sum-square uncertainty, and image → ground projection
  only through a valid calibration homography with an error bound. Anything else raises `UnsupportedGeolocation`
  and the UI shows "location not yet verified".
- The incident origin comes from `world.geo_reference` in `config/default.yaml` and is labelled "configured
  reference, not surveyed". `POST /api/incidents/{id}/georef` sets a surveyed origin;
  `GET /api/observations/{id}/location` returns ENU, WGS84 and uncertainty, or the reason there is none.
- Dashboard: a Calibration tab in the bottom panel shows the ground-transform state and error bound and the
  geographic origin, and lets the operator pair image points with map points (click the video, then the map) and
  fit through the existing `POST /api/calibration/ground-points` (holdout points from the fifth pair on). The
  evidence view shows the observation's location and WGS84 where an origin exists.
- Structure fixture fixes found in the browser check: openings were proposed again after a backend restart
  (duplicates in the queue), and fixture openings had no image. Proposals now carry the opening id and are checked
  against the store, and each one saves the drone frame with the projected opening outlined.
- Header no longer wraps the "Start demo incident" button at 768 px.
- Browser check (1440 px and 768 px): the Calibration tab shows "valid · telemetry … error bound"; pairing an image
  point with a map point recorded "Ground point 1 paired at -3.6, -1.2 m". I did not apply a fit from made-up
  pairs to the running demo. The fit's validation (RMSE, holdout) is covered by the existing calibration tests.
- Gate: 7 new tests; `make test` 127 passed; build OK; full `make eval` (report
  `evaluation/reports/run-044933`): no failures, every scenario expected to pass passed, numbers unchanged from
  phase 8.

### Phase 10: decisions (done)

- `backend/overwatch/decisions/recommendations.py`: the eight allowed recommendations as `MissionAction`s. Each has
  a fixed template, an approval rule and safety constraints. Messages are filled only from fields computed by code
  (observation time in UTC, uncertainty in metres, counts); a time is used instead of an age so messages never go
  stale. Reasons and questions are codes with fixed wording; no free text from a model is shown as fact.
- Rules propose deterministically every mission cycle, deduplicated by key: review each fresh pending candidate
  (re-sightings fold into their group), notify the responder for each confirmed observation without an alert
  (waits for approval), hold when no position is verified and calibration is invalid, and ask for side A in
  Structure. Actions close when their condition is met (reviewed, alerted).
- Model proposals (`POST /api/incidents/{id}/actions`) go through schema validation, a flight-control word check,
  a "no measurements from a model" check, mission rules and data availability. Every rejection is logged as a
  `proposal_rejected` event with its code. `POST /api/actions/{id}/approve|reject`. Approving notify_responder
  re-checks that the observation is still confirmed and fresh, then sends the brief recomputed from the responder's
  position at that moment.
- Router rules in `decisions/router.py`:
  - Freshness: answers older than 1.5 s, or from an earlier session (a session counter bumped on every reset),
    are logged and not acted on; a dropped D1 answer no longer flies the rules' pick either.
  - Validation: an answer outside the offered options, or probabilities that include other values, fall outside
    [0, 1], do not sum to about 1, or do not rank the chosen option first, is rejected.
  - Options: code removes unsafe options before any engine (or the rules) is asked and records what it removed.
    Each option can carry its code-computed outcome ("450 m, about 6.0 min on foot").
  - Cost guard: a per-session budget of model calls and tokens, after which the router answers with rules only
    and the label says so.
  - The status label reads "rules only (no API key)", "MOCK model (no API key) …" or the engine and model in use.
    It is shown in the Recommendations panel header.
- D6 to D11 (obstacle, lead triage, next sector, route, dispatch, opening condition) are defined in
  `decisions/tasks.py`, each with typed options, an escape option, a rules baseline and a trigger. There are 12
  hand-written designed cases. In real-data mode each maps only to an allowed recommendation. They are wired into
  the worlds in phase 14.
- **Bug found and fixed:** in the server, the runtime's decision hook replaced the `elif` chain in
  `Pipeline.apply_decision`, so D2 (identity), D3 (description) and D4 (motion) answers were never applied outside
  the headless evaluator.
- Dashboard: a Recommendations panel in the right rail shows open recommendations with who proposed them, their
  constraints, and approve and reject buttons when approval is needed.
- Browser check (1440 px and 768 px): the demo produced one review recommendation per candidate. Confirming a
  candidate closed its review and produced "Notify responder" awaiting approval. Approving it sent the alert ("…
  Your position is not available, so no bearing or distance."), and the panel showed 0 awaiting.
- Gate: 32 new tests; `make test` 159 passed; build OK; `make eval-quick` passes (report
  `evaluation/reports/run-045331`).

### Phase 11: logging, replay and metrics (done)

- New event kinds:
  - `sensor_status`: every change of a source's state (OK, STALE, OFFLINE, NOT_CONFIGURED).
  - `operator_command`: operator commands on the pipeline (locks, autonomy, e-stop, engine choice,
    calibration, simulation), marked `override` for locks, autonomy and engine changes.
  - Track events now carry provenance and first-seen time.
  - Already logged since earlier phases: observations, reviews, track starts and losses, mission changes, alerts,
    deliveries, acknowledgments, decisions, decision proposals and rejections, and errors.
- Alert delivery and acknowledgment events carry the exact stored timestamp. `incident/replay.py` `rebuild()`
  replays an event log into a fresh store, and metrics recomputed from the log alone equal the live metrics. Fixed
  in passing: metrics read only the first 10,000 events of a long incident; they now read the whole log.
- Localization error is reported only where ground truth exists. A simulated observation flagged from a track
  records the simulator actor whose rendered box overlaps it (`sim_truth`, labelled evaluation only), and the
  metric is the distance from the observation's verified position to that actor. Each metric carries its source;
  the dashboard shows it on hover and shows "no ground truth" instead of a number when there is none. The audit
  log shows readable summaries for the new event kinds.
- Labelled simulated benchmark: `make bench-incident` (`backend/scripts/benchmark_incident.py`) runs DEMO-1 for 60 s
  with a scripted operator (reviews after 3 s using simulator ground truth) and a scripted responder (acknowledges
  after 2 s). Result `evaluation/benchmarks/run-095949`:
  - 4 candidates: 1 confirmed (the volunteer), 3 rejected.
  - 4 of 4 observations have a verified position.
  - Detection to review: median 3.18 s, including the scripted 3 s.
  - Confirmation to alert: 1.05 s, including the rules cycle and approval.
  - Acknowledgment: 2.06 s, including the scripted 2 s.
  - 1 track loss.
  - Localization error: median 1.15 m, max 1.90 m (n = 3).
  - Metrics replayed from the log match the live values.
- Browser check: the Metrics tab shows the source of every value on hover and a localization error of 1.1 m
  (n = 2) for the live demo; the audit log lists track and decision-proposal events.
- Gate: 2 new tests; `make test` 161 passed; build OK; `make eval-quick` passes (report
  `evaluation/reports/run-050321`).

### Phase 12: 3D scene viewer and Indoor (done)

- `backend/overwatch/incident/scene.py`:
  - Scene assets (mesh, point cloud, splat), each in its own frame (`scene:<id>`, or `arkit:<id>` for scans),
    with provenance, how the asset was made (`origin`) and a `reconstructed` flag.
  - PLY, OBJ, .splat and .ksplat files are validated on upload. A 3D Gaussian PLY is recognised from its
    `f_dc_*`, `opacity`, `scale_*` and `rot_*` properties.
  - Transform chains are resolved through valid stored transforms (inverses, up to 3 hops; expired transforms
    unused).
  - The overlay always draws an asset's own annotations. Annotations from other frames are drawn only through
    a valid chain; otherwise it names the frames it could not align.
- API:
  - Assets: list, raw-body upload (no new server dependency), `transform.json` import, file download.
  - Simulator geometry export: a SIMULATED incident only, exactly aligned by construction, labelled "not a
    reconstruction".
  - Synthetic sample indoor scan: labelled SYNTHETIC and unaligned.
  - Overlay query.
- Dashboard: a Scene tab (Indoor mode opens on it) with orbit, pan and zoom.
  - Rendering: PLY and OBJ meshes and point clouds through three.js. Gaussian splats through GaussianSplats3D
    0.4.7 (MIT, lazy-loaded); if the splat renderer fails, the splat centres are drawn as a point cloud.
  - Badges for provenance, kind, alignment (method and residual) and "not a reconstruction".
  - Clickable annotation labels show the time, provenance, frame of record, the transform path used, the
    uncertainty, and the evidence.
  - Room labels are placed by clicking the scan. An operator-entered alignment (yaw, east, north) is labelled
    as such.
- `scripts/reconstruction/`: frame extraction (ffmpeg), COLMAP, splatfacto (nerfstudio) and import scripts, plus a
  README with the import format. **Not run here (no GPU)**; the scripts were syntax-checked only.
- Browser check (1440 px and 768 px):
  - Indoor opened the Scene tab. The synthetic sample scan rendered 34,870 points, and the incident overlay was
    refused with the missing frame named.
  - A "Kitchen" room label placed by clicking the scan showed at once.
  - After an operator-entered alignment, 5 annotations appeared. Clicking "possible person" showed "shown via
    inverse of TF-… (manual (operator-entered offset))" and opened the evidence.
  - A synthetic 2,000-Gaussian test PLY rendered through GaussianSplats3D. It is a renderer test, uploaded via the
    API and labelled SYNTHETIC.
- Gate: 8 new tests; `make test` 169 passed; build OK; `make eval-quick` passes (report
  `evaluation/reports/run-051402`).

### Phase 13: Follow mode (done)

- `backend/overwatch/missions/follow.py`: the Follow target is whatever track an operator designated
  (`POST /api/incidents/{id}/follow/designate`) or confirmed (`/follow/confirm`). The pipeline's subject lock keeps
  tracking on its own and may re-bind after an occlusion. Follow treats any track a person did not designate or
  confirm as "unverified: confirm or re-designate" and logs `follow_rebind_unverified`; identity never switches
  silently.
- Trail: every observation of the bound track, with time, image box, and a ground position only when calibrated.
  Points recorded while the binding is unverified are marked as such and drawn dashed on the map.
- Events: `follow_designated`, `follow_redesignated` (with who), `follow_lost`, `follow_reacquired`,
  `follow_rebind_unverified`, `follow_released`, and `follow_point` at about 1 Hz.
- Motion statistics (distance, speed, heading) come only from trail points with a verified position on a confirmed
  track. Distance counts only moves larger than twice the position uncertainty: in the browser check a stationary
  person at ±3 m first accumulated "1.7 m travelled" from jitter, which this fixes. Speed under the uncertainty
  reads "0 m/s (displacement within the position uncertainty)". No facial recognition, identity or intent.
- **Look-alike finding (OW4):** the subject leaves the camera view at about 10.5 s. At 14.3 s the pipeline lock
  re-binds to the look-alike, labelled UNVERIFIED by the lock itself (appearance 0.77, 14.3 m from the prediction).
  The evaluator's "subject lock correct" (1.0) does not show this, because it counts only steps where the subject
  is visible. Follow marks the binding unverified from 15.0 s and never counts the look-alike's movement. The test
  checks this against simulator ground truth, counting only steps where the two boxes are clearly apart.
- Dashboard: in Follow, "Designate target" uses the incident endpoint. A Follow panel shows status, who
  designated, confirm or release, and the motion line with its source. The map draws the trail.
- Fixed in the browser check: the Scene tab kept the previous incident's splat on screen after a new incident
  started, and stayed open after leaving Indoor.
- Browser check (1440 px): designated person #1 → "TRACKING drone:1:e2, designated by operator"; trail and motion
  line updated; release logged.
- Gate: 5 new tests (including the OW4 look-alike run); `make test` 174 passed; build OK; full `make eval` (report
  `evaluation/reports/run-052841`): no failures, every scenario expected to pass passed, and every per-scenario
  number is identical to phase 9.

### Phase 14: the four simulation worlds (done, reduced scope)

- Simulator: a scenario can bring its own world (bounds, ground painter, objects), a scripted pilot path (manual
  flight; nothing automated) and hazard layers. Hazards are drawn per pixel into the drone image from the ground
  model: water skips roofs, tree crowns and people; smoke covers people. Colours stay below the simulated
  detector's saturation threshold so hazards don't read as people. A per-world evaluator scores against ground
  truth.
- New models, each with unit tests:
  - `sim/hazards/water.py`: bathtub flood model h(t); road graph closed above 0.3 m; Dijkstra and alternative
    routes.
  - `sim/hazards/fire.py`: arrival times by shortest path over a wind-weighted spread grid (ember-style);
    firebreaks; keep-out; Gaussian smoke plume.
  - `geometry/probability.py`: probability-of-area prior with illustrative profile parameters, Bayesian update,
    random-search POD.
  - `autonomy/coverage.py`: lawnmower paths with footprint-based spacing; shown to the pilot, never sent as
    commands.
  - `sim/sensors/thermal.py`: SIMULATED THERMAL from ground truth, no temperatures, label burned in.
  - All hazard models are labelled as screening models, not forecasts. Fire and flood run time-compressed for the
    demo, and the compression is stated.
- Worlds and scenarios (all with limits; all pass):

  | World | Scale here (plan) | Scenarios | Not built |
  |---|---|---|---|
  | Police pursuit | 150 by 110 m (400 by 300 m) | PP1 sidewalk follow, PP2 carport cover (lost then reacquired), PP3 look-alike crossing, PP4 running under street trees | PP5 to PP10 |
  | Search and rescue | 240 by 240 m, flat (1.5 km, heightmap) | SR1 clearing (found), SR2 two decoys (exactly 2 false objects, found), SR3 under canopy (not found, sector keeps 0.205 probability) | SR4 to SR8 |
  | Flood rescue | 180 by 180 m | FR1 three stranded people in rising water (3 of 3 found, 0 false), FR2 crew route at 0.28 m water while the 0.37 m route is removed | FR3 to FR8 |
  | Firefighter routing | 120 by 120 m | FF1 crew route to the nearest door outside the keep-out (0 keep-out cells), FF2 smoke over all 6 openings ("unclear") and an occupant found | FF3 to FF8 |

  The scales are cut because the OpenCV renderer textures the whole world. Heightmap terrain, a road graph from
  OpenStreetMap and glTF assets are not built.
- Dashboard:
  - Every world shows as ready with its scenarios and a "Not built" line.
  - Choosing a scenario in another world starts a new incident with that world's mission. Before this fix, a
    scenario switch kept the old incident and its candidates, found in the browser check.
  - The map reloads the world's bounds and orthophoto when the world changes and draws water, fire, keep-out and
    smoke areas with the model label. The orthophoto was cached once for every world; it is now cached per world
    with a world-specific URL.
  - "Sim thermal" shows the labelled simulated thermal inset.
- D6 to D11 wired into the worlds' evaluators: D8 next sector from the posterior POA in SR, D9 route and D10
  dispatch in FR, D11 opening condition in FF. D6 cases come from pursuit geometry in the report.
  `scripts/worlds_decisions_report.py` runs seed-paired engine-versus-rules reports on cases generated from the
  world models (labelled simulated; agreement is with the rules, not with field outcomes).
  - The mock engine agrees with the rules by construction.
  - The real engine (gpt-6-luna via the configured key; 6 seeds per task, 35 calls, 13,937 tokens) agreed with the
    rules: D6 0.5, D7 0.833, D8 0.833, D9 1.0, D10 1.0, D11 1.0.
  - Acted fractions after the confidence gate: D6 0.0, D7 0.667, D8 0.5, D9 0.667, D10 1.0, D11 0.167.
  - D6 confidences were 0.16 to 0.52, so the rules flew every case.
  - Reports: `evaluation/benchmarks/run-105336-worlds-decisions-openai` and `…-105415-worlds-decisions-mock`.
- Bugs found and fixed by the first real-engine run:
  - D11: the model answered "no obstruction seen" for openings under smoke or out of view and was acted on (1 of 6
    agreed with the rules). Options the data cannot support are now removed by code before asking; agreement
    became 6 of 6.
  - With a single option left, the API rejected the question ("choices array too short"). The router now answers
    from the rules without a model call.
  - The mock engine gave a single remaining option a probability below 1, which the validator correctly rejected.
- Browser check (1440 px and 768 px): FF2 showed the house, the occupant, the fire map layers and the thermal
  inset. FR1 started a new flood incident whose Search flagged the stranded people with evidence. No horizontal
  overflow at 768 px.
- Gate: 9 new tests (5 hazard and model tests, 3 world tests, 1 decision test) plus 2 updated designed cases;
  `make test` 184 passed after updating the use-case test, whose old assertion said the worlds were planned; build
  OK. Full `make eval` (report `evaluation/reports/run-061121`): 38 scenarios, every one with a pass criterion
  passes (28 = 17 existing + 11 new), no failures. The 27 existing scenarios' numbers are identical to phase 13.
### Phase 15: drone simulation accuracy (done; hardware checks outstanding)

- `sim/aircraft.py`: aircraft profiles where every value carries its source: spec sheet, Overwatch cap, model file,
  assumed or measured. The DJI Mini 2 profile uses spec-sheet mass, speeds, flight time and battery energy. Its
  response time constants are marked "assumed" (not measured), and the kinematic defaults equal this profile
  (tested). Skydio X2 reads its mass from the MuJoCo Menagerie model file.
- `sim/wind.py`: low-altitude Dryden turbulence (MIL-F-8785C) with first-order shaping filters, exact for u and
  approximate for v and w (stated). Also an energy-based battery whose hover power is derived from spec energy and
  flight time, with an assumed speed dependence. Both are opt-in, so existing scenarios behave identically.
- `sim/step_response.py`: velocity step-response harness (delay, 10 to 90% rise, steady state, RMS) with the plan's
  pass criteria (15%, 0.3 m/s) and a CSV recording format.
- `tests/unit/test_aircraft_fit.py`: profile sources; kinematic rise time equals ln(9) τ; the comparison passes on
  matching models and fails on slow ones; Dryden σ and lag-1 correlation; hover endurance equals the spec 31 min.
  The DJI recording fit test is **skipped with its reason**: no DJI simulator step recordings exist (hardware:
  Mini 2 plus bridge).
- The kinematic model has no acceleration or tilt limit and is not validated against a Mini 2; it is not changed
  without recordings. (The model-to-model cross-check was removed on : the project works with DJI
  aircraft only.)

- Gate: 6 new tests (5 pass; 1 skipped with its reason); `make test` 192 passed, 1 skipped; build OK. Full
  `make eval` (report `evaluation/reports/run-063040`): 28 of 28 pass criteria met, no failures, every number
  identical to phase 14. The new modules are opt-in, and the evaluator does not import them (checked).
### Phase 16: near-IR and extras (done)

- `sources/near_ir.py`: `NearIRVideoSource` over a file, webcam or RTSP stream (selected as `near_ir` in the source
  manager, LIVE backend). Frames go to the pipeline as mono and are enhanced only when the operator turns it on.
  Each frame's metadata lists the processing steps.
- Enhancement (denoise, CLAHE, gamma, contrast stretch) is labelled "IMAGE PROCESSING (near-IR, not thermal; no
  temperatures)". A raw-versus-processed preview works on any recorded frame in any mode
  (`POST /api/near-ir/preview`), and on the live near-IR source (`/api/sources/near-ir/preview.jpg`).
  Enhancement settings are logged as a source update.
- Dashboard: a near-IR preview in the Source health tab.
- Tests: enhancement off by default and labelled when on; the dim figure's contrast at least doubles; source
  frames are mono with their processing in the metadata; no temperature appears anywhere in the source's
  description; the preview endpoint labels its processing and rejects non-images.
- Not possible here: native iOS LiDAR export (needs Xcode on a Mac) and ESP32 haptics (needs the hardware).
- Browser check: a darkened recorded frame through **Source health → Near-IR enhancement preview** came back as
  raw and processed side by side, captioned with its steps (denoise, CLAHE 2.5, gamma 0.8, contrast stretch). The
  processed half is headed "IMAGE PROCESSING (near-IR, not thermal; no temperatures)".
- Gate: 3 new tests; `make test` 192 passed, 1 skipped (it includes them); build OK; `make eval-quick` passes
  (report `evaluation/reports/run-063344`).

### Phase 17: full demo and handoff (done)

- `backend/scripts/e2e_demo.py` (`make e2e`) uses Playwright 1.48 (installed into the backend venv) to drive the
  system Chrome headless. Each run starts a fresh backend: SIMULATED, its own database, API keys blanked so the
  labelled mock answers and nothing leaves the machine. The steps:
  1. Start the demo incident; a candidate is flagged.
  2. Confirm it; "Notify responder" is recommended; approve it, and the brief is sent.
  3. The responder, in a phone-sized context, acknowledges it.
  4. Switch to Structure and set side A; openings group by side.
  5. Open the 3D world and replay the timeline.
  6. The log holds the core event kinds.

  Run twice: **every step passed both times with identical outcomes** (final run `evaluation/e2e/run-113908`; first run `…-111332`,
  with screenshots).
- Docs: README (incident-command section, current test numbers), `.env.example` (incident store), new
  `DEMO_RUNBOOK.md`, `ARCHITECTURE.md` and `LIMITATIONS.md`, `HARDWARE_INTEGRATION.md` (near-IR, drone model fit),
  and this file.
- Small fix from the screenshots: the Recommendations title was cut off by the engine label. Lint cleanup: type-only
  imports in the incident service; unused names removed.
- Final gate on the finished tree: `make test` 192 passed, 1 skipped (DJI recordings); dashboard build OK;
  `make eval-quick` passes (report `evaluation/reports/run-063907`); `make e2e` passed twice with identical
  outcomes. The last full `make eval` was the phase 15 gate (28 of 28 pass criteria). Code changed after it only
  in the dashboard, lint fixes and docs.

## Final report

### Implemented and tested (automated tests or the scenario suite)

- Incident contracts:
  - UTC timestamps, named frames and transforms, provenance.
  - SQLite store with raw observations separate from reviews, and an append-only event log.
  - REST API and WebSocket stream with ordering.
- Log replay: metrics rebuilt from the log alone equal the live values. Metrics carry their sources; localization
  error appears only with ground truth.
- Demo world DEMO-1 with deterministic replay (identical state hash). Video sources with honest states and
  reconnect counts.
- Missions:
  - Search: grouping, review states, never "clear".
  - Structure: side A designation, sides B to D, fixture labelled, no re-proposal after a restart.
  - Follow: operator-only designation; unverified re-binds, including the OW4 look-alike checked against ground
    truth; motion statistics from verified positions.
- Positions and geo: haversine, bearing, ENU/WGS84, ground projection only through a valid calibration;
  observation location reports.
- Recommendations and decisions:
  - Eight templated recommendations with approval; model proposals validated (flight words, measurements,
    schema, mission rules, data availability).
  - Router: freshness and session counter, probability validation, unsafe and unsupported option removal, the
    one-option rule, cost guard, rules-only label.
  - D6 to D11 with designed cases.
- Responder briefing: grounded range and bearing only; trusted heading for clock directions.
- Scene assets: validation, transform chains, overlays refused without alignment, simulator export, synthetic
  indoor sample.
- The four worlds:
  - Hazard screening models (water, fire, smoke), probability of area, coverage paths, simulated thermal.
  - 11 world scenarios with limits, all passing, plus the 27 original scenarios unchanged.
- Aircraft profiles with value sources, Dryden statistics, energy battery, step-response harness.
- Near-IR enhancement labelling and a source that never reports temperature.
- The full demo end to end in headless Chrome, run twice.

### Implemented, checked in the browser, not covered by automated tests

- Dashboard panels:
  - Calibration ground-point pairing.
  - Recommendations approve and reject.
  - Follow panel and the trail on the map.
  - Scene viewer: room labels by clicking, operator alignment, annotation details and evidence, and splat
    rendering through GaussianSplats3D (on a synthetic test splat).
  - World hazard map layers, the simulated thermal inset, the near-IR preview.
  - Layouts at 1440 and 768 px; the responder at the mobile preset.
- The incident benchmark (`make bench-incident`) and the D6 to D11 engine-versus-rules reports (run once each; the
  numbers are in the phase entries).

### Simulated (and labelled so wherever it appears)

- Everything shown with SIMULATED: drone video, people, the four worlds, the structure opening fixture, the
  simulator geometry export, the sample indoor scan, simulated thermal, the mock decision engine.
- Hazard models are screening models, time compressed for the demo. Lost-person profile parameters are
  illustrative.

### Hardware-dependent (not tested here)

- DJI Mini 2 through the iOS bridge (DJI MSDK V4); DJI simulator step recordings for the model fit (that test is
  skipped until they exist).
- iPhone responder app on a real phone (speech, vibration, AirPods, GPS heading); near-IR camera footage.

### Not implemented

- World scenarios PP5 to PP10, SR4 to SR8, FR3 to FR8, FF3 to FF8.
- Heightmap terrain, OpenStreetMap import, glTF world assets, offscreen glTF rendering, a photoreal tier.
- Open-vocabulary door and window detection (OWLv2, Grounding DINO); not installed.
- Reconstruction runs (COLMAP, splatfacto): GPU only; scripts documented, nothing reconstructed.
- Native iOS LiDAR export (needs Xcode on a Mac), ESP32 haptics.
- Kinematic model changes from real recordings: none exist yet.

## Extensions after the 18 phases

### E1: larger worlds and object memory (done)

- **Worlds at plan size:**

  | World | Size | Contents |
  |---|---|---|
  | Pursuit | 400 by 300 m | 3 by 3 street grid, 153 boxes, street graph as map data |
  | Search and rescue | 1.5 by 1.5 km | about 15,900 trees, 40 outer clearings, 3 by 3 sectors of 500 m |
  | Flood | 500 by 500 m | 215 houses, extended road grid |
  | Fire | 200 by 200 m | scrub, two outbuildings |

  The original scenario areas are unchanged (the search core's trees are placed exactly as before), and all 11
  world scenarios still pass (report `evaluation/reports/run-121510`).
- **Rendering at this size:**
  - The renderer culls by view for large worlds (more than 60 static objects), so the training field still
    renders exactly as before.
  - Tree geometry and shadows are cached, and canopies are projected in one batch.
  - The phone's simulated LiDAR depth pass considers only objects within 60 m; LiDAR confidence is zero past
    4.6 m anyway.
  - The search world went from 2.2 s to 207 ms per drone frame, including the phone. It runs at about half real
    time on this CPU.
  - The 3D view gets the 3,000 trees nearest the drone, with a note saying so. World textures are cached per world,
    two at a time.
- **Object memory** (`tracking/memory.py`): Permanence's ideas, reimplemented; that repository has no licence, so
  none of its code is used.
  - Persistent people in the ground frame: VISIBLE, OCCLUDED (canopy at the remembered spot in the image),
    OUT_OF_VIEW (outside the projected image) or LOST (0.6 s unexplained).
  - States change only after 3 frames agree, and confidence fades at a different rate in each hidden state.
  - A new track is attached only when it is the single plausible match: inside the walking-reach gate, matching
    appearance, clearly better than the next candidate, and with no other person within 2.5 m (a crossing).
  - "continued" (explained absence, tight gate) versus "possible" (anything wider).
  - Logged as `object_*` events, shown on the map (fading circles of possible area) and in the drone video (dashed
    ghost boxes).
  - Search flags one candidate per remembered person; a "possible" attachment still gets its own candidate.
- **Identity persistence** (new evaluation block `memory`, report `evaluation/reports/run-123026` and the
  rerun after the crossing guard): ids per simulated person, tracker versus memory.

  | Scenario | Tracker ids | Memory ids |
  |---|---|---|
  | S05 | 3 | 2 |
  | OW2 | 3 | 2 |
  | S04 | 5 | 4 |
  | S14 | 4 | 3 |
  | PP2 | 2 | 1 |
  | PP3 | 5 | 5 |
  | OW4 | 4 | 4 |
  | PP4 | 1 | 1 |

  Wrong merges by the memory: 0. Before the crossing guard there were 3, in OW4 and PP3. The remaining wrong
  identities in OW4 (1) and PP3 (2) are tracker swaps, where one track moves between two look-alikes before the
  memory sees anything.
- Gate: 5 new unit tests; `make test` 197 passed, 1 skipped; build OK; `make eval-quick` passes (report
  `evaluation/reports/run-124237`).

### E2: autonomous pursuit in the simulator (done; commit c9a3448)

- Per-world geofence; a hold (VERIFY: camera yaw and gimbal only, no translation) whenever the subject lock re-binds
  without a person's confirmation.
- Decision task D12 picks among code-computed routes on the street map (street ahead, intersection branches,
  orbit of the last-seen spot, last sighting of a remembered person matching the operator's "look for" words, hold).
  Unsafe routes (outside the geofence) are removed before the engine is asked.
- Operator inputs: "who to follow" in words (D3 proposal, confirmed by a person) and "look for".
- Scenarios PP5 to PP7. Gate report `evaluation/reports/run-133152`: demo, OW1 and S05 pass; PP5 to PP7 pass
  (subject found again 3.5 s after loss, zero steps following the look-alike). Known limitation: one memory wrong
  merge in PP6.
- Not done: a D12 report against the real engine.

### E3: navigable demo page (done)

- `/` is now a demo page; the full incident-command dashboard moved to `/dashboard` (`/ops`, `/3d`,
  `/responder` unchanged). The page has every simulation grouped by world, a **Start here** card, the drone camera
  or the 3D world, a map, "Now playing" status in plain words, the latest events, and play, pause and restart.
- New scenario **PP0** (a person runs, the drone follows): one volunteer jogs round two corners; the scripted demo
  operator designates them once and the simulator autonomy follows. Headless run (report
  `evaluation/reports/run-134641`): PASS, the subject in frame for 100% of the 726 follow steps, lock correct
  1.0, no ID switches, no losses.
- The live server now uses the same scripted operator as the headless runner (`OperatorEmulator`), so the
  "operator_watch" and "look_for" actions of PP5 to PP7 also work in the dashboard. Every lock it makes is labelled
  "scripted operator (demo)".
- Fix: a subject lock made outside the Follow panel (the ops console, the scripted demo operator) now becomes the
  Follow designation. The lock's engage time changes only when a person engages it, never on an automatic
  re-bind, so re-binds still need confirmation.
- Tests: `test_pursuit_demo_scripted_lock_shows_in_follow_and_drone_follows`; the use-case test expects PP0 first.

### E4: demo controls, dispatch, detailed people, rules-first pursuit, the jev-drone stack (done)

- **Camera feed.** The simulated feed is clean by default: a little gimbal wobble, no sensor noise, no vignette, no
  compression, no motion blur (fault windows still add them on purpose). Between simulator frames the video pane no
  longer dims to "NO DRONE VIDEO"; and the server renders at least every 0.3 s of simulation time when a large world
  cannot keep up, so the feed slows down instead of going stale.
- **People.** Jointed figures: head with hair and a face, neck, torso (shirt, open jacket, vest, backpack), arms with
  elbows and hands, legs with knees, shoes, an optional hat; limbs swing with the walking phase, runners lean. Skin,
  hair, sleeve and trouser length vary per person (deterministic from the actor id); clothing colours stay as the
  scenario sets them, and look-alike pairs (OW4, PP3, PP6) are kept identical. The 3D view draws the same figures,
  animated. Measured on PP0 at 24 m: YOLO26 tiled went from 3 of 40 people to 34 of 40 (6 false positives, about
  200 ms a frame on this CPU); the colour-blob stand-in stays at 40 of 40 at 11 ms. The blob detector stays the
  default; a scenario can set `detector` to use YOLO.
- **Operator-driven people.** `POST /api/simulation/people/{id}` with goto, drive, stop or resume; the demo page's
  People panel sends a person to a map point or steers them with the arrow keys while the drone follows. The
  scripted behaviour waits and resumes where it left off.
- **Dispatch (PP8, SR4).** Each world has a launchpad. `POST /api/autonomy/dispatch` (or the demo page) takes a
  clothing description and where the person was last seen; the drone takes off, flies to the area, flies lawnmower
  legs, matches the people its detector finds against the words (several frames in a row, never a face, ambiguity
  means nobody is chosen), hands over to Follow on a match, and reports the target's distance, bearing and compass
  point from the launch point (plus lat/lon) every 4 s. **Wrong person** releases the match and the search resumes.
  Headless: PP8 matched the red shirt 50 s after dispatch with 3 people seen and 0 wrong matches, 58 reports with a
  0.5 m mean distance error; SR4 matched the yellow jacket in 39 s, found the lost person, 0 false candidates, 54
  reports with 0.7 m error. Seen live on the demo page: the match, the position line, a rejection and the resumed
  search.
- **Pursuit without the API.** `decisions.pursuit_engine: rules` (default) answers D1, D4 and D12 with the rules
  and makes no API call while following; `model` restores the engine.
- **The jev-drone stack (PJ1, PJ2).** `autonomy/reflex.py` reimplements the jev-drone ideas (MIT): five forward range
  sectors at the aircraft's height and higher (from the simulator's geometry, a depth-camera stand-in with no noise),
  a reflex that brakes for what is in the path, steers to the wider side and backs off, and D6 asked only when
  something is close (TypeSafe Jev when its key is present; otherwise the configured engine, the labelled mock in
  headless runs). The MuJoCo Skydio X2 flies the same pursuits at 7 m. Honest result: the X2 followed the runner
  round the corner with D6 sidestepping for the street trees.
- **Batch (report `evaluation/reports/run-165258`, all five pass):**

  | Scenario | Result |
  |---|---|
  | PP0 | subject in frame 1.00 of the follow steps, lock correct 1.0, 0 id switches |
  | PP8 | matched 48.1 s after dispatch, 3 people seen, 0 wrong matches, 60 position reports, 0.6 m mean distance error |
  | SR4 | matched in 38.6 s, lost person found, 0 wrong matches, 54 reports, 0.7 m error |
  | PJ1 | in frame 0.994, found again after the corner, min clearance 4.1 m, 0 collisions, reflex active 0.7% of steps, 5 D6 judgments (mock engine: sidestep right) |
  | PJ2 | in frame 0.956, found again, min clearance 2.2 m, 0 collisions, 3 D6 judgments (mock) |

  The D6 engine in headless runs is the labelled mock; with `TYPESAFE_API_KEY` set it is Jev, and live it is the
  configured engine. The sectors come from simulator geometry, so the clearance numbers say nothing about a real
  depth camera.
- Tests: `tests/unit/test_dispatch_and_reflex.py` (5). The world test counts PJ as pursuit; the OW4 follow test
  accepts a rejected re-bind (the lock no longer re-binds to the look-alike at all, which is the safer outcome);
  the incident-log test no longer assumes Search flags nobody meanwhile. `make test`: 210 passed, 1 skipped.

### E5: chases start on the pad, a city world, a phone controller over a Cloudflare tunnel (done)

- **Chases start on the ground.** PP0 and both city chases begin with the drone on the base's pad (the 3D view shows
  the pad, its H and the hangar); the scripted operator dispatches it at 2 s, it launches, climbs, flies to where the
  person was last seen, matches the description and follows. A world test now requires any scenario that starts on
  the ground to start on its launchpad and to dispatch.
- **City world** (`sim/worlds/city.py`): a 400 by 400 m downtown grid, five streets each way 70 m apart, blocks of
  two to four buildings 12 to 48 m tall, a plaza, the police base in the south-west corner, a street graph for D12.
  The chase altitude is 45 m, above most roofs, so the towers hide the target at the corners. CT1 is the
  phone-controlled chase (the target walks a loop by itself after a minute if nobody drives it); CT2 is scripted:
  the target cuts behind the towers at each corner.
- **Phone controller** (`/drive`): a joystick page for a simulated person (the chase target by default), speed
  walk/jog/run/sprint, hand back to the script. It renews a drive command every 250 ms while pushed; the server
  lets a command lapse after 0.6 s, so a dropped connection stops the person rather than running them away.
- **Cloudflare tunnel**: `scripts/tunnel.sh` / `make tunnel` runs a `cloudflared` quick tunnel to the backend (which
  serves the built dashboard) and prints `<url>/drive`. Checked once: a quick tunnel came up in about 30 s. The demo
  page's "Drive the target from a phone" panel builds the phone link (with the access token when one is set).
- **Headless CT2** (report `evaluation/reports/`, batch of , evening): matched the red shirt 31.5 s after
  dispatch, 0 wrong matches, in frame 98.5% of the follow steps, hidden by the buildings twice, lost at 86.9 s and
  found again 3.9 s later on a D12 street route (rules), 82 position reports with 0.7 m mean distance error. CT1 was
  watched live: launch from the pad, match at about 60 s, the target driven north from the phone page, position
  reports relative to the base.
- **Headless PP0 from the pad** (report `evaluation/reports/run-171535`): matched 54.8 s after dispatch, 0
  wrong matches, in frame 99.8% of the follow steps, 46 position reports with 0.6 m error. The integration test of
  the scripted lock now uses PJ1 (airborne start) since PP0's match arrives a minute in.
- **QR code.** The server starts the quick tunnel itself (`POST /api/tunnel/start`, `GET /api/tunnel`,
  `POST /api/tunnel/stop`, `api/tunnel.py`) and the demo page draws the phone link as a QR code (the `qrcode` npm
  package). The phone page is now only a joystick and a speed, with the target chosen in the header. Checked live:
  the tunnel came up in about 35 s, the QR carried the public address, and `/drive` and the people API answered
  through it; the tunnel was stopped afterwards.

### E6: Tiger Data (TimescaleDB) mirror (done)

- `incident/tiger.py`: a background writer that batches three streams into Tiger Cloud hypertables: `ow_events`
  (every incident-log entry, as appended, through the store's listener), `ow_telemetry` (position, speed, altitude,
  battery, autonomy mode and FSM at the state rate) and `ow_detections` (every confirmed track with its ground
  position, a few times a second). Continuous aggregates `ow_events_1m`, `ow_telemetry_10s`, `ow_detections_10s`
  with refresh policies and real-time aggregation; compression of telemetry and detections after a day.
- `GET /api/tiger` (connection, rows written, queue, last error; the host only, never the credentials) and
  `GET /api/incidents/{iid}/tiger/metrics` (the rollups for one incident). The demo page's **Live metrics · Tiger
  Data** panel draws detections per 10 s, people tracked, altitude, speed and events per minute from them.
- Configuration: `TIGER_DATABASE_URL` in `.env`, or, when unset, the service URL read straight from the Tiger
  Cloud credentials file at the repository root (`tiger-cloud-*credentials*.txt`, or `TIGER_CREDENTIALS_FILE`).
  Both are gitignored; the value is never logged and the API reports the host only.
- `tests/integration/test_tiger.py` round-trips two events, five telemetry rows and one detection through the real
  service, checks the hypertables and the aggregates, then deletes its rows; it skips without the variable or when
  the service cannot be reached. The hostname did not resolve for the first half hour after the service was
  created (NXDOMAIN); once it did, the test passed against the real service in 3 s: the three hypertables and the
  three continuous aggregates were created, 8 rows round-tripped and came back through the rollups (real-time
  aggregation), and the rows were deleted. `CONNECTION_STRING` is accepted as the variable name too.

### E7: routing from a belief, live crew routing, voice commands, scenario stories (done)

- **Pursuit routing from a belief (P1).** `autonomy/belief.py` (numpy only): when the subject is lost, 500 particles
  per identity hypothesis on the directed street graph, advanced at 5 Hz with turns straight 0.6, left and right
  0.18, reverse 0.04 times an edge weight (cover higher, towards the drone lower), stop-and-hide under cover, speed
  jitter, systematic resampling below N/2 effective particles; `mass_by_edge()`, `mass_by_node()`. Negative
  information is a detection rate (fast in the open, slow under roofs and trees) over the camera footprint, and a
  look-alike seen walking out of view does not reweight the hypotheses (they exist under every hypothesis).
- **Hypotheses.** H1 is "the subject, unseen"; every remembered person (ObjectMemory) who looks like the subject
  (clothing similarity at least 0.5) and could have got there becomes another, with prior similarity times
  reachability. The operator's "look for red shirt, jeans" is parsed into a clothing descriptor
  (`vision/appearance.py`: `parse_description`, `descriptor_likelihood`) and multiplies the hypothesis weights; that
  is the only way operator text reaches routing (it was removed from D12's facts).
- **Viewpoints and route.** `autonomy/pursuit.py: coverage_candidates`: look points on every street holding more than
  2% of the mass plus the three busiest intersections, scored by the detection-weighted belief mass inside the camera
  footprint from there over a common 10 s horizon, minus 0.004 per second of flight, minus
  `SafetySupervisor.check_viewpoint` penalties (rejected viewpoints are removed); the route is the shortest safe path
  on the street graph (`StreetGraph.route`), flown as a hover-and-look at the end. D12 gets the top three with the
  argmax as the rules baseline (confidence gate and single-option short-circuit kept), re-planned every 2 s. The
  belief, candidates and route are published as `WorldState.pursuit` with provenance and logged
  (`belief_started`, `belief_hypothesis_added`, `belief_hypothesis_dropped`, `pursuit_route_changed`,
  `belief_ended`).
- **Re-bindings.** `LockState.reject_rebind` ("Not them" in the Follow panel, `POST /api/incidents/{iid}/follow/reject`)
  restores the pre-rebind search and drops that person's hypothesis for good (they are never added back in that
  belief). The scripted demo operator uses it in PP5 to PP7.
- **Clothing evidence is voted.** One frame's colour word for trousers at 28 m is close to a coin flip (measured on
  PP7: the subject's jeans read blue in 64% of frames, gray in 36%; the look-alike's dark trousers read green from the
  grass between the legs). The belief now takes the subject's colours from a vote over every verified frame of the
  designation (across re-bound tracks) and each remembered person's from a vote over their frames; dispatch and the
  rest of the pipeline keep the per-track colours unchanged.
- **Memory: no merge behind a runner.** A remembered person last seen running (2 m/s or more) is no longer attached
  to a new track that appears well behind where they were heading; the refusal is logged as
  `object_attach_refused`. Seen live in PP6: the look-alike's object had been given the real subject's new track.
- **PP5 to PP7** were redesigned so D12 is actually exercised: a 56 m covered walkway on Oak Street hides the
  subject. PP6 and PP7 add a look-alike (same red shirt, dark trousers) who steps out of the walkway's far end. Their
  lock searches for 45 s before asking the operator (default 20 s), and PP6 runs 75 s.

- **Results** (headless, rules engine; reports `evaluation/reports/run-210803` (PP0 to PP8),
  `-211256` (PJ, CT, VC), `-205515` (demo, OW), `-210211` (SR, FR, FF)): 37 scenarios, all 34 with a verdict pass.

  | Check | Result |
  |---|---|
  | PP5 | one hypothesis; all 9 D12 picks "street ahead (Oak Street between Main St and North Rd)"; found again 19.1 s after the loss |
  | PP6 | two hypotheses, 0 memory wrong merges; at 41.1 s the pick covers both (46% of the subject's cloud, 80% of the look-alike's); from 46.5 s the drone checks the look-alike's cloud on North Road (in the open, the better bet at 50/50), finds nobody, and the subject's weight recovers; found again 31.4 s after the loss, inside the 45 s search |
  | PP7 | "look for red shirt, jeans" at 44 s: the subject's hypothesis goes to 0.87; the route stays on Oak Street where PP6 turns east; found again 23.6 s after the loss |
  | PP5 to PP7 cross-check | `[PP5-7] routes differ: PASS` (after 44 s PP6 flies P1 then P3, PP7 only P1) |
  | FR2, FF1 | live route equals the offline evaluator's: FR2 R1 with at most 0.29 m of water; FF1 door C with 0 keep-out cells (the crew leaves at 20 s) |
  | VC1 | "Follow the person in the red shirt" resolved to the subject and was TRACKING within 3 s of approval; "Stop" held on the next tick; "Go to the house with the carport" asked which of three and flew to the answer; every step logged |
  | VC2 | "Search sector two" started the sweep of S2; logged |

- **Live checks** (check server on :8010, dashboard on :5190): PP6 (before the last two fixes, memory and colour
  votes) and PP7 (final code) played end to end, with the route card showing both hypotheses and the viewpoints,
  and the subject re-found and confirmed by the scripted operator at about 52 s in each run. Earlier live PP6 and
  PP7 runs on a saturated machine timed out or chased the look-alike; those runs found the restart, merge and
  colour bugs above. SR2 left six "possible person" candidates, all pending review; FR2 and FF1 drew the crew route on the
  map and the water, fire, keep-out and smoke in 3D; voice commands typed in the panel (Stop, follow with approve)
  reached the server and were logged; switching PP6 to VC1 to PP1 retitled the incident. On a loaded machine the
  live server falls behind real time and throttles rendering, so live timings differ from headless ones.

- **Live crew routing (P2).** `missions/crew_routing.py`: time-dependent Dijkstra carrying the arrival time
  (flood: a road is closed if the water on it exceeds 0.3 m when the crew would leave it; fire: a cell is usable only
  if the crew is there two compressed minutes before the fire; a fully smoky metre costs as much as four clear ones). `CrewRoutingService` re-plans every 2 s
  (the hazard update), publishes `WorldState.crew_routing`, logs `crew_route_planned` on a change and offers D9 the
  alternatives; drawn on the map and in 3D. FR2 and FF1's `_eval` call the same planners and assert that the live
  service published the same route (`live_matches_offline`).
- **Voice commands (P3).** `voice/commands.py`, `voice/grounding.py`, task D13 (FOLLOW, SEARCH, GOTO, ORBIT, LOOK,
  HOLD, RTL, FIND, UNCLEAR with `has_location_reference`, `has_person_description`, `is_cancel`): keyword grammar as
  the baseline and default engine (`decisions.voice_engine: rules`), below 0.7 it asks; HOLD and RTL bypass the
  router. Grounding against named entities (streets, intersections, houses with carports, sectors, landmarks, people
  by clothing); two plausible answers are a question. Primitives in the MissionAction shape with an approval policy
  (FOLLOW always; SEARCH and GOTO for a real aircraft, or with the simulator toggle). Every step is an incident event
  (`voice_transcript`, `voice_intent`, `voice_question`, `voice_grounding`, `voice_primitive`, `_approved`,
  `_rejected`, `_executed`, `_failed`, `voice_reply`). Dashboard: the Voice panel (browser speech or typed).
- **Scenario stories (P5).** `sim/worlds/manifests/*.json` (title, problem line, beats with caption, entity and
  camera preset, success line) for PP0, PP5 to PP8, SR1 to SR3, FR1, FR2, FF1, FF2, a default for the rest; the demo
  page plays them over the video or 3D view. Route reasoning card (hypotheses with weights and evidence, viewpoints
  with per-hypothesis coverage bars, baseline vs model pick and confidence) on `/` and `/dashboard`; the particle
  clouds in two colours on the map and in 3D. Water, fire, keep-out and smoke in 3D. The header retitles on a
  same-world scenario switch.
- **Found by the live checks and fixed:** the voice endpoints answered 422 (their request models were local to
  `create_app`, which FastAPI cannot resolve under postponed annotations); an expired belief restarted every 120 s
  from the same old sighting; a rejected look-alike came straight back as a new hypothesis; buildings were labelled
  "house house-5"; the fire crew label showed the internal id; smoke drew as squares; the incident log named D12's
  pick only by id (now `pursuit_route_changed` carries the label, coverage per hypothesis, score and ETA).
- **Not done:** ORBIT and LOOK (recognised and declined); no D12 or D13 report against a real model engine; unit
  tests were not run in this pass (by request: manual and scenario checks only), so `tests/unit/test_pursuit.py`
  and others may need updating.

### E8: simulation speed, static flood and wildfire rescues (done; not run here)

- **Speed.** 1x, 2x, 3x buttons in the camera-view toolbar of the simulation workspace, for the main engine
  (`/api/simulation/control` speed) and the City engine (`POST /api/test12/speed`, a pace multiplier in its run
  loop; the toolbar shows the pace it actually achieves when YOLO cannot keep up). Measured before the buttons moved:
  2.01x and 2.94x on the main engine.
- **FL1, flood (static).** The same 500 m neighbourhood with a pad by the staging area; the water level is fixed at
  0.75 m (no rise), with three raised mounds that stay dry; three people stand on them at fixed positions (the same
  every run). The rescue mission launches the drone, sweeps a 70 m radius round the reported spot (the survey map
  fills in), matches the yellow shirt, puts a beacon on the person and routes the crew along dry roads.
- **WF1, wildfire (static).** On the 1.5 km search-and-rescue woodland (its trail, creek, clearings and 15,900
  trees, charred and shrunk inside the burn), launching from the trailhead pad: three burning patches in the core
  and a ring of fire round the clearing at (10, -55) with the person inside, fixed positions; YOLO26 as the detector.
  The fire model has fuel only inside those patches and lights them all at once, so the burning area is the patches
  from the first second and never grows; smoke drifts from them and the keep-out is the patches. The drone sweeps an
  80 m radius at 45 m, beacons the person, the crew is routed round the fire.
- The picker lists only FL1 and WF1 for flood and fire; FR1 to FR3, SAR1 and FF1 to FF3 stay in the registry,
  unlisted. Checked here: both scenarios register, the stranded positions are dry (0 m) with 1.27 m of water between
  them, the fire mask is identical at 0.5 s and 300 s. Not run end to end (manual testing by Surya).

### E9: concise spoken callouts, the responder in the picture (done; not run here)

- One shared callout list in the pipeline (`Pipeline.say`, `WorldState.callouts`, `GET /api/voice/{n}.mp3`): one
  short phrase per operation, never repeated within 8 s, logged as `CALLOUT` events. "Launching.", "Searching.",
  "Found, red shirt." (urgent), "Lost." (urgent), "Back in view." (urgent), "Still lost. Holding.", "Wrong person.
  Searching again.", "Area covered. Nobody matched.", and a position every 4 s while the target is tracked (any
  follow, not only a dispatch): relative to the responder when their position is known ("Target 46 metres
  north-east of you, your 2 o'clock, moving north."), otherwise to the base; no degrees, no feet. The rescue
  mission's callouts go through the same list, shortened ("Found. Review the beacon.", "Beacon confirmed. Crew
  moving.", "Crew on scene. Holding.", "Person 46 metres north-east, your 2 o'clock.", "No safe route. Hold.").
  test12's speech is shortened the same way ("Lost.", "Found, red shirt, near Oak and Main.", "Suspect 46 metres
  north-east, your 2 o'clock.", "Police on scene.").
- The page's voice now speaks the newest callout and drops older ones: an urgent one interrupts whatever is
  playing, a routine one after the current has had 1.5 s. The workspace reads `state.callouts` for every scenario
  (City keeps its own list).
- 3D: the main-engine frame carries the responder's position and the latest callout; a dashed yellow line runs from
  the responder (police officer or crew) to the drone's estimate of the target with the words on it, so the demo
  shows what the callout is relative to. The survey map already drew the crew or police ping.
- Not checked beyond compiling and type-checking (manual testing by Surya).

### E10: visible targets, a high-resolution search feed, the crew route drawn at the beacon (done; not run here)

- **Every marked target is in the open.** SR3 ("Search: person in the far clearing") now spawns the lost person in
  the clearing at (-20, 10) instead of under canopy, and expects them found. `sim/world.under_cover(world, xy)` names
  the structure or tree canopy that would hide a point; a check over SR1-SR5, SAR1, FL1 and WF1 found no subject under
  cover. Pursuit scenarios keep their deliberate occlusions (carports, the city towers): those are the point of them.
- **The search feed.** `Scenario.camera` (`{"width", "height", "fps", "hfov_deg"}`) and `Scenario.perception`
  (`{"yolo_imgsz", "tiling", "tile_px"}`) are applied per scenario: the camera in `SimulationDriver.load` (the frame
  is rendered at that size and rate), the detector in `Pipeline._ensure_detector` (rebuilt when the size or tiling
  changes, the configured values restored for a scenario without overrides). SR1-SR4, VC2, FL1 and WF1 fly a 1080p
  feed at 5 fps with YOLO26n on a full-resolution 1920 px pass; WF1 searches at 30 m (was 45), FL1 at 28 m (was 40).
  Measured on the forest at 30 m (gimbal -50, CPU): the old 960x540 feed at imgsz 960 saw the person in 0 of 8 frames
  (20-26 px tall); 1080p at 1280 in 2 of 8 (66 ms), at 1920 in 6 of 8 (191 ms), tiled 1280 px in 8 of 8 (293 ms).
  The 1920 pass at 5 fps costs about a second of CPU per simulated second, so 2x and 3x still run ahead of real time
  (the tiled 960 px variant dragged the simulation to a third of real time and was dropped).
- **The crew route at the beacon.** `RescueMission` assesses the responder's way in (A* on the hazard grid,
  `missions/sar_guidance.access_route` through `crew_routing.assessed_route`) the moment the beacon is created, and
  replans every 2 s while the beacon is under review as well as while the crew walks. The 3D view draws it as the
  green tube with the "crew route to rescue beacon N m" label (the same style as the hazard-layer routes) and, when
  the assessment is blocked, says why at the beacon ("crew route: no assessed access route…"). `rescue.view()` now
  carries `route` and `route_status`.
- **WF1's ring of fire has a break** on the trailhead side (bearing 205, 36 degrees wide): the person inside was
  unreachable by any route, so the assessment was always "blocked". The access route now threads the break (about
  166 m from the responder's position). In a live run at 3x the beacon was placed at 71 s of simulated time and the
  route appeared in the same frame.
- Checked: `py_compile`, `ruff` on the touched files (no new findings beyond the pre-existing style ones), `tsc -b`,
  `npm run build`; one WF1 run through the 3D frame stream for the beacon and route. Everything else is manual
  testing by Surya.

### E11: rescue sweeps tag everyone (done; not run here)

- `RescueMission` no longer matches a description: the sweep is a plain dispatch (`sweep_label`), and every
  confirmed person track with a calibrated ground position gets a beacon (`B1`, `B2`, …; a person seen again within
  6 m of a beacon is that beacon). The crew's way in to each beacon is assessed (A* on the hazard grid) the moment
  it is placed and replanned every 10 s (every 2 s for the one the crew is walking to). The drone keeps sweeping
  until the area is covered, says "Found. Beacon N." for each, "Area covered. N people found." at the end, and
  returns to the pad 15 s later unless the crew is still walking. The operator confirms or rejects beacons
  (`POST /api/rescue/beacons/{id}/confirm|reject`, buttons under the mission metrics); the crew walks to confirmed
  beacons in order and says "Crew at beacon N." on arrival. `GET /api/rescue` and `rescue.view()` carry `beacons`,
  `found` and `target`; `beacon` stays as the crew's current (or first pending) beacon for older callers.
- 3D: every beacon is a ring with "Beacon N · status" and its route is an amber tube labelled "route BN · L m"
  (green for the one being walked, the crew-route block); a blocked assessment is said at the beacon.
- SR6 "Rescue sweep: beacon everyone in the sector" on the forest (three people, no hazards, A* over the structures
  only), listed first under Search and rescue. FL1 and WF1 now tag everyone too (the hiker in WF1, the three
  stranded people in FL1). Pursuit and city scenarios are unchanged: they still match one described person.
- Checked: `py_compile`, `tsc -b`, `npm run build`; not run (manual testing by Surya).
