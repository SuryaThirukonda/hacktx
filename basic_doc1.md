# Drone V1 — drone integration handoff

**Date:** 
**Branch:** `drone-v1`
**Repository:** https://github.com/SuryaThirukonda/test_init
**Start here:** this handoff describes the latest workflow; earlier audit/test documents preserve the history.

## What works, and what is newly added

| Capability | Evidence / status |
| --- | --- |
| DJI Mini 2 → iPhone 15 Plus → Mac live video | User tested successfully over Personal Hotspot. Earlier failures were connectivity-related. |
| Person detection, selection and persistent tracking | User tested real video and confirmed tracking. |
| Automatic drone yaw to follow a marked person | User confirmed the aircraft rotates to follow the person. |
| Forward/backward and sideways movement + vertical camera centering | Added in this branch; software-tested. New translation and camera-axis behavior still require a hardware check. |
| Manual keyboard and automatic follow | Both retained as separate controls; activating manual keyboard pauses automatic follow. |
| Original DJI SDK phone interface | FPV / Test Frame / Start Stream preserved. Operator controls are on the website. |
| Test Frame while streaming | Backend validates/acknowledges the JPEG without disturbing the live stream or tracker; previous 409 fixed. |
| Text and push-to-talk appearance requests | Implemented on dashboard and responder; one marked subject. Actual OpenAI decision/transcription requests previously succeeded. |
| Pilot priority | Native RC takeover latch, command expiration and link watchdog implemented/tested in software. Physical takeover timing remains a hardware test. |
| UI simplification | LIVE dashboard contains video, drone controls, manual controls and target chat. Review queue/scenario/map/timeline clutter is hidden in LIVE. |

No 3D model changes were made for this work. No flight/movement commands were issued by the coding agent while implementing this release.

## Start and use it

On the existing Mac checkout:

```sh
cd $PROJECT_ROOT
YOLO_CONFIG_DIR=/private/tmp/hacktx-yolo MPLCONFIGDIR=/private/tmp/hacktx-mpl make drone-live
```

One backend owns **8000** and compatibility **8765**. Do not run `receiver.py`, another `make backend`, or a second
`make drone-live` alongside it. `address already in use` usually means the existing backend is already running.
Check `http://localhost:8000/api/health` before starting another process.

1. Connect Mac and iPhone through the working reachable Wi-Fi/hotspot. Run `ipconfig getifaddr en0` for the current
   Mac address; do not reuse an old address after changing networks.
2. Open the original SDK FPV screen, enter that Mac IP with **port 8765**, and **Start Stream**.
3. Open **http://localhost:8000/dashboard**. If needed, **Start live session**. Confirm live video and DJI telemetry.
4. Choose **Movement + camera**, neutralize RC sticks/dial and press **Enable aircraft controls**. This explicitly
   opts into horizontal movement and requests native re-arm. It does not take off or start following.
5. For manual operation, press **Enable manual keyboard**. For automatic operation, designate a visible person
   (or use the clothing chat and confirm when required), then **Start automatic follow**.
6. **Stop / RC control** stops website output and disables backend real-flight output. RC input takes priority
   without needing that button. Releasing the RC does not resume either mode: explicitly re-arm and start again.

The system does not ask indoor/outdoor. It checks actual connection, telemetry freshness, battery, flight state,
permissions and RC state. Physical movement requires the pilot to put the aircraft airborne; no automatic takeoff
or landing command is exposed by this workflow. A landed, props-off DJI simulator check remains available under
**Connection details / DJI simulator**; DJI must confirm simulator start before it is treated as simulated flight.

### Manual controls

| Selected capability | Keys | Behavior |
| --- | --- | --- |
| Movement + camera | W / S | Forward / backward body-frame velocity |
| Movement + camera | A / D | Left / right body-frame velocity |
| Movement + camera | Left / right arrows | Drone yaw |
| Movement + camera | Up / down arrows | Camera pitch up / down |
| Rotation + camera only | A/D or left/right | Yaw; translation stays zero |
| Rotation + camera only | W/S or up/down | Camera pitch; translation stays zero |
| Either | Escape / key release / window blur | Stop the relevant input; Escape/blur ends the keyboard session |

