# Guardrails, privacy and limits

These are product features, shown live in the console's Guardrails panel.

| Guardrail | How it is enforced in code |
|---|---|
| No face recognition or biometric identity | Re-acquisition uses session-scoped clothing colour histograms (`vision/appearance.py`); galleries are cleared when an incident or session ends. No face crops are made or stored. |
| A human selects the subject | Locks are created only by an operator click (`/api/click`, `/api/locks/*`) or an operator-confirmed D3 text match. Decision models never lock anyone. |
| Never switch identity when unsure | Rebinds need distance gating + appearance + margin; crossings with similar people require consistent motion; otherwise the lock goes UNCERTAIN and asks (D2 at ≥ 0.75 confidence, or the operator). Rebinds are labelled UNVERIFIED. |
| Active incident with consent | `privacy.incident_required_for_lock`: locks and recording need an incident started with an explicit consent checkbox. |
| No intent or threat inference | Alerts are kinematic facts (range, subject-induced closing speed, time-to-contact) from validated geometry. The motion label (D4) is restricted to observable motion and never gates alerts. |
| Rules own safety | Critical alerts, LiDAR proximity, geofence, altitude, speed caps and battery RTL are deterministic. AI answers are bounded choices with a deadline and a rules fallback. |
| Real-flight autonomy off by default | `autonomy.allow_real_flight: false`: virtual-stick commands go only to the simulator or the DJI flight simulator (props off). Otherwise they are computed and shown as ADVISORY. |
| Pilot override | Any stick input exits virtual stick (DJI behaviour); the bridge hovers after 300 ms without commands and hands control back after 2 s without the Mac. E-STOP latches. |
| No crowd monitoring | More than `privacy.crowd_threshold` people disables subject locks with a visible notice. |
| Retention off by default | Nothing is recorded unless an operator starts a recording (consent-gated outside simulation). Recordings stay on the Mac (`sessions/`, git-ignored). |
| No images to cloud models by default | Decision engines receive structured facts only unless `decisions.allow_images` and `privacy.send_images_to_cloud` are both enabled. |
| Honest provenance | Every frame/observation is labelled LIVE, RECORDED or SYNTHETIC; stale sources dim and suspend claims; metric readouts disappear when calibration or registration is invalid. |

## Deliberately not built

Face recognition, cross-session identity, social-media lookups, threat or hostility scoring, autonomous pursuit
without a human in the loop, crowd surveillance, and any claim of operational emergency-service fitness.

## Flight and legal checklist (from the Overwatch plan)

Fly only with a qualified pilot in command, visual line of sight and a spotter; never over people who are not part
of the exercise; check B4UFLY/TFRs (UT game days), Austin park rules and Texas Gov. Code ch. 423; film consenting
teammates only. The demo station runs the aircraft on a stand with props off and the DJI simulator.

## Hackathon rules note

Both source plans flag that HackTX requires project code to be written during the event. Check with the organizers
before relying on code written before the hacking window.
