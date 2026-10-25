# First camera-follow test: yaw and gimbal only

The reported behavior had three separate causes:

1. Clicking a box called `action: select`, so a bold rectangle meant selection, not a persistent subject designation.
2. The original DJI sample app is accepted as a video-only source. It does not implement Overwatch's pilot-override/control-token protocol, so the backend never sends it flight commands.
3. The backend's real-flight gate was off, and the dashboard hid flight controls outside SIMULATED mode.

The dashboard now distinguishes selection from designation, offers **Designate target** for a selected person,
shows a persistent lost-target status and displays the flight-control panel in LIVE mode with the actual blockers.
Designation does not arm flight; **Start camera follow** is a separate operator action.

## What changed

`make drone-live` now defaults to `motion_profile: camera_only`. It still starts with real-flight commands off.

- While the designated, verified person is visible, local image-based control turns the aircraft left/right
  (yaw, capped at 15 degrees/s) and tilts the camera (gimbal, capped at 10 degrees/s).
- Forward/backward, sideways and vertical velocity commands are exactly zero. This path never invokes the full
  controller's standoff/altitude/people-avoidance translation, automatic routes or translating RTL.
- The native phone applies its own zero-translation limit and yaw cap in camera-only mode. Translation now has a
  separate phone opt-in and full-motion backend setup; see [PERSON_MARK_AND_FULL_FOLLOW.md](PERSON_MARK_AND_FULL_FOLLOW.md).
- Loss, stale video, or an unverified re-binding stops rotation and camera movement. The designated subject remains
  recorded; if the same verified track returns, camera follow can resume. A new binding needs confirmation.
- RC stick/dial input, Pause/RTH, mode changes, website Stop/manual control, stale controller callbacks and the existing
  link/watchdog rules retain priority. Explicit operator re-arm and an explicit start are required after takeover.
- Camera-only control does not need a surveyed map or target-ground distances. It still requires a capable/re-armed
  bridge, fresh video/telemetry, altitude/heading/battery/gimbal data and an airborne aircraft for real control.
  Full-motion real control retains the GPS/ground-calibration/site-geofence requirements.

OpenAI Decisions remains the sole selected decision engine. Frame-by-frame camera centering is deterministic
local control; OpenAI is used for the existing description and higher-level decision tasks. Camera-only mode
does not execute translating pursuit/search proposals when a person disappears.

## Original SDK app and props-off simulator verification

The original SDK sample now contains the control relay without replacing its FPV interface. Build/install
**DJISDKSwiftDemo** from the original sample workspace shown in
[SDK_WEBSITE_CONTROL_FIX.md](SDK_WEBSITE_CONTROL_FIX.md). Do not install the separate OverwatchDroneBridge app.
The existing phone binary must be updated before video can carry guarded downlink control.

1. Land the aircraft, remove propellers and keep the backend running with `make drone-live`.
2. Enter the Mac's current reachable hotspot IP in the original SDK FPV screen, then Start Stream.
3. On `/dashboard`, verify **Control relay connected**, fresh video and canonical DJI telemetry.
4. In **Aircraft control connection**, confirm the props-off checkbox and press **Start DJI simulator**.
   Wait for DJI simulator telemetry; a request receipt alone does not prove simulator start.
5. Neutralize RC sticks/dial, then press **Arm / re-arm website controls**. This does not start movement.
6. Designate/confirm a visible consenting person, then press **Start camera follow**. Check simulated yaw and
   gimbal response. Forward/right/up commands remain zero in the default camera-only profile.
7. Move the person out of view: yaw/gimbal must stop and the designation remain lost. Confirm any new track binding.
8. RC stick/dial/Pause/RTH or mode change must latch pilot control. Releasing the RC must not resume follow.
   Repeat explicit re-arm and Start only when ready. Test stream loss/expiry as well.

Physical output remains off for this check. No indoor autonomous flight has been activated.

## Dashboard keyboard / arrow controls

The dashboard has a **Keyboard camera control** panel independent of target designation. In camera-only mode,
connect/re-arm the updated native bridge, then click **Enable keyboard control**. This pauses automatic follow
and acquires a short-lived operator session; it never clears the phone's RC takeover latch.

- **A/D** or **Left/Right arrows** rotate the drone (yaw, up to 15 degrees/s).
- **W/S** or **Up/Down arrows** tilt the camera (up to 10 degrees/s, pitch limited to −90…0).
- Hold the on-screen arrow buttons for the same controls. Releasing keys/buttons stops that input.
- **Escape**, disabling controls, leaving the window/tab or focusing a text field ends the session.
- Heartbeats expire after 300 ms without accepted input; stale video/link, RC takeover, low battery or a mode
  change also stop keyboard control. Releasing the RC never automatically resumes keyboard or follow control.
- No forward/backward, sideways or vertical aircraft translation is available through these keys.

Verify key release, opposite keys, tab switching and RC takeover in the props-off simulator first. Backend
real-flight opt-in and phone arming are still required for physical flight. Start camera follow explicitly if
you want to resume target centering after keyboard control.

## Later outdoor hardware activation

After the simulator and pilot takeover are verified, use an open outdoor test area. Stop the backend's current
launcher and start:

```sh
cd $PROJECT_ROOT
backend/.venv/bin/python backend/scripts/run_drone_live.py --engine openai --motion-profile camera_only --real-flight
```

The original SDK relay requires an explicit website re-arm with neutral RC controls. The aircraft must be flown by the pilot
before starting camera follow. Enter FOLLOW only after fresh telemetry/video and a verified designation are present.
This command is documented for that later test; it has not been run as part of this change. Indoor autonomous
flight has not been activated.

Bluetooth transport is unchanged and remains future work. The currently proven connection is the personal hotspot.