The on-screen hold buttons provide the same inputs. Manual horizontal velocity is capped as a **vector** at
**0.5 m/s**, including diagonals. Yaw is capped at **15°/s** and camera pitch changes at **10°/s**, limited to
−90°…0°. Vertical aircraft velocity is **zero** in both website profiles; altitude remains with the RC.

## Automatic movement and keeping the person centered

`visual_follow` is a new backend profile separate from rotation-only `camera_only` and the older map-based `full`.

- Image horizontal error drives yaw and sideways velocity. Image vertical error drives **gimbal pitch**, not
  aircraft climb/descent. There are deadbands to reduce jitter.
- The first accepted visible target box establishes the starting normalized image height. Five accepted control
  samples stabilize the estimate. A shrinking person box requests slow forward motion; a growing box requests
  backward motion, aiming to retain the starting framing.
- Forward/backward motion is held until the person is nearly centered. Bounding boxes clipped at the image edge,
  tiny boxes, occlusion flags and abrupt size changes suppress translation rather than guessing.
- Lost/unverified targets, stale data, link loss and RC takeover stop movement. No translating search is flown.
  The designation is retained on loss; a changed track must be confirmed before physical movement resumes.
- A new confirmed person or a new Start follow resets the framing reference. Switching capabilities stops the
  existing control session and requires a new explicit Start.

**Limits:** apparent person size is a relative visual cue, **not a range sensor, depth estimate, measured standoff,
obstacle detector or collision avoidance system**. Crouching, pose changes, zoom, occlusion and detection jitter can
change it. The 0.5 m/s limit and stop rules reduce motion but do not establish safe obstacle clearance. Translation
signs, camera pitch direction and RC takeover must be verified on this aircraft. No hardware translation result
is claimed by this handoff.

The original guarded SDK relay already supports translation permission and the `full` wire profile. The new
`visual_follow` backend maps to that existing wire profile after explicit movement arming, so the currently updated
original SDK app does not need another interface replacement. Both native real-flight and translation flags must
be armed; the phone still caps physical horizontal velocity at 1 m/s, while this backend imposes the stricter
0.5 m/s bound. The older map-based `full` mode retains calibrated ground positions, GPS/site/geofence prerequisites;
its requirements have not been silently bypassed.

## How people are detected, tracked and matched

This is **appearance-based subject selection**, not recognition of a person's real identity.

1. **Detection:** local Ultralytics YOLO, configured for **`yolo26n.pt`**, inference size **960**, confidence floor
   **0.25**. The last observed live detector was `ultralytics:yolo26n.pt@960:mps` on the Mac. The loader can fall back
   to `yolo11n.pt` / `yolov8n.pt`; `/api/perception` and `/api/health` reveal the actual loaded checkpoint.
2. **Tracking:** custom **ByteTrack-style** association, time-aware Kalman box prediction and Hungarian matching.
   High/low detection thresholds are 0.5/0.15, confirmation needs 3 hits, lost threshold is 300 ms and expiration
   4 seconds. Session/epoch-qualified track IDs prevent an old ID from becoming a new identity claim.
3. **Camera-motion compensation:** Shi-Tomasi background corners, pyramidal Lucas–Kanade optical flow and a robust
   homography, so camera motion does not look like subject motion.
4. **Appearance:** local HSV torso color histograms, Bhattacharyya similarity and session-scoped appearance galleries
   support re-acquisition. Coarse upper/lower clothing colors support requests such as “blue sweatshirt, black pants.”
   Fine logos, faces, names and unsupported garment details are not established by these features.
5. **Language matching:** OpenAI **Decisions API**, configured model **`gpt-6-luna`**, chooses among current labeled
   person candidates from structured local observations. It does not produce flight velocities. TypeSafe/Jev is not
   the selected engine. Other adapters/rules remain in the codebase for fallback/evaluation.
6. **Approval:** automatic marking requires successful, non-mock OpenAI output with **confidence ≥0.90**, fresh
   confirmed tracks and unique consistent clothing evidence. Low confidence/ambiguity/fallback needs human review.
   This confidence is a model score, not calibrated identity accuracy. Only one person is marked.
