# Wire contracts (schema_version 1.0)

Source of truth: `backend/overwatch/schemas.py` (pydantic). Dashboard mirror: `dashboard/src/types/contracts.ts`.
Native apps: `ios/OverwatchResponder/Sources/Contracts.swift`, `ios/OverwatchDroneBridge/Sources/BridgeContracts.swift`.

Conventions: local frame ENU meters (x east, y north, z up); headings in degrees clockwise from local +y (true
north when `world.north_offset_deg = 0`); timestamps in milliseconds; **missing geometry is `null`, never 0**.

## Binary video frames (device → Mac)

Used on `/ws/phone` and `/ws/bridge`:

```
byte 0       kind: 0x01 = JPEG, 0x02 = H.264 Annex-B access unit (SPS/PPS in-band before IDR)
bytes 1..8   capture timestamp, float64 big-endian, device clock milliseconds
bytes 9..    payload
```

## Clock sync (server-initiated, both device sockets)

Server → device: `{"type":"sync","t0":<server ms>}` every ~5 s.
Device → server: `{"type":"sync_reply","t0":<echo>,"t1":<device recv ms>,"t2":<device send ms>}`.
The server keeps the minimum-RTT estimate: `offset = device − server`, `server_ts = device_ts − offset`. Until an
estimate exists, capture times are replaced by arrival time and marked `timestamp_quality: arrival`.

## `/ws/phone` — responder iPhone (native app)

Uplink JSON `phone_observation` at ~10 Hz:

```json
{"schema_version":"1.0","type":"phone_observation","source_id":"responder-iphone","seq":82,
 "capture_ts_ms":120805.0,"arkit_tracking_state":"normal","tracking_reason":null,
 "camera_pose_4x4":[[...],[...],[...],[...]],          // ARKit world-from-camera, row-major, y up
 "camera_intrinsics_3x3":[[fx,0,cx],[0,fy,cy],[0,0,1]],  // at depth-map resolution
 "image_resolution":[1920,1440],                          // sensor-native capturedImage size
 "image_orientation":"right",                             // up|down|left|right: rotation applied to make JPEG + boxes upright
 "lidar_supported":true,"depth_mode":"sceneDepth",
 "depth_valid_fraction":0.79,
 "sectors":[{"sector":"front","nearest_m":1.4,"confidence":"medium","num_valid_points":124}, ...],
 "obstacle_points":[[x,y,z], ...],                        // ARKit world, floor removed, <= 400 voxel-downsampled
 "floor_y_m":-1.31,"heading_deg":45.2,"heading_accuracy_deg":12.0,"head_yaw_deg":null,
 "gps":{"lat":30.2669,"lon":-97.7729,"accuracy_m":5.0},"battery_pct":88,
 "vision_boxes":[[x0,y0,x1,y1], ...],                     // normalised, top-left origin, in the upright image
 "vision_ranges_m":[3.1,null], "local_alert_active":false}
```

Sector semantics: `nearest_m: null` with `confidence: "unknown"` = not observed (outside FOV / no valid depth);
`nearest_m: null` with any other confidence = observed and clear. Sectors: `left` [−75°,−40°), `front_left`
[−40°,−12°), `front` [−12°,12°], `front_right` (12°,40°], `right` (40°,75°] relative to the phone's horizontal
forward. The depth algorithm is `backend/overwatch/geometry/phone_depth.py` (Swift mirror: `DepthProcessor.swift`).

Orientation: the JPEG and `vision_boxes` are upright (portrait when the phone is held normally, `image_orientation:
"right"`), while the depth map and `camera_intrinsics_3x3` stay sensor-native (landscape). The Mac takes the upright
horizontal FOV from the intrinsics (`cy/fy` when rotated, `cx/fx` otherwise) for box bearings and the map frustum;
`backend/overwatch/geometry/phone_view.py` mirrors the app's mapping and scenario S13 tests both orientations.

Uplink binary: camera JPEG frames (kind 0x01), ~5–15 FPS, sent *before* the observation they belong to.
Uplink JSON `{"type":"ack","alert_id":"a-12"}` when the responder acknowledges.

Downlink: `alert` objects (see below), `responder_view` (compact state ~5 Hz), `transcript`, `ack`, `sync`.

## `/ws/bridge` — DJI bridge iPhone (iPhone A)

Uplink JSON `telemetry` at ~10 Hz:

```json
{"schema_version":"1.0","type":"telemetry","source_id":"mini2-bridge","seq":1,"capture_ts_ms":120830.0,
 "lat":30.2669,"lon":-97.7729,"alt_rel_m":18.4,"yaw_deg":91.2,"pitch_deg":-1.1,"roll_deg":0.4,
 "vel_ned":[0.2,1.1,0.0],"gimbal_pitch_deg":-45.0,"battery_pct":72,"gps_level":4,"sats":14,
 "flying":true,"vstick_enabled":true,"sim":false,"flight_mode":"VIRTUAL_STICK"}
```

`sim: true` means the DJI flight simulator is running (props off): the Mac then treats the aircraft as a safe
command target. With `sim: false` virtual-stick commands are only sent if `autonomy.allow_real_flight: true`.

Uplink binary: video (0x02 raw H.264 from `DJIVideoFeeder`, or 0x01 JPEG). Uplink JSON `hello`, `status`,
`audit` (`{"type":"audit","text":"watchdog hover"}`), `sync_reply`.

Downlink commands (`BridgeCommand`):

| type | payload | bridge behaviour |
|---|---|---|
| `vstick` | `{forward_mps, right_mps, up_mps, yaw_rate_dps, ttl_ms, seq, ts_ms}` | latch as current setpoint; the bridge's own 20 Hz loop sends it to the aircraft. Body frame velocity mode: **roll = forward, pitch = right**. |
| `gimbal` | `{pitch_deg}` | absolute pitch, −90…0 |
| `mode` | `{vstick: bool, motion_profile?: "full" \| "camera_only"}` | configure profile before enabling/disabling virtual stick; re-apply control modes after every (re)connect |
| `hover` | `{}` | zero setpoint |
| `rtl` | `{}` | disable virtual stick, start go-home |
| `estop` | `{}` | zero setpoint + disable virtual stick |
| `sim_start` / `sim_stop` | `{lat, lon}` | DJISimulator on/off |
| `ping` | `{}` | heartbeat |

Bridge watchdogs (must be implemented on the phone, not the Mac): no `vstick` for 300 ms → send zero velocity;
no message from the Mac for 2 s → disable virtual stick. The bridge explicitly listens to RC hardware callbacks
and latches takeover for stick/dial input, Pause/RTH or a flight-mode change; it does not assume the firmware
automatically exits virtual stick. Hardware timing/callback behavior must be verified on the aircraft.

Control-capable bridges declare `pilot_override_protocol: 1` in `hello`. Their periodic `status` carries
`control_inhibited`, `pilot_reason` and `control_token`. An immediate
`{"type":"audit","event":"pilot_override","text":"..."}` cancels the Mac's plan and pending flight decisions.
Only local phone re-arm can clear the latch; it rotates `control_token`. The Mac includes that token in command
payloads. The phone rejects movement/mode-enable commands with an old token, stale timestamps, duplicate sequence
numbers, inhibited control or stale/non-neutral RC input. After re-arm, the operator explicitly selects FOLLOW.

Bridge status also reports `motion_profile`. In `camera_only`, the backend sends zero forward/right/up velocity,
caps yaw at 15 degrees/s and limits gimbal changes to 10 degrees/s. The native bridge independently zeros those
three velocity axes and caps yaw in this profile. Physical full-motion requires bridge status
`translation_supported: true`, `translation_armed: true`, local real-flight arm/re-arm and backend site/GPS/
calibration gates. Both ends cap the initial physical test at 1 m/s horizontal, 0.5 m/s vertical and 15°/s yaw.
Lost/unverified targets or stale video stop camera following while retaining designation.
Automatic RTL and translating search plans are not executed in this profile. See
[CAMERA_FOLLOW_TEST.md](CAMERA_FOLLOW_TEST.md) for readiness and simulator checks.

Authenticated keyboard endpoints under `/api/autonomy/keyboard/` use the same native control gate:
`POST start` returns an exclusive random `session` in camera-only mode; `POST input` accepts
`{session, seq, yaw, tilt}` (monotonic sequence; normalized axes −1…1). Accepted inputs renew a 300 ms lease.
`POST stop` with `{session, seq}` disables virtual stick for that session. Expired, superseded, reordered or
pilot-inhibited inputs are rejected; old stops cannot cancel a newer owner. All keyboard output has zero
translation. Any mode change invalidates the session; no web endpoint re-arms the phone.

