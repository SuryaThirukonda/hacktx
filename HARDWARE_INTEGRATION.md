# Hardware integration

What each piece of hardware does with Overwatch, what has been verified, and what must be verified on the device.
**Nothing below has been run on real hardware yet** unless a section says otherwise. The whole demo works without
any of it, using simulated or recorded data that is labelled as such.

## DJI Mini 2 (drone video and telemetry)

**What it is:** stock RGB camera only. No thermal, no depth sensing, no forward obstacle avoidance, no reliable indoor
autonomy. Overwatch never assumes any of those.

**SDK:** DJI Mobile SDK **V4** (the bridge pins 4.16.2). V5 is not assumed to support the Mini 2. Compatibility of
the exact phone, iOS version, SDK build and live-stream pipeline must be verified on the device.

**How it connects:**
1. An iPhone ("iPhone A") is plugged into the RC-N1 controller and runs `ios/OverwatchDroneBridge`.
2. The app sends telemetry (10 Hz) and video (raw H.264, or JPEG fallback) to the laptop over Wi-Fi:
   `ws://<laptop-ip>:8000/ws/bridge`.
3. The backend runs in LIVE mode: `make backend-live`. The dashboard shows the source as **DJI Mini 2 bridge** with
   status `connected` only when the app has said hello and is sending; otherwise `disconnected` or `not_configured`.
   A test harness (`make devices`) declares synthetic imagery and is shown as **simulated**, never live.

**Flight stays manual for the hackathon.** The pilot keeps full control. `autonomy.allow_real_flight` stays `false`,
so the backend never sends flight commands to a real aircraft; on real hardware, go to, orbit, follow and return are
operator suggestions only. The bridge app's safety layer (300 ms command expiry → hover, 2 s of backend silence →
virtual stick off and pilot in control, E-STOP, return home) exists for the DJI simulator test with props off.

**Must be verified on hardware (in order):**
1. The app builds and registers with a DJI App Key (needs a Mac with Xcode and internet on first launch).
2. The Mini 2 connects; telemetry arrives at 10 Hz in the dashboard.
3. Video decodes on the laptop (raw H.264 through PyAV, or the JPEG uplink fallback).
4. Props off: the DJI simulator runs, and pilot stick input always takes control back.

