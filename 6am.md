# OVERWATCH — Combined repository audit

## Repository and integration state

The repaired `hacktx` platform and the persistent-target/SAR work are manually synchronized in `/Users/gaurav/Documents/hacktx26_prog`. The checkout is on `main`, tracking `origin/main` at `https://github.com/SuryaThirukonda/hacktx.git`. Changes are local; this integration does not publish or deploy anything.

| Input/checkpoint | Revision | Treatment |
|---|---|---|
| Published `hacktx/main` | `abe3a67d305aa872fdd46e0a1426bc91429e91ab` | Base of this checkout and its Git history |
| Completed `gaurav_integrated` source | `4ed230002f9a651b59b988ebd175da879bff48c4` | Selected source changes and tests synchronized manually |
| Repair checkpoint | `7eac809` | Repairs committed before feature synchronization |

The old source checkout remains `/Users/gaurav/.codex/worktrees/gaurav-integrated/HackTx26`. Its branch and files were not changed. Original repository bundles are stored outside the deliverable at `/Users/gaurav/.codex/backups/hacktx-sync-before-repairs`. This repository inherits only the new `hacktx` history; the old contributor/commit history was not merged. Run `git log -3 --oneline` and `git rev-parse HEAD` for the final local integration commit.

The complete changed-file inventory is [integration-files.json](evaluation/current/integration-files.json); the machine-readable preservation and acceptance record is [integration-summary.json](evaluation/current/integration-summary.json). All original test function names from both inputs remain present, including 232 from published `hacktx` and 283 from the older source; parametrized test execution produces different totals.

Historical calendar-labelled reports and old dependency folders were not imported. Operational timestamps remain in capture provenance, alerts, acknowledgments and evaluation evidence because freshness and audit ordering depend on them. Existing assets were preserved; no new README images were created.

## Architecture

The system is a Python/FastAPI runtime with a React/TypeScript/Vite dashboard, native Swift device clients and a separate City simulation engine. Typed Pydantic contracts connect the runtime, HTTP APIs and WebSockets; exported JSON schemas live in `schemas/`. SQLite owns incident persistence, with an optional Tiger Data mirror.

1. **Sources:** `sources/` receives drone video, telemetry and responder-phone observations. `sim/driver.py` supplies equivalent synthetic packets. Recording/replay uses the same pipeline with explicit provenance. Modes are `SIMULATED`, `LIVE` and `REPLAY`.
2. **Perception:** `vision/` selects the detector, performs tracking and camera-motion compensation, assesses measurement quality, estimates clothing/appearance and projects supported ground contacts into a shared frame.
3. **State and fusion:** `pipeline.py`, `geometry/` and `tracking/` combine calibrated observations, occupancy, locks, object memory, kinematics and confidence/freshness gates. Predictions remain separate from measurements.
4. **Missions and decisions:** `missions/`, `autonomy/`, `decisions/` and `voice/` produce mission observations, bounded plans and operator intents. The autonomy controller and physical permission checks remain the final authority over aircraft commands.
5. **Incident records:** `incident/` owns evidence, reviews, proposals/actions, target snapshots, dispatch alerts, delivery, acknowledgment, timeline and replay. `api/` exposes these functions without a second persistence system.
6. **Presentation:** the new simulation workspace combines existing styling/assets with camera, shared Three.js scene, coverage, callouts and incident review. Existing command, responder, diagnostics, classic demo and native workflows remain available.

Default rates are a 30 Hz simulation world, 20 Hz autonomy commands, 10 Hz fusion/mission ticks and 10 Hz published state. Source/video rates are separately configurable. Runtime subscribers have bounded queues so slow consumers do not accumulate unlimited stale state. Optional inference/decision work runs through bounded services/executors; shutdown cancels work and joins the executor.

## Feature inventory

### Perception, localization and fusion

