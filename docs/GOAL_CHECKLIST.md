# Goal verification — 

Changes are local and unpushed. The software scope is implemented; device/flight verification is not complete.

## Implemented and verified

- [x] Feed ingestion into the backend and YOLO/person tracking; the original sample has an updated background control relay; its old installed binary remains video-only.
- [x] OpenAI Decisions selected; no TypeSafe/Jev selected. An anonymous live API test succeeded.
- [x] Dashboard WASD/arrow controls for yaw/gimbal, explicit enable, key release, blur and timeout stops.
- [x] Responder and dashboard text/push-to-talk requests for one marked person.
- [x] Automatic mark approval at OpenAI confidence >=0.90 only when current clothing evidence is unique and consistent.
- [x] Below-threshold/ambiguous/stale results require review; late cancelled results cannot restore a mark.
- [x] Marking starts tracking; aircraft movement has a separate explicit Start action.
- [x] Designation retained on loss; physical translation stops on lost/unverified targets and does not fly search routes.
- [x] Physical movement cannot resume on a different track without explicit track approval.
- [x] Full translation supports forward/backward, sideways and vertical commands behind backend/site and phone opt-ins.
- [x] RC takeover latches pilot control; releasing sticks cannot automatically resume control.
- [x] Initial physical limits on both ends: 1 m/s horizontal, 0.5 m/s vertical, 15 degrees/s yaw.
- [x] No 3D model changes requested or made.

Evidence: 256 backend tests passed, 1 skipped; dashboard build and unsigned iOS device build passed; Swift
profile/motion limits passed. Real OpenAI transcription succeeded using generated speech, without recording a
microphone. HTTPS API and secure WebSocket proxy checks passed using a temporary localhost certificate.
The real clothing API test returned 76% confidence and therefore did not qualify for automatic approval.

## Required to complete device verification

- [x] With user-confirmed landed/props-off setup, install rebuilt **DJISDKSwiftDemo** from the original sample workspace.
      Keep its original FPV UI; controls are on the website. Do not install the separate bridge UI.
- [ ] Reconnect the hotspot, check the current Mac IP and Start Stream to port 8765.
      Confirm live video AND canonical DJI telemetry and control-relay capability reach the backend.
- [ ] Verify yaw/gimbal, command expiry, link loss, RC stick/dial/Pause/RTH takeover and explicit re-arm in DJI's simulator.
- [ ] Configure HTTPS with a certificate trusted by the responder phone for push-to-talk there. HTTP text works;
      Mac localhost microphone input works with permission. No device certificate trust was installed automatically.
- [ ] Supply the actual outdoor GPS reference, geofence, altitude settings and ground calibration before translation.
- [ ] Enable backend full/real-flight and explicit website re-arm only for that verified test; verify axis direction and
      RC handoff physically. Software tests do not prove hardware callback timing or physical flight behavior.

Last runtime audit: LIVE, OpenAI, camera_only, MANUAL, physical-flight output OFF, mark idle. No drone frames or telemetry
had arrived since the latest restart. Do not report physical follow as tested from that state.

Latest SDK relay/Test Frame/readiness/chat fix: [SDK_WEBSITE_CONTROL_FIX.md](SDK_WEBSITE_CONTROL_FIX.md).

Setup: [PERSON_MARK_AND_FULL_FOLLOW.md](PERSON_MARK_AND_FULL_FOLLOW.md).
Simulator checklist: [CAMERA_FOLLOW_TEST.md](CAMERA_FOLLOW_TEST.md).
