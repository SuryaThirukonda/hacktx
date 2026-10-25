# Judge demo script (60–90 s)

The `demo` scenario is timed for this script and loops every 72 s. The same beats work live at the tabletop
station (config/tabletop.yaml) and on a replay of the Zilker flight. Say "simulated" out loud when it is.

| t | On screen | Say |
|---|---|---|
| 0–8 s | Two live panes (drone, responder phone), fused map, `SIMULATED · SYNTHETIC` badge | "A drone sees the scene, the responder's phone sees what's close. Nobody can watch two feeds while walking into an unknown. We fuse them into one map and talk to the responder." |
| 2–3 s | Operator marks the responder, then shift-clicks the subject (orange ring, LOCKED) | "A human picks the subject. No face recognition: identity here is a session-only appearance gallery." |
| 4–6 s | Callout in the Spoken tab; responder page compass | "The phone hears 'Subject 1 o'clock, 16 meters' — measured, not guessed. Range ± error is on screen." |
| 4–12 s | Subject walks under the tree: TRACK_LOST (~4.5 s), map shows last-seen X + growing search circle; from 7 s the drone in FOLLOW runs a search primitive chosen by the decision model (AI panel: D1, probabilities, latency) | "Lost under the canopy. A decision model picks among bounded search maneuvers in ~300 ms; it can never emit raw velocities." |
| 11–13 s | Phone LiDAR: LOCAL LIDAR ALERT on the cardboard box (works offline) | "Local obstacle warning straight from the phone's LiDAR — no cloud, no drone needed." |
| 12–13 s | REACQUIRED (UNVERIFIED) | "Reacquired by appearance and geometry — flagged unverified until a human or the identity check confirms." |
| 13–22 s | Drone follows north (footprint moves on the map), safety supervisor clamps visible | "Autonomy is simulator-only by default; geofence, altitude and speed limits are deterministic." |
| 23–26 s | Sprint: red CRITICAL banner, "Subject 12 o'clock, 16 meters, closing fast", phone vibrates/speaks, ACK | "Kinematics only: closing speed and time-to-contact. Critical alerts never wait for a model." |
| 26–40 s | Sim tab: toggle *Internet down* → AI PAUSED · RULES ACTIVE; toggle *Drone video lost* → OFFLINE, no position claims; turn both off | "Break anything and it degrades honestly." |
| 40–60 s | Calibration tab: holdout marker error; System tab: latency p50/p95; `make eval` summary | "Holdout markers measure real map error. Every number has a denominator and a label: these are synthetic." |

## Live variants

* **Tabletop**: a judge holds the responder phone + AirPods; another judge is the subject. Lunge → the phone's
  CLOSING_FAST pattern (3 strong pulses) and voice from the right side.
* **Replay**: `make replay SESSION=sessions/<zilker>`. Same pipeline, inputs labelled RECORDED.

## Fallbacks

Video fails → laptop camera via `OVERWATCH_DRONE_URL=0`; phone fails → web `/responder`; drone sim fails →
software simulator; everything fails → replay of a recorded session.

## Likely questions

* *Why not Skydio?* They send video to a remote operator; we put officer-relative alerts on the responder's body
  from a consumer drone, and we show our error bars.
* *Why a decision model?* Bounded judgments (reacquire strategy, identity confirmation, text-described selection,
  notification priority) at ~300 ms with probabilities; the AI panel shows latency, confidence, rule-vs-AI diffs
  and what happens on timeouts.
* *Accuracy?* Holdout marker error and the scenario suite (synthetic, labelled). Real accuracy needs labelled
  footage — we say so.
* *Privacy?* Guardrails panel: human selection, incident + consent, no biometrics, no crowd mode, retention off,
  structured facts only to the cloud.
