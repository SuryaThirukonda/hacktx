# Live DJI feed and OpenAI Decisions control — local integration

Implemented locally on  from GitHub main `004e50c`. Work is on local branch `codex/drone-live-integration`; nothing has been committed or pushed. The original `$HACKTX_ROOT` receiver/app source has not been changed.

## Use the existing working iPhone app for video and ML

From `$PROJECT_ROOT`:

```sh
make drone-live
```

The existing `receiver.py` must be stopped first because it owns port 8765. Enter the Mac's IP in the existing iPhone app and start its stream as before. The launcher serves a single Overwatch LIVE runtime on port 8000 and exposes the same runtime on port 8765 for compatibility. There is no second detector or competing command loop.

Open `http://<Mac-IP>:8000/dashboard`. The existing `ws://<Mac-IP>:8765/ws` raw-JPEG stream and `POST /frame` test button now enter Overwatch's normal model pipeline. They remain video-only command targets. Canonical Overwatch telemetry messages can also travel on that socket, but arbitrary app-specific telemetry JSON is not guessed or relabelled as valid telemetry.

Frames go through decode → configured detector (YOLO by default) → tracker → dashboard overlays / mission pipeline. Video queues are bounded, old JPEG frames are replaced, H.264 resynchronization is requested after congestion, and inference runs separately from socket telemetry/override handling. Native timestamped JPEG/H.264 still uses `/ws/bridge`. Both endpoints enforce the existing access token when configured. Only one drone socket can own the source at a time.

This workspace already has the Python vision/H.264 dependencies, YOLO26n checkpoint and dashboard build installed. On a fresh machine:

```sh
make setup-drone
cd dashboard
npm install
npm run build
```

The launcher selects OpenAI Decisions (`openai`) as the primary engine. Put `OPENAI_API_KEY` in the repository's local `.env` for live OpenAI calls; never commit it. Without a key, existing router behavior uses visibly labelled rules. The image pipeline works without a cloud key. The default launcher clears simulator map coordinates unless a site config is supplied and keeps real-flight control disabled.

## Aircraft control uses the updated native bridge

The old installed sample relay is accepted for video; it does not implement the new control safety protocol and is never sent movement commands. Rebuild `ios/OverwatchDroneBridge` for aircraft control.

The generated workspace is prepared at `ios/Overwatch.xcworkspace`. It has successfully built for a generic iOS device with signing disabled against DJI SDK 4.16.2 and DJIWidget 1.6.8. The generated, git-ignored project has been locally configured from the working DJI sample's bundle identifier, signing team and app key. The app key is resolved into the build and is not copied into tracked source. Nothing has been installed on the iPhone. **Installing that locally configured bundle would replace the existing sample app**, because it reuses the same bundle identifier.

For regeneration, use XcodeGen as described in `ios/README.md`, or the provided `generate_project.rb` using the `xcodeproj` gem included with CocoaPods. Then run `pod install` and re-run:

```sh
ruby configure_bridge_from_sample.rb "$HACKTX_ROOT/Mobile-SDK-iOS/Sample Code/SwiftSampleCode/DJISDKSwiftDemo.xcodeproj"
```

The helper modifies only the generated Xcode project. On this Mac, CocoaPods' Ruby environment is needed for these helpers (its gem is not installed in the system Ruby):

```sh
GEM_HOME=/opt/homebrew/Cellar/cocoapods/1.16.2_2/libexec /opt/homebrew/opt/ruby/bin/ruby generate_project.rb
```

For an independently registered app, configure its own bundle identifier, team and `DJISDK_APP_KEY` build setting instead.

## How manual override wins

- The phone directly listens to DJI remote-controller hardware callbacks. Stick or gimbal-dial movement beyond a small centering deadband, Pause/RTH, or a flight-mode switch change latches pilot takeover on the phone. It clears the pending setpoint and immediately requests virtual-stick disable, independently of Wi-Fi, YOLO and Jev.
- The Mac receives structured takeover/status messages, changes to MANUAL, clears the selected route/primitive, and invalidates pending D1/D12 model answers. Old answers are logged as dropped rather than acted on.
- Releasing the sticks does not resume AI control. Re-arm is a local phone action requiring fresh controller callbacks and neutral controls for at least 250 ms. Then select FOLLOW explicitly on the dashboard. Jev cannot issue re-arm.
- Every phone re-arm changes a control token. Buffered commands from a previous arm are rejected both before network delivery and on the phone. Virtual-stick sequence checks reject duplicates; timestamps/TTL reject delayed packets. Command expiry hovers; prolonged backend silence disables virtual stick. Stale RC callbacks fail closed. Backgrounding, E-STOP and aircraft reconnects also require local re-arm.
- If an asynchronous DJI enable operation finishes after a pilot takeover, the bridge disables it again rather than accepting that late completion.

The listener uses DJI's documented [remote-controller hardware callback and stick state](https://developer.dji.com/api-reference/ios-api/Components/RemoteController/DJIRemoteController.html). The small deadband is 20 SDK counts out of a documented ±660 range; it avoids treating normal stick-centering noise as operator input. **Physical handoff timing, callback cadence and RC-N1 behavior must still be verified on this exact Mini 2/iPhone 15 Plus combination.** Compilation and software tests do not establish a hardware takeover guarantee.

## OpenAI Decisions and flight enablement

OpenAI uses the repository's existing Decisions API adapter/router. It selects bounded reacquisition choices (D1), and D12 route choices when street-map data is available. It does not supply arbitrary flight velocities. The controller computes movement and the safety supervisor applies limits. Real-world D12 street routing still needs an actual map provider; this work does not turn simulator streets into a real map.

Real flight needs both backend opt-in and local phone arming/re-arm. Example backend invocation after preparing a site YAML:

```sh
cd backend
.venv/bin/python scripts/run_drone_live.py --config /absolute/path/to/site.yaml --real-flight
```

That YAML must explicitly define the actual `world.geo_reference` and `autonomy.geofence` polygon. For OpenAI real-flight mode the launcher requires `OPENAI_API_KEY`. The backend refuses FOLLOW/HOLD for real flight without the updated, re-armed bridge, fresh video and telemetry, position/altitude/heading/battery, and valid ground calibration. Losing those conditions returns it to MANUAL. Do not use the simulator site's defaults for a physical site.

Initial device verification remains: build/sign on the phone, connect the aircraft, validate telemetry/video, and use the DJI simulator with propellers removed to test stick/Pause/mode-switch takeover, re-arm, stale-command handling and link loss. Real flight was not enabled during this implementation.

## Verification completed

- Full backend suite: **212 passed, 2 skipped** (86 s) before the final small guard/parser additions. Final focused bridge/Jev checks: **11 passed**, including H.264 decoding, real-flight readiness gating and stale-telemetry handoff.
- Dashboard typecheck and production build: passed.
- Generic iOS device build: passed, signing disabled. The RC delegate callback compiled against the actual DJI SDK, including its `ObjCBool` button fields.
- Standalone Swift override policy tests: passed (initial inhibit, stick takeover, persistent latch, local re-arm, stale RC, Pause and mode change).
- Real YOLO26n: detected four people in Ultralytics' bundled test photo. Four JPEG frames were sent through the real FastAPI legacy socket, YOLO, tracker and dashboard video WebSocket; four person tracks were present.
- A temporary server on test ports 18000/18765 confirmed both ports share the same session; the HTTP Test Frame path, raw-JPEG socket, YOLO output and built dashboard worked through the compatibility port. The temporary server was stopped afterward.
- Jev adapter/router exercised with a mocked HTTP response; no live paid API call was made. No actual iPhone installation, drone movement or physical override test was performed.
