> For the current movement controls and clean SDK build instructions, see [Drone V1 handoff](../DRONE_V1_HANDOFF.md).
> Current device workflow: keep the original **DJISDKSwiftDemo** UI and use the website for operator controls.
> Its background relay is integrated with `integrate_sdk_sample.rb`; see
> [SDK website control fix](../docs/SDK_WEBSITE_CONTROL_FIX.md). Do not install the separate bridge UI for this setup.

# Overwatch iOS apps

Two native apps that speak the wire protocol in [`docs/contracts.md`](../docs/contracts.md) (schema 1.0) to the Mac
backend running in `LIVE` mode:

| App | Device | Socket | Does |
|---|---|---|---|
| **OverwatchResponder** (`com.overwatch.responder`) | the responder's iPhone with LiDAR (12 Pro or newer Pro) | `ws://<mac>:8000/ws/phone` | ARKit pose + LiDAR depth → obstacle sectors (port of `backend/overwatch/geometry/phone_depth.py`), on-device proximity alerts that work **without** the Mac, Apple Vision person boxes with LiDAR ranges, compass/GPS/AirPods/battery, upright JPEG frames; receives alerts and plays haptics, speech and a spatial earcon; big ACK button |
| **OverwatchDroneBridge** (`com.overwatch.dronebridge`) | "iPhone A", plugged into the DJI RC-N1 of a **DJI Mini 2** | `ws://<mac>:8000/ws/bridge` | DJI MSDK 4.16.2: telemetry at 10 Hz, raw H.264 video (or JPEG fallback), executes `BridgeCommand`s with the phone-side safety layer (20 Hz virtual-stick loop, 300 ms command TTL → hover, 2 s Mac silence → virtual stick off / pilot in control, caps, E-STOP, RTL, DJI simulator) |

```
ios/
  project.yml                      XcodeGen spec (both targets, iOS 17)
  Podfile                          DJI-SDK-iOS ~> 4.16.2 + DJIWidget ~> 1.6.3 for the bridge only
  Shared/Sources/                  compiled into both apps
    WireProtocol.swift             binary frame packing, DeviceClock, sync reply, hello, JSON helpers, Locked<T>
    ReconnectingWebSocket.swift    URLSessionWebSocketTask + backoff reconnect, keepalive/RTT, bounded send queue
  OverwatchResponder/
    Info.plist
    Sources/  Contracts.swift (PhoneObservation, Alert, responder_view…), ARSessionManager, DepthProcessor
              (+ LocalProximityNotifier), VisionBoxes, SensorHub, CaptureTransport, ObservationPipeline,
              FrameGeometry (pose/intrinsics/orientation/JPEG), AlertFeedback, ResponderModel, Views/
  OverwatchDroneBridge/
    Info.plist                     DJISDKAppKey goes here
    Sources/  BridgeContracts.swift (DroneTelemetry, BridgeCommand…), ProductLink, TelemetryCollector,
              VirtualStickController, VideoForwarder, H264Tools, FPVPreview, BridgeController, BridgeModel, Views/
```

## Build