- Optional Ultralytics YOLO and the synthetic pixel detector; detector metadata distinguishes loaded checkpoints and input provenance. Optional tiling supports small aerial targets.
- High/low detection association, Kalman track state, tracker epochs, appearance galleries and camera-motion compensation. These newer `hacktx` implementations were retained.
- Ground calibration through ArUco, telemetry projection and manually paired points, with uncertainty/age limits and calibration diagnostics. Phone alignment/registration supports shared-frame observations.
- Phone obstacle/proximity processing, occupancy fusion, tracked subject/responder separation, relative range/closing calculations and evidence quality explanations.
- Drone JPEG/H.264 or configured video URL sources, frame age and capture-age checks, reconnect handling, source diagnostics and optional recording/replay.

### Persistent target memory and clothing acquisition

`tracking/memory.py` preserves the newer motion-aware `ObjectMemory` and adds `SubjectMemory` as a read model over existing locks and measured objects. It does not replace association or create a second controller.

- Stable logical subject IDs are scoped to incident and session and may span detector track-ID changes.
- Views expose last measured/verified position, current measured or predicted position, uncertainty, measured motion trail, appearance and candidate evidence. Predicted points never extend the measured trail or refresh the last verified timestamp.
- Loss is labelled as temporary loss, reacquiring, uncertain or lost. Extrapolation is bounded; uncertainty grows with absence. Continuity scores are heuristics, not identity probabilities.
- Existing clothing parsing/ranking now supports continuous search. A supported unique candidate requires distinct current frames and quality checks. Ambiguous clothing remains a review choice; unsupported details such as logos are not invented.
- Automatic operational acquisition/continuity is limited to synthetic input in a simulated bridge. LIVE proposes candidates and requires a human binding. Acquisition and target confirmation do not arm or start physical follow.
- Rejected track choices are respected. Release, incident change/closure, tracker/session changes and new designations invalidate relevant requests and archive memory. Snapshots and events reuse SQLite and incident replay.
- Dashboard polling and explicit refreshes guard incident switches so responses from a previous incident do not overwrite the selected incident.

### Pursuit and cover recovery

The newer `hacktx` core belief, pursuit, tracker, appearance, lock and aircraft safety modules were preserved byte-for-byte from the published baseline. Integration adds memory/evidence/control-scope hooks around them.

- Street-graph particle belief, motion-aware object continuity, cover-aware negative observations, candidate viewpoints and bounded route/decision choices remain active.
- Existing D1 reacquisition and D12 pursuit command-epoch guards are retained. D6 search decisions also reject obsolete control epochs.
- Wrong rebindings can be rejected through incident/session/designation-scoped controls. LIVE rebind confirmation requires a separate Start Follow action.
- The isolated `test12` City engine retains its own detector, road belief, drone movement, police pings, relative callouts and reports. Its repaired loss strategy performs a bounded full scan, a short cover-inspection orbit and then the existing road search. It does not replace the main pursuit controller.
- City has shared 3D/coverage publication, an orthographic endpoint and authenticated dynamic APIs/sockets. Stopping or switching away waits for the child engine to acknowledge shutdown; main simulation ownership is not resumed on a failed stop.

### Simulated SAR assessment and human review

`missions/sar.py` combines current confirmed person measurements with explicitly simulated environmental context. Hidden actor casualty roles are not used for operational detection.

- Flood/fire context can create a possible assistance candidate. Dry stationary people alone do not meet the default hazard threshold. The designated responder is excluded.
- Rescue variants additionally match measured clothing against the reported missing-person request; dry forest/structure cases can therefore request review without pretending a hazard was detected.
- Candidates retain frame evidence, reasons, score, provenance and logical object/subject links. Repeated frames or track changes do not create uncontrolled duplicate assistance alerts; rejection is retained.
- Confirm/Reject uses existing `Observation`, `Review`, `MissionAction` and `Alert` records. Confirmation and dispatch are explicit operator actions. Rejection does not dispatch a crew. Repeated completed confirmation does not duplicate notification.
- Review/dispatch rejects another incident/session and stale video/capture evidence. This SAR dispatch implementation is simulation-only.

### Responder routes, beacons and base-return rescue

`missions/rescue.py` was malformed in published main. It is now repaired and connected to the incident review workflow. The new compact scenarios are FR3 (flood refuge), FF3 (fire perimeter) and SR5 (forest clearing).

