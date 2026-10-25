# Current DJI movement and camera handoff

This is the current device-workflow entry point for the combined repository. The full software inventory and measured validation are in [6am.md](6am.md). No physical flight or phone installation was performed by this integration.

## Device workflow

The retained workflow uses the original **DJISDKSwiftDemo** interface with its guarded background relay and the website for operator controls. [SDK website integration](docs/SDK_WEBSITE_CONTROL_FIX.md) describes `ios/integrate_sdk_sample.rb`, the existing FPV/Start Stream interface and the relay. Native sources remain unchanged from the published `hacktx` baseline.

Generated Xcode projects, CocoaPods, DJI registration/app keys, signing and the external SDK sample are machine-local setup. They are not included in this fresh checkout. The earlier standalone bridge build described in [DRONE_LIVE_INTEGRATION.md](docs/DRONE_LIVE_INTEGRATION.md) is historical integration evidence; it is not evidence that the current folder has a signed, installed or hardware-tested app. Follow [ios/README.md](ios/README.md) for source layout and device prerequisites.

## Operator controls

`make drone-live` starts the existing live launcher; `/dashboard` presents Drone controls, keyboard/camera controls, target selection and the incident panels. Source/bridge status explains which controls are ready. A generic video URL or legacy raw-JPEG relay remains video-only when it lacks the required DJI control protocol and status.

The UI distinguishes camera-only control, movement/camera control and visual follow. A selected person, clothing match, reviewed observation or SAR beacon does not start flight. Re-arm and the relevant Start action remain explicit. Physical startup permission is off after restart.

The native camera-only policy zeros translation and caps yaw at 15 degrees/s. Its guarded real-motion policy bounds horizontal speed to 1 m/s, vertical speed to 0.5 m/s and yaw to 15 degrees/s. Backend/site limits may constrain these further. These are code limits, not measured hardware response or a recommendation to begin physical flight.

Pilot stick/dial/Pause/mode input latches takeover. Neutral sticks alone do not resume control. Local fresh RC callbacks, neutral dwell and explicit re-arm are required. Short-lived tokens, command sequence, capture/telemetry freshness, command epochs and expiry prevent buffered motion/re-arm from an earlier control generation. Stop invalidates pending movement. Current DJI telemetry/video and target confirmation remain prerequisites for physical follow.

## What is validated

The backend regression suite and browser checks exercise permission/scope/freshness boundaries without aircraft movement. Standalone Swift assertions compile and execute the manual-override, camera-only/real-motion limit and command-freshness policies. Evidence is in `evaluation/current/swift-safety-tests.txt` and the root audit.

Those checks do not establish DJI callback timing, axis response, actual takeover latency, radio/link-loss behavior, real camera calibration, real LiDAR accuracy or safe navigation at a physical site. Native SDK compilation/signing and device/props-off tests remain separate hardware preparation. Simulated SAR dispatch, coverage, beacons and crew routes never enable physical aircraft permissions.