Incident-scoped `/api/incidents/{iid}/person-mark` supports GET status and POST `{text}` to request one mark.
`/confirm` and `/follow` accept `{request_id}`; `/release` cancels/clears; `/preview?request_id=...` returns a
memory-only request-time JPEG with `Cache-Control: no-store`. `/transcribe?format=webm|mp4|wav` accepts raw audio
up to 4 MB and returns `{text}`. All routes require the configured access token. Transcription itself never
executes a command. Automatic approval requires successful OpenAI confidence ≥0.90 plus current, unique clothing
evidence. Marking and starting movement are distinct actions; neither can clear pilot takeover.

Legacy AerialCopilot ingestion is `/ws` (bare JPEG binary messages) and `POST /frame` (one JPEG). Both use the
same LIVE pipeline and access-token checks, but the legacy connection is never a flight-command target. The
`run_drone_live.py` launcher exposes port 8765 as an alias of the same port-8000 runtime. See
[DRONE_LIVE_INTEGRATION.md](DRONE_LIVE_INTEGRATION.md) for setup and hardware verification limits.

## Dashboard sockets

* `/ws/state`: `hello` then `WorldState` (~10 Hz) and `extras` (~2 Hz: occupancy PNG, motion label, describe
  proposal, cross-view association, audit logs, metric series).
* `/ws/events`: `backlog`, then ordered `event`, `decision`, `alert`, `transcript`, `ack`, `describe_proposal`.
* `/ws/alerts`: responder channel (web page or native): `responder_view`, `alert`, `transcript`, `ack`; accepts
  `{"type":"ack","alert_id":...}`.
* `/ws/video/{drone|phone}`: binary `[uint32 BE header length][FrameOverlay JSON][JPEG]` — overlays belong to that
  exact frame (`frame_id`).

## Alert (Mac → phone)

```json
{"type":"alert","alert_id":"a-12","kind":"CLOSING_FAST","severity":"critical","ts_ms":..,"expires_ts_ms":..,
 "text":"Subject 2 o'clock, 10 meters, closing fast (3.4 m/s, TTC 2.9 s)",
 "spoken_text":"Subject 2 o'clock, 10 meters, closing 3 meters per second",
 "bearing_rel_deg":60,"clock":2,"range_m":9.8,"closing_mps":3.4,"ttc_s":2.9,"haptic":"CLOSING_FAST",
 "delivery":["dashboard","phone_speech","phone_haptic"],"message_source":"rules","ack_required":true,
 "can_read_as_confirmed_location":true}
```

Haptic patterns: `CLOSING_FAST` (3 strong transients 80 ms apart, repeat until ack), `TARGET_LOST` (2 long pulses
0.6), `REACQUIRED` (single soft tap), `PROXIMITY` (rapid ticks scaled by distance), `DIRECTION_LEFT` /
`DIRECTION_RIGHT` (rising/falling ramp). Severities: `info | attention | warning | critical | sensor_fault |
local_proximity`.


## Original SDK website setup and readiness

The original sample can advertise `web_control_supported` in canonical bridge status. Authenticated
`GET /api/autonomy/readiness` returns actual autonomy state, blockers, virtual-stick telemetry and recent bridge audit.
`POST /api/autonomy/bridge/setup` accepts `sim_start`, `sim_stop` or `operator_rearm`; `sim_start` requires
`props_removed: true` and a landed physical aircraft. All setup requires a capable connected relay and fresh DJI
telemetry. Physical re-arm is rejected when backend real-flight output is off. Setup enters MANUAL and does not
start follow. The bridge attaches its current control token; native setup checks token and clock age, and re-arm
checks fresh neutral RC input. Real-flight/translation flags come from backend configuration. `operator_rearm`
rotates the token rather than enabling virtual stick directly. Legacy video-only sockets cannot execute setup.

While streaming, `POST /frame` returns `diagnostic_only: true, stream_active: true` after JPEG validation and leaves
the active stream/tracker unchanged. It remains LIVE-only. A test-frame receipt is not aircraft readiness.


Website setup additionally accepts `enable_camera_control` and `disable_control`. Enable is explicit session-only
camera-only real-flight opt-in with fresh video/telemetry, complete altitude/heading/battery/gimbal data and a battery
check; full-motion enable is rejected. It sets MANUAL, sends native `operator_rearm` with translation false, and does
not emit movement or takeoff. Native RC-neutral/freshness checks may still refuse. Disable always stops to MANUAL
and turns backend real output off, including after link loss. LIVE Follow/Hold REST requests return 409 when output
is off/inhibited; Follow requires a currently visible verified designated target. Bridge extras expose `connected`
and a persistent latest DJI rejection `error` to distinguish stream capability from a current connection.