7. **Voice:** browser push-to-talk sends bounded audio to backend OpenAI transcription (`gpt-transcribe` configuration),
   then uses the same text flow. Audio is held in memory, capped at 20 seconds/4 MB. Phone browser microphones need
   trusted HTTPS; Mac `localhost` can use microphone permission. Text works over HTTP.

No face recognition or biometric identity model is used. Current decision configuration sends structured observations,
not raw drone images. Cloud failures do not turn into automatic mark approvals. Cloud models are separate from the
local 20 Hz movement controller. Chat also supports task-specific `status`, `stop`, `clear mark` and `help`.

## Architecture and code map

```text
DJI Mini 2 + RC-N1
  → original DJI SDK sample FPV / background relay on iPhone
  → /ws/bridge (JPEG + DJI telemetry + RC/token status)
  → Mac :8765 alias → :8000, same FastAPI runtime
  → local YOLO → tracker / appearance / designated subject
  → local 20 Hz visual controller → expiring guarded BridgeCommand
  → SDK virtual-stick body velocities + gimbal pitch

Dashboard/responder text or voice → Decisions clothing match → one approved mark
Physical RC → native override latch → virtual stick disabled → explicit re-arm required
```

| Area | Main files |
| --- | --- |
| Run/dependencies | `Makefile`, `backend/scripts/run_drone_live.py`, `backend/pyproject.toml`, `config/default.yaml` |
| Ingestion/control REST | `backend/overwatch/api/app.py`, `backend/overwatch/sources/live.py` |
| Autonomy/keyboard/framing | `backend/overwatch/autonomy/controller.py`, `backend/overwatch/pipeline.py` |
| Detection/tracking | `backend/overwatch/vision/{detectors,tracker,appearance,cmc}.py`, `backend/overwatch/tracking/lock.py` |
| Clothing mark/voice | `backend/overwatch/incident/person_mark.py`, `backend/overwatch/voice/transcription.py` |
| Live website | `dashboard/src/pages/Mission.tsx`, `components/mission/{BridgeSetup,KeyboardCamera,CenterView,RightRail}.tsx`, `components/PersonMark.tsx` |
| Original SDK interface/adapter | `ios/SDKSample/CameraFPVViewController.swift`, `ios/SDKSampleRelay.swift`, `ios/integrate_sdk_sample.rb` |
| Native flight/RC defense | `ios/OverwatchDroneBridge/Sources/{BridgeController,VirtualStickController,ManualOverrideGuard,CameraOnlyLimit}.swift` |
| Tests | `backend/tests/unit/test_visual_follow.py`, camera-follow tests, bridge/keyboard/mark integration tests, `ios/tests/` |

Control endpoints include `/api/autonomy/readiness`, `/api/autonomy/bridge/setup`, `/api/autonomy/mode`, and
`/api/autonomy/keyboard/{start,input,stop}`. Movement setup action is `enable_visual_follow`; rotation-only is
`enable_camera_control`; both stop to MANUAL before re-arm. `disable_control` works after link loss as well.
When `OVERWATCH_TOKEN` is configured, both website/control APIs and device connections require it.

## RC priority and command lifetime

Native RC stick/dial input, Pause/RTH, mode changes, stale RC callbacks, app backgrounding and link watchdog behavior
have precedence over backend/model commands. The guard requires fresh callbacks (0.5 s) and neutral dwell (250 ms)
to re-arm. A 20/660 stick/dial deadband filters noise. Takeover latches; releasing the RC cannot auto-resume AI.

Movement setpoints have ≤300 ms TTL; keyboard leases require renewed input inside 300 ms; the phone runs its own
20 Hz loop and disables virtual stick after 2 s Mac silence. Current control tokens and clock-age checks reject
buffered commands after re-arm. Control epochs discard old cloud decisions after mode changes/takeover. DJI
rejections persist visibly on the website instead of being overwritten by routine status messages.

## Teammate setup from a clean checkout