Requirements: a Mac with Xcode 15 or newer (iOS 17 SDK), [XcodeGen](https://github.com/yonaskolb/XcodeGen)
(`brew install xcodegen`), CocoaPods (`brew install cocoapods`), an Apple developer team for device signing, and for
the bridge a DJI developer account. Both apps need **real devices** (ARKit/LiDAR; DJI External Accessory).

```bash
cd ios
xcodegen generate          # writes Overwatch.xcodeproj (git-ignored)
pod install                # integrates the DJI pods into the bridge target, writes Overwatch.xcworkspace
open Overwatch.xcworkspace # always the workspace, never the .xcodeproj
```

* Re-run `pod install` after **every** `xcodegen generate` (XcodeGen rewrites the project CocoaPods modified).
* Signing: select each target → Signing & Capabilities → your Team (or set `DEVELOPMENT_TEAM` in `project.yml` so it
  survives regeneration). If the bundle IDs are taken in your team, change `PRODUCT_BUNDLE_IDENTIFIER` in
  `project.yml` — and register the new bridge ID with DJI.
* The Podfile's `post_install` raises the DJI pods to iOS 17 (Xcode 15+ can no longer link iOS 9 targets) and turns
  bitcode off. The DJI pod excludes arm64 for the simulator; build for a device.
* Use a Release build (Product → Scheme → Edit Scheme → Run → Release) for long sessions: the per-pixel depth loop
  is much faster optimised.

### DJI App Key (bridge only)

1. developer.dji.com → Apps → *Create App*: SDK **Mobile SDK**, platform **iOS**, bundle identifier
   `com.overwatch.dronebridge`.
2. Set the bridge target's `DJISDK_APP_KEY` build setting to the key. `Info.plist` expands this setting;
   keep the key out of tracked source. To reuse an existing working DJI sample locally, see
   [the integration guide](../docs/DRONE_LIVE_INTEGRATION.md).
3. The **first launch needs internet** (cellular or Wi-Fi with internet) so `DJISDKManager.registerApp` can activate
   the key; afterwards registration is cached. The bridge phone must also reach the Mac over the LAN.

## Run

1. On the Mac: `make backend-live` (i.e. `OVERWATCH_MODE=LIVE uvicorn overwatch.main:app --host 0.0.0.0 --port 8000`).
   If `OVERWATCH_TOKEN` is set in `.env`, enter the same token in each app. In `SIMULATED` mode the Mac answers the
   bridge socket closes with code 4403; the phone socket receives an `error` message and closes.
2. Phones and Mac on the same Wi-Fi (or the Mac's hotspot). Enter the Mac's IP (e.g. `192.168.1.20`; port 8000 is
   the default; `host:port` and `ws://…` also work) in each app; it is saved in `UserDefaults`.
3. Grant permissions when asked:
   * Responder: Camera (ARKit), Location When In Use (GPS + true heading), Local Network (the Mac), Motion (only if
     AirPods head tracking is switched on).
   * Bridge: Local Network, Location (DJI fly-safe), Bluetooth (DJI SDK); connect the phone to the RC by cable.

**Responder screen:** status line (Connected · RTT), severity-coloured alert banner, compass (12 o'clock = where you
face; arrow = subject from `responder_view.kin.bearing_rel_deg`; range / closing / TTC / clock), LiDAR sector bars
(dashed `?` = not observed, green = observed clear, red/orange/yellow bar = obstacle distance), ACK button
(`{"type":"ack","alert_id":…}`, stops the repeating CLOSING_FAST buzz), Voice / Haptics / 3D toggles; gear → host,
token, video uplink, AirPods head tracking (+ "zero" button), live sensor diagnostics. The screen never sleeps while
running.

**Bridge screen (landscape):** FPV preview with PILOT IN CONTROL / MAC CONTROL / SIMULATOR banners, the big red
**DISABLE VIRTUAL STICK** button (latches: the Mac cannot re-enable until you tap RE-ARM), links, virtual-stick state
(setpoint and its age, what was actually sent after caps, Mac-link age), the **Real flight armed** switch (off by
default: virtual stick is refused unless the DJI simulator runs), telemetry, simulator start/stop + sim-only
takeoff/land, RTL, video uplink mode (H.264 / JPEG / off), scrolling audit log (also sent to the Mac as
`{"type":"audit"}`).

## Protocol notes (what the apps implement)

* One device clock: `ProcessInfo.systemUptime` ms (= `ARFrame.timestamp` timebase). `capture_ts_ms` for observations
  is `ARFrame.timestamp * 1000`; telemetry uses the arrival time of the DJI flight-controller state; binary frames
  carry the same clock; `sync_reply` t1/t2 are taken on receive/transmit.
* `hello` (`"synthetic": false`) on every connect; `sync` → `sync_reply` immediately.
* Responder: JPEG (0x01, upright, ≤ 640 px, q 0.6, ~8 fps) is sent **before** the observation of the same frame;
  `camera_pose_4x4` and `obstacle_points` only while ARKit tracking is `normal`; intrinsics at depth resolution;
  `vision_boxes` are normalised xyxy with a **top-left** origin in the same upright frame as the JPEG.
* Send queue: acks / sync / hello / audit go out immediately; observations and telemetry are "latest wins" when the
  link is congested (stale ones are dropped); video frames are dropped first (H.264 then resumes at the next SPS/IDR).
* Bridge virtual stick (DJI body-frame velocity): **roll = forward, pitch = right**, yaw = deg/s, throttle = up m/s,
  control modes re-applied after every aircraft or Mac (re)connect and checked once a second.

## Test checklist

**0. Backend first (no hardware).** `make backend-live` + `make devices` (backend/scripts/fake_devices.py) must show
SYNTHETIC drone + phone sources on the dashboard. This validates the Mac side of the same protocol.

**1. LiDAR proximity without the Mac.** Responder app, no host (or Mac off / airplane mode). Hold the phone upright and
walk slowly toward a wall: the sector bars turn yellow → orange → red; at about 1.2 m (2 consecutive frames) you get
the PROXIMITY tick pattern and "Obstacle ahead, 1.1 meters" (banner: *Local rule · works without the Mac*). Back
away past ~1.5 m: the alert clears (hysteresis); walking in again re-fires (2.5 s cooldown). Point at open floor:
the front sectors show green "clear", sectors outside the field of view stay dashed `?`.

**2. Responder uplink.** Connect to the Mac: status shows *Connected · N ms RTT*; the dashboard shows the phone source
(LIVE) with the camera image, Vision boxes on people with LiDAR ranges, sectors, heading. Have someone walk in front
of the phone. Trigger alerts from the dashboard scenario/operator flow: CLOSING_FAST buzzes 3× every ~1.1 s until ACK
(and stops if the dashboard acks); TARGET_LOST two long pulses; REACQUIRED one soft tap; APPROACH a left/right ramp.
With AirPods, a 3 o'clock alert's earcon comes from the right before the speech. A Mac LIDAR_PROXIMITY echo of a
local alert shows in the banner without a second buzz.

**3. Props-off DJI simulator test (bridge).** Remove the propellers. Power the RC + Mini 2, plug in iPhone A, open the
bridge: DJI registration OK, product "DJI Mini 2", Mac connected. Tap *Start simulator* (or send `sim_start`):
telemetry shows `sim` / SIMULATOR banner and a GPS fix at the start point. *Sim takeoff*. On the dashboard lock a
subject and switch autonomy to FOLLOW: the bridge shows *MAC CONTROL · VIRTUAL STICK*, setpoints update at ~20 Hz,
telemetry `vstick_enabled: true`, the simulated aircraft moves. Axis check: a positive `forward_mps` must increase
velocity along the aircraft heading, a positive `right_mps` must move it to its right (watch `vel_ned` vs `yaw`).
With *Real flight armed* OFF, stopping the simulator immediately disables virtual stick.

**4. Watchdogs (kill the Mac).** In the simulator with virtual stick enabled, Ctrl-C the backend:
within 300 ms the banner shows *HOVER (no fresh command)* (audit: "watchdog hover"); at 2 s virtual stick is disabled,
*PILOT IN CONTROL* appears (audit: "no message from the Mac for 2.0 s") and the RC sticks fly the aircraft. Restart
the backend: the bridge reconnects with backoff, the buffered audit lines appear on the dashboard, and virtual stick
stays off until the operator selects FOLLOW again.

**5. Overrides.** (a) Move a stick / flip C-N-S / press Pause while the Mac flies: the aircraft leaves virtual stick and
the bridge shows *PILOT IN CONTROL* within ~0.5 s. (b) DISABLE button: immediate zero + disable; a FOLLOW from the
Mac is refused (audit) until RE-ARM. (c) Dashboard E-STOP: zero + disable. (d) RTL: virtual stick off, go-home.
(e) Press the Home gesture while the Mac flies: virtual stick is disabled before the app is suspended.

**6. Video.** H.264 mode: the dashboard shows the drone feed (needs `pip install av` on the Mac). If the image is
grey/garbled, switch the bridge to JPEG mode (preview must be on) and report it (see VERIFY list).

## VERIFY items (could not be checked without Xcode/hardware)

Marked `// VERIFY:` in the code:

1. `VirtualStickController.swift` — MSDK 4.16 imports `-sendVirtualStickFlightControlData:withCompletion:` as
   `send(_:withCompletion:)`.
2. `BridgeController.swift` — Swift name of `-startWithLocation:updateFrequency:GPSSatellitesNumber:withCompletion:`
   (written as `start(withLocation:updateFrequency:gpsSatellitesNumber:withCompletion:)`), and that the Mini 2 exposes
   `flightController.simulator`.
3. `BridgeController.swift` — `DJIGimbalRotation(…, ignore: true)` semantics for absolute pitch on the Mini 2.
4. `VirtualStickController.swift` — on the Mini 2 + RC-N1, stick input / C-N-S switch / Pause really makes the aircraft
   leave virtual stick (the 2 Hz `getVirtualStickModeEnabled` poll turns that into PILOT IN CONTROL).
5. `VideoForwarder.swift` — the Mini 2 raw feed, with DJIWidget-style AUD stripping, decodes cleanly in PyAV on the
   Mac (else use the JPEG uplink mode). Streams without periodic IDR frames (intra refresh) are handled by
   re-sending cached SPS/PPS after a resync, but this needs a real feed to confirm.
6. `SensorHub.swift` — `CLHeading.trueHeading` with the phone held upright tracks the camera direction (Core Location
   references the top edge of the device; upright that is ill-defined on some iOS versions).
7. `SensorHub.swift` — AirPods yaw sign: turning your head right must make `head_yaw_deg` positive.
8. `AlertFeedback.swift` — `AVAudioEnvironmentNode` listener faces −z with +x right (3 o'clock earcon from the right).

Also worth confirming on first build (not marked, high confidence from DJI's own Swift samples / headers):
DJI delegate method names (`flightController(_:didUpdate:)`, `battery(_:didUpdate:)`, `gimbal(_:didUpdate:)`,
`simulator(_:didUpdate:)` — a mismatch compiles but is silently never called; Xcode warns "nearly matches optional
requirement"), `DJIFlightControllerState.gpsSignalLevel`, `DJIBatteryState.chargeRemainingInPercent`, and the
`UISupportedExternalAccessoryProtocols` list (`com.dji.video`, `com.dji.protocol`, `com.dji.common`, plus
`com.dji.logiclink` which DJI's current sample also lists).

## Known limitations

* The responder uplinks **upright** frames and boxes and reports `image_orientation`; the Mac derives the upright
  horizontal FOV from `camera_intrinsics_3x3` (≈ 50° held in portrait, ≈ 64° in landscape) for box bearings and
  the map frustum. Simulator scenario S13 checks both orientations against ground truth.
* AirPods head yaw is relative to a calibration taken when head and phone face the same way (automatic at start,
  or the "zero" button); both IMUs drift slowly.
* The bridge never auto-takes-off a real aircraft (takeoff/land buttons only work in the DJI simulator).

## Responder marking and optional translation

The updated bridge has separate **Real flight armed** and **Allow aircraft translation** opt-ins. Camera-only
remains the default; a real full-motion request requires both opt-ins, local RC re-arm and backend site/GPS/
calibration readiness. Profile changes disable control, and RC input remains highest priority. Initial physical
translation caps: 1 m/s horizontal, 0.5 m/s vertical, 15°/s yaw. See
[PERSON_MARK_AND_FULL_FOLLOW.md](../docs/PERSON_MARK_AND_FULL_FOLLOW.md) for current setup and verification limits.
