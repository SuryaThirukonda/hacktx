# Original DJI SDK interface, website controls — 

**Latest:** [Drone V1 handoff](../DRONE_V1_HANDOFF.md) adds Movement + camera, manual WASD translation and automatic visual follow. The rotation-only workflow below remains available.

Marking/designating a person selects the subject; it does not start movement. The tested legacy stream also
carried JPEGs without canonical telemetry/RC control status, and real-flight output was disabled. These were
independent blockers. A red target circle alone does not prove aircraft control is active.

## Installed SDK update

The original `DJISDKSwiftDemo` sample was updated, signed, built and installed on the iPhone. Its existing FPV,
Test Frame and Start Stream interface is retained. The source workspace remains:

```
$HACKTX_ROOT/Mobile-SDK-iOS/Sample Code/SwiftSampleCode/DJISDKSwiftDemo.xcworkspace
```

`ios/integrate_sdk_sample.rb` replaces only the existing relay class and adds the guarded native core sources.
Bundle identifier, signing and DJI registration remain those of the original sample. Backups are ignored under
`ios/sdk-sample-backups/`. The standalone OverwatchDroneBridge interface is not part of this device workflow.

The updated relay sends JPEG frames, DJI telemetry, simulator status, RC takeover state and short-lived control
tokens through `/ws/bridge` on compatibility port 8765. All operator actions are on the website. Local RC
freshness/neutral checks, takeover latch, token rotation, command TTL and watchdog remain enforced on the phone.
Releasing RC sticks never resumes AI movement; explicit website re-arm and Start are required.

## Test Frame and chat

`POST /frame` now decodes and acknowledges a diagnostic JPEG while a stream is connected, rather than returning
409. It does not inject that JPEG into active tracking, replace the stream or change control state. Invalid JPEGs
still return 422; the endpoint still requires LIVE mode. This works with old and updated streaming protocols.

Dashboard/responder **Chat with Overwatch** accepts text and push-to-talk clothing requests, including a blue
sweatshirt and black pants. Matching uses OpenAI Decisions and the existing single-subject approval checks:
successful OpenAI result, confidence at least 90%, fresh confirmed person and unique consistent clothing evidence.
Other matches require review. The score is model confidence, not a calibrated identity accuracy guarantee.
`status`, `stop`, `clear mark` and `help` provide task-specific control conversation. Mac localhost supports
microphone permission; a phone browser needs trusted HTTPS for push-to-talk. Clothing matching is coarse color
evidence; it does not infer fine garment details or personal identity from faces.

## Current simple website workflow

The LIVE dashboard shows video, **Drone controls**, **Fly with keyboard** and target chat. Review queue, map,
scenario list and timeline are omitted from the LIVE control screen; observation data is retained internally.

1. Start the backend using `make drone-live`, open the existing SDK FPV screen and Start Stream on the reachable hotspot.
2. On `/dashboard`, press **Enable aircraft controls** with neutral RC sticks/dial. This explicitly enables the
   backend camera-only output and sends a guarded native re-arm request. Wait for **Controls enabled**.
   There is no indoor/outdoor selector. Actual link, telemetry, battery and airborne readiness still apply.
3. Press **Enable keyboard control**: A/D or left/right rotates the drone; W/S or up/down tilts the camera.
   On-screen arrows support the same hold-to-move controls. Keys do not translate the aircraft.
4. To follow, designate a visible person (or use text/voice and confirm when required), then **Start camera follow**.
   Target marking alone never starts movement. DJI virtual-stick confirmation and SDK errors are visible.
5. **Stop / RC control** stops movement and turns backend physical output off. RC input always latches pilot
   takeover; releasing the RC never automatically resumes control. Re-arm, then start again explicitly.

The DJI simulator remains under **Connection details / DJI simulator** for landed, props-off checks. The initial
backend state is still physical output OFF after every restart, but the website button now supplies the explicit
camera-only opt-in without requiring a special launch flag. It does not take off or start any motion. Simulator
requests still require a landed/props-off confirmation. Lost/unverified targets, stale video/telemetry, RC takeover,
command expiry and link loss still stop control. Physical callback timing/axis behavior require hardware testing.

Full-motion follow remains implemented behind explicit backend full/real-flight configuration, website re-arm,
fresh telemetry, GPS, actual site geofence/altitude bounds and calibrated target ground position. No site geometry
has been invented. Physical movement and controller handoff are not established by passing software tests.

## Decisions API and alternatives

Historical live errors included DNS resolution failures and an 800 ms timeout. The latest anonymous retry
succeeded in approximately 194 ms, selected the intended clothing candidate and returned 69% confidence, correctly
requiring review. OpenAI Decisions remains selected. Changing models would not add missing phone telemetry,
arm control or replace the explicit Start action.

For a later change, OpenAI [Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
is the closest option for richer text/image extraction and conversation with schema-constrained output.
[Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) and
[Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) are other
documented schema-based options. Evaluate them on the same recorded candidate evidence, latency, ambiguity and
false approvals before switching. Keep frame-by-frame yaw/gimbal/translation servo control local and deterministic.

## Verification and remaining device work

256 backend tests passed, one hardware-recording test skipped. Dashboard typecheck/build and both unsigned and
signed builds of the original SDK sample passed. Installation succeeded. Automatic app launch was refused because
the phone had locked; the user must unlock/open it and restart the stream. Since backend restart, no new device
video or telemetry had arrived at the last check. Physical-flight output remains OFF and no movement command
was issued. Simulator response and physical RC takeover still require the connected hardware check.

All repository changes are local and unpushed.


## Simplification verification

The website camera enable API checks a connected capable relay, fresh video/DJI telemetry, complete camera-control
telemetry and battery. It rejects the full-motion profile, never enables translation, and remains MANUAL after
arming. Stop works even with the link offline. LIVE Follow/Hold mode requests now fail explicitly when controls
are disabled; Follow additionally requires the current verified visible subject. Native RC checks remain authoritative.
Control errors persist across status heartbeats until successful DJI enable, a new setup request or reconnection.
No physical movement command was sent while implementing this update.
