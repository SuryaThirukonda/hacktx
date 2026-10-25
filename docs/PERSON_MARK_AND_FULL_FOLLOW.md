# Responder mark requests and full-motion follow

Both `/responder` and `/dashboard` have **Chat with Overwatch · one person of interest**. They share one subject designation in the current
active incident. Type “Hey mark the guy wearing the blue T-shirt and black pants”, or hold **Hold to talk**,
speak, then release to send. Allow microphone access first; if you release during the permission prompt, hold
again afterward. Recordings stop at 20 seconds; leaving the tab cancels recording.

The backend transcribes audio using `gpt-transcribe`, then runs the same text matching flow. The OpenAI key stays
on the backend. Audio is bounded to 4 MB, stays in memory and is not saved as a recording. See the
[official OpenAI transcription guide](https://developers.openai.com/api/docs/guides/speech-to-text).
The responder's **Enable voice** option also announces results and confidence.

## Approval and tracking

The current configuration sends locally extracted clothing-color observations to OpenAI Decisions, not drone
images. It measures coarse upper/lower colors. Fine garment details, logos, hats, faces and other unsupported
attributes require review rather than automatic approval.

Automatic approval requires **OpenAI confidence ≥90%**, a successful non-mock/non-fallback result, a fresh
confirmed person, a unique clothing match at request and result time, and adequate separation between model
scores when probabilities are provided. Stale answers, a changed target/incident, contradictory clothing or
ambiguous people cannot automatically approve a mark. Confidence is a model score, not measured identity accuracy.

Otherwise, review the request-time preview and press **Confirm this person**, or clear/retry. Manual confirmation
rechecks visibility and freshness. Previews remain in memory. An approved mark starts persistent tracking and
highlights the subject on the dashboard. **It does not itself start aircraft movement.** A new request pauses
movement and replaces the previous mark before searching. Clearing a request invalidates late results. Loss retains the designation; a new
track binding needs explicit confirmation before physical movement can resume.

## Phone microphone connection

Mac `http://localhost:8000/responder` can use the microphone with permission. A phone visiting
`http://<Mac-IP>:8000/responder` cannot: browser microphones require a secure context. Text still works.

For phone push-to-talk, supply an HTTPS certificate/key already trusted by that phone, including the Mac's
hostname/IP. The dashboard development server supports these files and proxies API/WebSocket traffic to :8000:

```sh
cd $PROJECT_ROOT/dashboard
OVERWATCH_HTTPS_CERT=/absolute/path/to/cert.pem OVERWATCH_HTTPS_KEY=/absolute/path/to/key.pem npm run dev
```

Open `https://<certificate-hostname>:5188/responder` on the phone. Keep private keys on the Mac. The DJI bridge
still connects to the backend on port 8000. No certificate has been installed/trusted on the user's phone
automatically; that device setup remains a prerequisite for phone voice input.

## Aircraft motion

`make drone-live` remains camera-only with physical-flight output disabled. Use **Start camera follow** after
marking for yaw/gimbal; the button reports its readiness blocker. Start with the DJI simulator, props removed,
as described in [CAMERA_FOLLOW_TEST.md](CAMERA_FOLLOW_TEST.md).

Full-motion follow supports forward/backward, sideways and up/down. Rebuild/install the original **DJISDKSwiftDemo**
using its existing workspace. Its updated background relay supports website control; old installed versions remain video-only. Verify axes, target loss and RC
takeover in the props-off simulator before an outdoor test.

Prepare the actual site's YAML: `world.geo_reference`, a geofence in that local coordinate frame, altitude
range, desired altitude and standoff. Obtain valid ground calibration; never reuse simulator site coordinates.
Then start:

```sh
cd $PROJECT_ROOT
backend/.venv/bin/python backend/scripts/run_drone_live.py --engine openai --motion-profile full --real-flight --config /absolute/path/to/site.yaml
```

On the website, explicitly arm/re-arm controls after configuring the backend. The original SDK relay receives
those permission flags but still checks RC freshness/neutral state locally and rotates its control token.
The separate bridge app is not required. The pilot must place
the aircraft airborne, inside the configured altitude range and at least 2 m inside the fence, with a strong
GPS fix (level ≥4, at least 6 satellites). Full motion requires fresh video/telemetry and ground calibration.
Without a calibrated target ground position, physical translation holds rather than guessing from box size.

Press **Start aircraft follow** for the current verified mark. Both ends cap the initial physical full-motion
test to **1 m/s horizontal, 0.5 m/s vertical and 15°/s yaw**. Keyboard controls remain camera-only.
Lost/unverified targets stop physical translation; no speculative translating search is flown.

RC stick/dial input, Pause/RTH, a mode change or DISABLE latches pilot control. Releasing the sticks never
automatically resumes movement: explicit website re-arm and Start are required. Builds/software tests do not
establish physical RC callback timing or axis direction on this aircraft.

No real site coordinates were invented, no updated app was installed automatically, no physical-flight output
was enabled, and no physical movement command was issued. Outdoor translation remains unverified until these
site/device checks are completed. Changes are local and unpushed.

## Conversation

Typed and push-to-talk clothing requests share the Decisions matching flow. The assistant also understands
`status`, `stop`, `clear mark` and `help`. Status reports actual backend blockers, including MANUAL mode, missing
telemetry, a video-only relay and pilot takeover. It does not claim movement from a red marker or a command receipt.
Chat is scoped to marking and control status, rather than general-purpose conversation.