1. A scripted simulation dispatch launches the drone from its pad toward a reported search location and clothing description. It does not script human approval.
2. Current supported camera measurements create a pending beacon with uncertainty, an incident observation ID and a logical subject ID. The drone holds camera contact for review.
3. Human confirmation approves assistance delivery and crew guidance. A rejected beacon does not move the crew.
4. The crew follows an assessed route, rechecking movement in short substeps. Stale tracking, unsafe ground and manual/stop conditions prevent continued autonomous movement.
5. Arrival means reaching the assessed approach within the configured distance plus capped measurement uncertainty; it does not claim physical contact with a hidden actor. After a ten-second hold the simulated drone returns to base and the mission completes.

The existing time-dependent road/cell crew router remains. SAR and rescue share an assessed connector in `crew_routing.py`/`sar_guidance.py`: bounded 1 m A*, edges sampled at intervals no longer than 0.5 m, structure clearance, world bounds, flood depth and fire keep-out/arrival checks. It does not snap a responder across a hazard. Optional walking speed checks hazards at expected arrival time; execution checks again as time advances.

Results distinguish `access_route`, `approach_only`, `blocked` and `unavailable`. The original road router's 0.3 m closure rule remains; the SAR/rescue connector defaults to a stricter 0.15 m water limit. An inaccessible target receives a dry approach or a blocked result, not an arrow through the hazard. Heading-dependent clock directions require a valid shared frame and trusted heading. Loss/staleness reports last-seen information instead of presenting predictions as a current measured route target.

Guidance is delivered through the existing responder channel. Delivery and idempotent acknowledgment are persisted and visible in the operator timeline. Optional TTS/browser speech reads simulated callouts. Rescue voice files are authenticated and registered before the frontend catch-all route.

### Coverage, map and 3D presentation

- Repaired vectorized camera/geometry visibility supports the ground coverage survey. A fading blue grid records observed ground, with roof/canopy occlusion leaving cells unknown. It is display history, not proof that an area is clear or safe.
- Survey maps show measured trails, separately styled predictions, report history, responder/crew pings, confirmed access routes and beacons. City ground truth remains explicitly opt-in on the map.
- Shared 3D uses the same world/frame feed for the main simulator and City. Chase/overview/top cameras, Mini 2 stand-in and beacon visualization work; legacy X2 physics/mesh presets remain.
- The simulation workspace activates the published dashboard styling and existing situation pictures. Six situation cards expose **36 picker entries**, including the separate T12 engine. There are **54 registered main-engine scenarios**: all 50 original IDs plus SAR1, FR3, FF3 and SR5. CT1/CT2 and all original pursuit/search/flood/fire variants remain.
- Theme choice persists, scene code is loaded lazily, and camera/map/callout views support fullscreen and playback/restart. Review, persistent target, pursuit, voice and diagnostics are below the main simulation panels.

### Mission, incident, scene and voice tools retained

- Search sectors/lawnmower plans, sightings, follow/person-mark workflows, structure sides/openings and indoor mission tools remain available.
- Scene meshes, point clouds and Gaussian splats, frame alignment and placement, evidence/map markers, calibration, review queues and recommendations remain in incident command.
- Incident creation/selection/closure, evidence records, immutable event history, review/actions, alert delivery/ACK, replay, metrics and optional Tiger Data mirroring remain available.
- Rules are the deterministic offline decision engine. OpenAI, TypeSafe/Jev and Strands adapters remain optional and gated by configuration, keys, budget, confidence and freshness.
- D13 voice/text commands retain grounded world entities, ambiguity handling, explicit approval and optional Gemini/ElevenLabs support. No paid provider calls were needed for the recorded validation.

### Native devices and real-flight boundaries

All `ios/` files are unchanged from published `hacktx/main`: DJI bridge/manual controls, ARKit/LiDAR responder paths and native scan workflows are preserved. Native compilation, aircraft operation and real LiDAR accuracy were not validated here.

Startup never inherits real-flight permission. Explicit permission, bridge capability/re-arm, local SDK permissions, current telemetry/video, current target confirmation, pilot override, watchdogs and command epochs remain authoritative. The integration restores strict actual-DJI-source checks and invalidates control state on incident/session changes. A prerecorded or unknown video source is not physical targeting evidence. Simulation camera aiming is gated to simulation; no physical rescue movement path was added.

