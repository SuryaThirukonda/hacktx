# Run the drone integration on this Mac

Project: `$PROJECT_ROOT`

For responder text/push-to-talk marks and explicit full-motion follow, see
[PERSON_MARK_AND_FULL_FOLLOW.md](PERSON_MARK_AND_FULL_FOLLOW.md).

The current LIVE screen has **Enable aircraft controls**, keyboard controls, Start/Stop follow and target chat.
No indoor/outdoor selection is required; fresh telemetry, native RC checks and explicit arming remain required.
For the current **yaw and camera tilt only** test, follow [CAMERA_FOLLOW_TEST.md](CAMERA_FOLLOW_TEST.md).
The launcher defaults to `camera_only`; real-flight commands remain off until explicitly enabled.

Dependencies are installed: Python 3.12 virtual environment with backend/dev/YOLO/H.264/voice/MuJoCo support; dashboard npm dependencies and production build; CocoaPods with DJI SDK 4.16.2 and DJIWidget 1.6.8. The generated iPhone workspace is configured locally from the existing DJI sample. The rebuilt original SDK sample is now installed; no aircraft movement has been performed.

The supplied settings are in the root `.env`, ignored by Git with file permissions 0600. URLs were normalized to plain values rather than Markdown links. ElevenLabs account 2 is preferred, with account 1 as fallback. Agent IDs are preserved in `.env`; the existing backend uses the TTS API rather than those conversational agents.

`OPENAI_API_KEY` is configured. OpenAI Decisions is the default and sole active engine; shadow engines are disabled. A separate anonymous clothing request succeeded in about 300 ms at confidence 76% (correctly below automatic approval). OpenAI also transcribed generated test speech successfully. These checks sent no drone images or microphone recordings. Credentials were disabled during the regression suite to avoid cloud calls from tests.

Verification after responder marking/full-motion support: **256 backend tests passed, 1 skipped**; dashboard typecheck/build, unsigned iOS device build and standalone Swift motion/profile limit tests passed. HTTPS API and secure WebSocket proxy smoke checks passed using a temporary localhost certificate. The skipped test needs DJI hardware recordings. Physical flight and iPhone microphone/certificate setup remain device checks.

## Start the current iPhone relay and live YOLO

1. Stop the old `receiver.py` first. The new launcher needs the same port 8765.
2. In Terminal:

   ```sh
   cd $PROJECT_ROOT
   backend/.venv/bin/python backend/scripts/run_drone_live.py --engine openai
   ```

   This uses the supplied OpenAI key. Real aircraft commands remain disabled. For no cloud model calls, use
   `--engine rules` and temporarily leave the voice keys unset if you also want no ElevenLabs warmup calls.
3. Put the Mac and iPhone on the same reachable Wi-Fi/hotspot. Find the Mac's Wi-Fi address with
   `ipconfig getifaddr en0` (use the active interface if Wi-Fi is not en0).
4. Connect the iPhone to the RC-N1, power the controller/drone, and open the existing app's FPV screen.
   Enter that Mac IP and tap Start Stream as before. The updated original SDK sample sends to `:8765/ws/bridge`; the old `:8765/ws` remains video-only.
5. Open `http://localhost:8000/dashboard` on the Mac. Confirm the drone image, live source status and a YOLO
   model in `/api/perception`. Detection does not require flight enablement. Designations require starting
   an incident with the application's existing participant-consent step.

Do not run a second `make backend` alongside this launcher. The compatibility port and main port share one runtime.
Ctrl+C stops the launcher.

## Check the feed and OpenAI Decisions

`make drone-live` now starts OpenAI Decisions by default. The running server is LIVE on port 8000, with the
existing relay compatibility port on 8765. Real flight is disabled.

1. Open `http://localhost:8000/api/health` and confirm `decisions.primary` and `decisions.active` are `openai`.
2. The working hotspot test used **172.20.10.2**; a later Wi-Fi check showed **100.64.196.139**.
   Reconnect to the working hotspot and recheck with `ipconfig getifaddr en0`. The old relay uses that IP on
   port 8765; the updated bridge uses `<current-Mac-IP>:8000`.