```sh
git clone --branch drone-v1 https://github.com/SuryaThirukonda/test_init.git
cd test_init
make setup-drone
make build
# Create .env locally from .env.example and supply credentials privately.
make drone-live
```

Python 3.12 is the verified local runtime. `make setup-drone` installs backend dev/vision/H.264/voice/simulator extras
and dashboard npm dependencies. Model weights may download on first inference. Dependencies, weights, generated
Xcode workspaces, recordings, incident DBs and `.env` are deliberately not committed.

For a fresh phone build, obtain the DJI **Mobile SDK iOS 4.16.2** Swift sample with **DJIWidget 1.6.8**, configure
its valid DJI registration/bundle/signing for the target phone, and install its CocoaPods dependencies. From this
repo run (Ruby must have the `xcodeproj` gem, also supplied by CocoaPods):

```sh
ruby ios/integrate_sdk_sample.rb '/path/to/SwiftSampleCode/DJISDKSwiftDemo.xcodeproj'
open '/path/to/SwiftSampleCode/DJISDKSwiftDemo.xcworkspace'
```

Select **DJISDKSwiftDemo** and the iPhone. The integration script backs up original source/project, adds this repo's
native core, preserves an existing customized FPV UI, or supplies the checked-in FPV screen to a clean sample. It
retains DJI registration/signing and does not register a second SDK owner or steal the preview decoder. It targets
iOS 17+. Do **not** install the separate OverwatchDroneBridge UI for this workflow. Device installation should be
performed with the aircraft landed. The Mac USB cable is for installing; reconnect the phone to RC-N1 for DJI use.

Reference: DJI's [flight-controller API](https://developer.dji.com/api-reference/ios-api/Components/FlightController/DJIFlightController.html)
and [virtual-stick data contract](https://developer.dji.com/iframe/mobile-sdk-doc/ios/Structs/DJIVirtualStickFlightControlData.html).
The existing native adapter uses body velocity mode: DJI roll/X = forward, pitch/Y = right, yaw = degrees/second,
verticalThrottle = upward velocity. Verify the physical axis directions before relying on automatic movement.

## Verification and remaining checks

See the final verification result below. New tests cover signed forward/backward/sideways response, camera pitch,
centering before approach, clipped/occluded/stale/lost/unverified stop, explicit movement arming, native movement
permission, manual diagonal cap, keyboard expiration/RC priority and target/reference reset. Earlier signed/unsigned
original-SDK builds and installation succeeded; the user subsequently confirmed real yaw follow.

Remaining hardware work: horizontal axis directions, actual translational response, camera pitch direction,
key release/stream-loss behavior and physical RC takeover timing. Do not confuse a command receipt or bold target
rectangle with DJI's `vstick_enabled` telemetry or observed aircraft movement. The website distinguishes these.
Bluetooth transport is not implemented; the proven transport is reachable Wi-Fi/Personal Hotspot.


### Final software verification

- Backend regression suite: **270 passed, 1 skipped** (the skipped case requires DJI hardware recordings).
- Dashboard TypeScript/production build: **passed**.
- Native Swift motion limits and RC override guard executable tests: **passed**.
- Clean sample integration/template fallback and repeated-run idempotence: **passed**.
- Credential-pattern scan and Git whitespace check: **passed**. `.env`, native local signing configuration,
  generated workspaces, model weights, runtime imagery/DBs and local sample backups remain excluded.
- New movement logic is software-verified; user-confirmed hardware behavior currently covers video, person tracking
  and automatic yaw. Translation and vertical camera centering still need the physical acceptance test.


### Runtime note: observed Apple Metal crash

A previous long-running backend exited with SIGSEGV inside PyTorch MPS/Metal tensor-copy kernels. Python cannot
catch that native crash. The LIVE launcher now defaults to `--vision-device cpu`; detection can be slower, but
avoids that Metal path. The last observed GPU checkpoint string above records the earlier working session, not
the current CPU choice. To evaluate an accelerator explicitly, launch the script with `--vision-device auto`,
`mps` or `cuda`. Check latency/freshness and stability before relying on accelerated flight control. No 3D model
changes were made to address this; the crash stack identified the detector's Metal backend.