### Windows/GPU tooling

- Loaded-model metadata uses a checkpoint basename consistently across platforms.
- Detector benchmark output supports external directories and UTF-8.
- `scripts/small_target_sweep.py` retains frozen-manifest, ignore-aware small-person scoring and CUDA sweep support. Scoring utilities import without GPU dependencies; inference dependencies load only for the CLI inference path.
- Defaults remain YOLO26n/960/confidence 0.25 with CPU/MPS/CUDA auto selection. Existing tabletop YOLO26s overrides and checkpoint fallback remain.
- No automatic LIVE Search/Follow model switch was introduced. A larger profile requires representative footage and latency/accuracy validation. Windows CUDA performance was not remeasured on this Mac.

## Repairs and preservation decisions

The published baseline had eight Python files that could not parse, a malformed SurveyMap component, contract mismatches and six failing regression tests. The baseline assessment was 276 passed, 6 failed, 2 skipped. Repairs were committed first; that checkpoint produced 282 passed, 2 skipped and a successful frontend build.

Repairs reconstructed rescue/survey/visibility, the City shared-3D adapter and four evaluation verifier scripts. Additional fixes restored valid SurveyMap code, missing optional contracts, the detector provenance fixture, an extensible scenario assertion and the documented fire demo spread parameter. Integration then connected components that existed in published main but were not mounted into its active UI/runtime.

| Area | Preservation/integration decision |
|---|---|
| New dashboard appearance/assets | Retained and wired into active `/dashboard`; classic tools retained |
| New core pursuit/tracking | Retained; older broad file replacements avoided |
| Persistent subject/SAR | Added using existing locks, object memory and incident records |
| Native DJI/LiDAR | No native source changes; added backend scope/freshness guards |
| Voice/decision services | Newer implementations retained; stale decision epoch checks extended |
| City engine | Retained isolated process; repaired adapter/recovery and explicit ownership handoff |
| Rescue/coverage modules | Repaired before integration; connected to measurement/review/delivery flow |
| Old Git history/dependencies | Not imported; source checkout and backups preserved outside target |

## Recorded validation

Evidence lives under `evaluation/current/`; platform scenario details are under `evaluation/merge/platform/`. These are measured results for the listed cases, not claims about every supported environment.

| Check | Result |
|---|---|
| Full backend regression | **356 passed, 2 skipped**, zero failures; 68.49 s |
| Skips | Tiger Data service absent; DJI simulator step recordings absent |
| Warning | Starlette/httpx TestClient deprecation; no failing assertion |
| TypeScript / production build | Passed |
| Python syntax / undefined names | Passed |
| LIVE dashboard browser | Passed: existing panels/routes, disarmed startup, no page errors |
| SAR incident/responder browser | Passed: search, persistent subject, review, guidance, delivery, ACK, no physical follow |
| New workspace browser | Passed: saved theme/picker, beacon review, responder ACK, shared 3D/survey, callouts, City process/handoff |
| Classic demo browser | Two deterministic runs passed and outcomes compared |
| SAR1 deterministic acceptance | **13/13** criteria passed, including real pixel detection, exclusions, evidence, approach-only route, delivery/ACK and no physical flight |
| FR3 / FF3 / SR5 deterministic acceptance | **10/10 each**: measured beacon, human approval before movement, observation link, arrival, return, safe steps, ACK, survey and no physical permission |
| Selected platform scenarios | PP0, PP8, PP5, PP6, PP7 and SR4 each met its scenario expectations |
| City headless | One seed, 240 simulated seconds; zero wrong matches, four losses/four recoveries |

The full suite includes subject memory through loss/occlusion, track-ID changes, ambiguous clothing, stale frames and quality, incident/session/tracker-epoch changes; control scopes and decision epochs; review rejection; concurrent/idempotent ACK; route hazards; and City/main ownership. Source-only older tests were retained alongside the newer tests rather than replacing the target suite.

In the selected pursuit runs, wrong-target locks were zero. Correct fractions among visible/locked steps were PP0 1.000, PP8 1.000, PP5 0.998, PP6 1.000 and PP7 0.997. These fractions do not mean uninterrupted visibility: cover scenarios intentionally lose camera contact, and SR4 is a search case with no subject lock.