3. On `/dashboard`, verify the live camera changes as you move it and consenting people receive detection boxes.
   `/api/perception` should show an active `ultralytics:` model after the first frame.
4. For consenting test participants, create a LIVE test incident from another terminal (the dashboard currently
   has a demo-start button, so use this explicit live incident call):

   ```sh
   curl -X POST http://localhost:8000/api/incidents -H 'Content-Type: application/json' -d '{"title":"Live drone test","mission_mode":"follow"}'
   ```

   Refresh `/dashboard`, enter a visible person's clothing in **Who to follow**, and press **Find**.
   This requests a description decision; it needs confirmed person tracks first. Inspect `/api/decisions` for
   a `D3_describe` record with `engine: openai` and `status: success`. Confirm the proposed match yourself.
   A fallback/error record is not proof of a successful OpenAI call.
5. Stop streaming and verify the source becomes stale/disconnected and current claims stop updating.

No frames had arrived at the initial health check, so YOLO reported "not started (no frame yet)" and the decision
log was empty. That is expected before the iPhone connects. A separate anonymous OpenAI Decisions request has
since succeeded; the incident log still needs a real eligible request. The server also reported ElevenLabs account 2 rejected its key;
that optional voice issue does not block video, detection or OpenAI Decisions.

## Keep the original DJI SDK app; update its background relay

Use the original SDK sample workspace, **not** the separate OverwatchDroneBridge app:

```sh
open '$HACKTX_ROOT/Mobile-SDK-iOS/Sample Code/SwiftSampleCode/DJISDKSwiftDemo.xcworkspace'
```

Choose **DJISDKSwiftDemo**. Its FPV/Test Frame/Start Stream interface, DJI registration and signing are preserved.
The sample's background relay has been updated using `ios/integrate_sdk_sample.rb`; ignored local backups are
under `ios/sdk-sample-backups`. It now sends canonical JPEG frames, DJI telemetry and RC takeover state through
`:8765/ws/bridge`. Enter the current Mac IP and tap Start Stream as before. The old installed binary does not
gain control support until this rebuilt original SDK sample is installed.

Aircraft/operator controls are on `http://localhost:8000/dashboard`. The **Aircraft control connection** panel
must show a control relay, fresh telemetry and the RC state. For a landed, props-off simulator check: confirm the
checkbox, start DJI simulator, neutralize RC, arm website controls, designate/confirm the person, then press
**Start camera follow**. Marking alone never starts movement. RC takeover requires explicit website re-arm and
another Start; neutralizing the sticks alone never resumes control.

Read [SDK_WEBSITE_CONTROL_FIX.md](SDK_WEBSITE_CONTROL_FIX.md) for the current fix and device checklist. Original
SDK builds compile; device installation and physical RC takeover still need verification. Physical-flight output
remains disabled. Do not test autonomous movement indoors in flight.

## Full-motion configuration, after site/device verification

Camera-only remains the default. Translation now requires the explicit backend full-motion/real-flight opt-ins, website re-arm, and a site YAML with the actual `world.geo_reference`, explicit
`autonomy.geofence`, altitude settings and ground calibration. Read the full-motion guide above first.
Its backend configuration is:

```sh
cd $PROJECT_ROOT
backend/.venv/bin/python backend/scripts/run_drone_live.py --engine openai --motion-profile full --config /absolute/path/to/site.yaml --real-flight
```

Backend opt-in alone does not arm the aircraft: the original SDK relay also checks fresh neutral-controller callbacks and requires
explicit website re-arm. Permission changes do not start movement. FOLLOW/HOLD requires fresh video/telemetry, position, altitude, heading, battery and valid ground
calibration. Missing readiness returns control to MANUAL.

See [DRONE_LIVE_INTEGRATION.md](DRONE_LIVE_INTEGRATION.md) for protocol details, command expiry and stale-answer
protection. No site geometry has been invented, and real flight has not been enabled as part of setup.