**Without the bridge:** record video on the Mini 2 (or the DJI Fly app's cache), copy the MP4 to the laptop, and play
it as a file source: `make backend-live` with `OVERWATCH_DRONE_URL=/path/to/flight.mp4`, or select **Video file** in
the dashboard. File playback uses the file's own frame timing and is labelled REPLAY.

## Video sources (any camera)

| Source | How | Label |
|---|---|---|
| Simulator | `make backend` (default) | SIMULATED |
| Video file (MP4, MOV) | `OVERWATCH_DRONE_URL=/path/clip.mp4 make backend-live`, or select it in the dashboard | REPLAY |
| Webcam | `OVERWATCH_DRONE_URL=0 make backend-live` | LIVE |
| RTSP stream | `OVERWATCH_DRONE_URL=rtsp://host/stream make backend-live` (reconnects with backoff) | LIVE |
| DJI Mini 2 bridge | `make backend-live` plus the bridge app | LIVE |

Switching between the simulator and real inputs requires restarting the backend in the other mode; the dashboard
says so instead of pretending to switch.

## iPhone (responder app) and AirPods

The responder app is a web app (PWA) served by the backend; it works without an API key and without Xcode.

1. Laptop and iPhone on the same Wi-Fi. Build the dashboard once (`make build`), then start the backend so it
   listens on the LAN: `make backend` (simulator) or `make backend-live` (real video); both bind 0.0.0.0:8000.
2. Find the laptop's address (`ip -4 addr` on Linux, `ipconfig getifaddr en0` on a Mac).
3. On the iPhone open `http://<laptop-ip>:8000/responder` (add `?token=...` if `OVERWATCH_TOKEN` is set).
4. Tap **Enable voice** once (browsers only allow speech after a tap). Alerts are then spoken through the phone's
   current audio output, so paired **AirPods** play them. This is ordinary speech, not head-tracked spatial audio.
5. Tap **ACKNOWLEDGE** on an alert; the dashboard records who acknowledged it and when.

**What a browser cannot do here (and what the app does instead):**
- **Vibration:** iOS Safari has no vibration API. The app flashes the screen instead; Android browsers vibrate.
  AirPods have no programmable haptics.
- **Location and compass over plain http:** browsers only share location (and register the offline service worker)
  in a secure context. On `http://<laptop-ip>` the app says so and shows targets on the map without bearing or
  distance. For location on the phone, serve over HTTPS, for example with a locally trusted certificate:
  `mkcert <laptop-ip>` then
  `uvicorn overwatch.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile <key>.pem --ssl-certfile <cert>.pem`
  (install the mkcert root certificate on the iPhone first).
- **GPS on the incident map:** a phone GPS fix can only be placed on the incident map when the incident has a
  geographic origin (WGS84 reference). Without one, the app says "GPS cannot be placed on the incident map".
- **Clock directions** ("2 o'clock") are only given when the compass heading is present and its accuracy is
  20 degrees or better; otherwise directions are compass points (north-east), or "refer to the map".
- **LiDAR and ARKit:** a browser cannot read them. The native app `ios/OverwatchResponder` (written, never built
  here: no Xcode on this machine) streams ARKit pose and LiDAR proximity to `/ws/phone`; the older sensor view is at
  `/responder/sensors`.

In a SIMULATED backend the responder app offers **Use the simulated responder position (SIMULATED)**, which places
the scripted responder from the simulator so bearings can be demonstrated; it is labelled everywhere it appears.

<!-- iPhone LiDAR scan import and near-IR camera sections are added in phases 12 and 16. -->

## Near-IR camera

A near-IR camera sees reflected light (IR illuminators, moonlight), not heat. Overwatch treats it as near-IR, never
as thermal, and never reports a temperature.

- **Sources:** a USB webcam with the IR-cut filter removed (or a NoIR module), recorded IR footage, or an RTSP
  stream. Select the `near_ir` source on a LIVE backend with a webcam index, file path or URL:

  ```bash
  make backend-live OVERWATCH_DRONE_URL=/path/to/ir_clip.mp4
  ```

  Then pick **near_ir** in the Sources panel with the same value.
- **Pipeline input:** frames enter as mono. Enhancement (denoise, CLAHE, gamma, contrast stretch) is off until the
  operator turns it on (`POST /api/sources/near-ir/enhancement`). Its steps are carried in each frame's metadata
  and labelled "IMAGE PROCESSING (near-IR, not thermal; no temperatures)".
- **Without new hardware:** **Source health → Near-IR enhancement preview** in the dashboard, or
  `POST /api/near-ir/preview`, shows raw against processed for any recorded frame, in any mode.
- **Detection:** the simulator's colour-blob detector finds nothing in mono IR. A trained detector (YOLO) is needed
  for people in IR footage, and none has been evaluated on IR here.

## Drone model checks (needs the Mini 2)

The kinematic Mini 2 model's response time constants are assumed, not measured (`backend/overwatch/sim/aircraft.py`
says which values come from the spec sheet).

- **To fit them:**
  1. In DJI's built-in simulator, with the propellers off, fly forward velocity steps of 3, 6 and 10 m/s through
     the bridge.
  2. Save each step as `data/recordings/dji_steps/<name>.csv` with columns `t_s,cmd_forward_mps,vel_forward_mps`.
  3. Run `tests/unit/test_aircraft_fit.py`. It is skipped until recordings exist.
- **Pass criteria:** rise time and steady-state speed within 15%, RMS velocity error under 0.3 m/s.