FR3/FF3/SR5 each completed in 26.33 simulated seconds with crew arrival at 16.33 s, 13.2 m walked, zero unsafe movement steps and zero water depth on the walked path. Beacon error against evaluation-only truth was approximately 0.604/0.617/0.617 m. The scripted acceptance operator explicitly approves the human API; interactive browser acceptance separately verifies the operator controls.

City seed 0 matched at 15.4 s, followed for 82.8% of the run, had 18.2 m median/19.0 m p90 following distance, four losses/four reacquisitions, 13.2 s median/13.8 s maximum recovery and police on scene at 32.8 s. This is a single-seed result; the run ended at its time limit, not a claim of an arrest or all-seed success.

## Reproducible demo and checks

Follow the image-free [README](README.md) for dependency setup and routes. Optional vision/checkpoint dependencies are required by City and explicitly YOLO-configured scenarios. The current local checkout uses ignored links to already installed Mac dependencies/checkpoint; these links are not part of a fresh clone or the committed source.

```sh
npm --prefix dashboard run typecheck
npm --prefix dashboard run build
backend/.venv/bin/python -m pytest backend/tests -q -rs
make sar-eval
make rescue-eval
make e2e-live
make e2e-sar
make e2e-workspace
make e2e
```

Do not rebuild assets while browser checks are serving them. Browser runners default to local Chrome; set `E2E_CHROME` when needed. They use isolated temporary databases, loopback servers and rules with external provider keys disabled.

For interactive SAR1:

```sh
make sar-demo
```

Open `http://127.0.0.1:8011/command`, inspect a possible assistance candidate, optionally start continuous clothing search, then Confirm & send guidance or Reject. Open `/responder` and acknowledge the specific guidance alert. The SAR1 route stops at an assessed dry approach because the measured target is in water. The helper leaves physical permission off.

For base-launch rescue, use the normal simulated server and `/dashboard`. Select Flood/FR3, Fire/FF3 or Search and rescue/SR5, launch, wait for the pending beacon, inspect evidence and confirm. The crew remains stationary before approval. Watch the survey/route/callouts, acknowledge on the responder page, then observe the hold and return. Stop/manual override pauses rescue and requires an explicit restart.

## Limits and remaining work

- No real aircraft flight, native app build, real LiDAR navigation, hardware watchdog timing experiment or Mac detector accuracy study was performed. Simulator and fake-device checks do not establish those outcomes.
- CUDA sweep code is preserved, but its dataset/checkpoints and GPU measurements must be supplied on the intended host. Larger-model latency/accuracy and automatic model switching remain unimplemented.
- SAR is a simulation workflow, not medical triage. Clothing matches are operational continuity, not verified personal identity. Gesture recognition and learned distress classification are not implemented.
- Hazard layers are screening maps, not measured navigability. Bounded grid search/clearance and location uncertainty can produce an approach-only, blocked or unavailable result. Real rescue deployment needs validated maps, positioning and human procedures.
- City validation covers one seed plus browser ownership/visualization checks. An exhaustive seed sweep and the entire 54-scenario matrix were not rerun. Existing unsupported extensions are listed in the use-case catalogue.
- External cloud decision/voice services, Tiger Data and physical DJI recordings were unavailable or intentionally unused. Base tests use optional-dependency fakes where the purpose is metadata/contract validation.
- The production bundle builds but has a large shared entry/3D vendor chunk. Lazy scene loading is present; further bundle optimization was not a merge requirement.

## Publishing this checkout

The folder already points to the requested repository and local `main` tracks `origin/main`. No force push or unrelated-history merge is needed. Publication is a separate step after review:

```sh
cd /Users/gaurav/Documents/hacktx26_prog
git status --short
git fetch origin
git log --oneline --left-right HEAD...origin/main
```

If `origin/main` is still the recorded base, normal `git push origin main` publishes the local repair and integration commits. If teammates have advanced it, review the new diff and integrate it without overwriting their changes, rerun affected checks, then push normally. This audit does not authorize or perform that push.
