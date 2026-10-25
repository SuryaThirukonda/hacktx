# Evaluation

Every number this project reports carries a label: **synthetic** (simulator ground truth), **composite**
(real pixels rearranged, pseudo-labels), **replayed real** or **live**. Nothing here measures detector accuracy on
real drone footage yet; that needs consented, labelled recordings (`make bench-models SRC=clip.mp4 LABELS=…`).

## How to run

```bash
make test             # 69 unit + integration tests (geometry, depth, registration, tracking, rules, decisions,
                      # autonomy safety, orientation, tiling, replay determinism, session reset, S16 adjudication)
make eval             # all 26 scenarios headless -> evaluation/reports/<stamp>/{report.json, summary.md}
make bench-small      # small targets: plain vs tiled detectors, recall by target height
make bench-models     # detector latency/accuracy on simulator frames (SRC=clip.mp4 for real footage)
make bench-decisions  # decision engines on designed cases (NOT RUN without keys)
```

Runs are deterministic for a given seed: the simulator, detector, tracker and mock engine are all seeded, and
nothing depends on Python's per-process string hashing. `run_scenarios.py --engine openai` swaps the mock for the
real engine (its measured latency is replayed in simulation time).

## Scenarios

| ID | What it checks | Pass criterion |
|---|---|---|
| demo | the 72 s judge walkthrough: lock, occlusion under a tree, FOLLOW, sprint, LiDAR obstacle | TRACK_LOST, LIDAR_PROXIMITY, CLOSING_FAST in their windows |
| S01 | static nadir scene: tracks + metric projection | no false CLOSING_FAST |
| S02 | known 1.4 m/s crossing | measurement only |
| S03 | subject walks then jogs toward the responder | APPROACH fires |
| S04 | subject leaves a fixed view | TRACK_LOST with last-seen, no invented continuation |
| S05 | occlusion under a canopy | TRACK_LOST then REACQUIRED |
| S06 | strong camera wobble, stationary people | no APPROACH / CLOSING_FAST (camera motion compensated) |
| S07 | gimbal pans mid-run | calibration updates or invalidates (measurement) |
| S08 | low light + blur | degrades honestly (measurement) |
| S09 | pilot climbs 20 → 95 m: people shrink from ~27 to ~3 px | recall ≥ 0.9 at 16–32 px and ≥ 0.75 at 8–16 px; below 8 px is reported, not gated |
| S10 | responder walks at a crate with the internet down | local LIDAR_PROXIMITY |
| S11 | open grass only | no LiDAR alert from the floor |
| S12 | LiDAR confidence drops out | sectors unknown, no metric claim (measurement) |
| S13 | phone held landscape, then upright | LiDAR range and bearing error ≤ 0.25 m / 2° in both orientations; cross-view accuracy ≥ 0.9 |
| S14 | no responder registration | map stays NOT REGISTERED (measurement) |
| S15 | API timeout, 429, internet loss | rules fallback, alerts still fire (measurement) |
| S16 | six scripted alert conflicts with written ground truth | every conflict logged with the engine's choice and the rules baseline |
| S17 | drone video drops, then the phone | SOURCE_OFFLINE for each, truthful UI |
| S20 | eight people wander | ID switches counted, no identity claims |
| OW1 | walk, then sprint at the responder | CLOSING_FAST from kinematics |
| OW2 | 3 s behind a tree | loss then reacquire |
| OW3 | subject exits the frame at 5 m/s, drone in FOLLOW | time-in-frame (measurement) |
| OW4 | two red shirts cross | lock must not silently switch (measurement) |
| OW5 | subject stands still, responder walks up | no APPROACH / CLOSING_FAST (subject is not the one moving) |
| OW6 | gust + frame-drop burst during the sprint | CLOSING_FAST still fires |
| OW7 | MuJoCo Skydio X2 in FOLLOW; the subject hides under three shelters | TRACK_LOST; D1 search decisions are flown (watch in /3d) |

S18 (replay fidelity) and S19 (new session resets IDs) are integration tests rather than scenes.

## Metric definitions

* **Detection recall**: GT person with visibility ≥ 0.5 counts as hit when a confirmed, currently matched track box
  has IoU > 0.2 with it. Also reported by GT box height (< 8, 8–16, 16–32, 32–64, ≥ 64 px).
* **ID switches**: per actor, distinct track IDs matched over the run minus one.
* **Projection error**: distance between a track's map position and the actor's true ground position, grouped by
  the calibration source in use (ArUco `markers`, `chained` through camera-motion estimates, `telemetry` ray
  projection). Only counted while calibration is valid.
* **Subject lock correct**: while the true subject is visible and a lock exists, the fraction of steps where the
  lock is LOCKED on the track matched to the subject (the rest split into wrong target / not locked).
* **Range error**: |true responder–subject distance − reported range| while kinematics are valid.
* **Phone vision** (S13 and any scenario with a phone): for each on-device box with a true person inside, the
  LiDAR range error and the bearing error, per orientation. Bearing error excludes boxes cut by the left/right image
  edge (their centre only bounds the bearing; the association gate treats them one-sided) and reports how many there
  were. **Cross-view** accuracy (box ↔ drone track) and coverage (share of boxes associated at all).
* **Decision adjudication** (S16): the engine's D5 choice, the logged rules baseline and what was actually
  announced, each against the scenario's written answer.

## Results (synthetic, seed 0)

The full table is in `evaluation/reports/<stamp>/summary.md` after `make eval`; the latest run:

Run `run-132912`:

All values come from the simulator's ground truth. They validate the
pipeline's logic and geometry, **not** detector accuracy on real video.

| Scenario | Expected events + limits | Detection recall | ID switches | Subject lock correct | Range err p50 (m) | Projection err p50 (m) |
|---|---|---|---|---|---|---|
| demo Judge walkthrough | PASS | 0.952 (1963/2063) | 13 | 1.0 | 0.554 | chained: 0.307, markers: 0.307, telemetry: 0.941 |
| S01 Static overhead scene | PASS | 0.995 (398/400) | 0 | 1.0 | 0.526 | chained: 0.405, markers: 0.404 |
| S02 Actor crosses left-to-right | n/a | 0.994 (342/344) | 0 | 1.0 | 0.478 | chained: 0.747, markers: 0.747 |
| S03 Actor approaches responder | PASS | 0.936 (412/440) | 4 | 1.0 | 0.061 | telemetry: 0.283, markers: 0.246, chained: 0.247 |
| S04 Actor leaves FOV | PASS | 0.811 (253/312) | 29 | 0.965 | 0.256 | telemetry: 0.6 |
| S05 Actor temporarily occluded | PASS | 0.876 (353/403) | 15 | 1.0 | 0.271 | telemetry: 0.721 |
| S06 Drone camera wobble | PASS | 0.922 (369/400) | 2 | 1.0 | 0.369 | telemetry: 0.656 |
| S07 Gimbal turn | n/a | 0.939 (429/457) | 2 | None | None | telemetry: 0.342, markers: 0.212, chained: 0.187 |
| S08 Low light / blur | n/a | 0.62 (243/392) | 1 | None | None | telemetry: 0.679 |
| S09 Small overhead targets | PASS | 0.504 (997/1979) | 10 | 0.81 | 0.638 | telemetry: 0.544, markers: 0.385, chained: 0.398 |
| S10 Phone local obstacle | PASS | 0.994 (160/161) | 0 | None | None | telemetry: 0.712 |
| S11 Ground floor only | PASS | 0.994 (160/161) | 0 | None | None | telemetry: 0.625 |
| S12 Intermittent depth | n/a | 0.994 (160/161) | 0 | None | None | telemetry: 0.707 |
| S13 Phone frame rotated | PASS | 0.989 (435/440) | 1 | 1.0 | 0.163 | telemetry: 0.76 |
| S14 Unaligned cameras | n/a | 0.862 (243/282) | 12 | 1.0 | 1.671 | telemetry: 0.608 |
| S15 Internet/API loss | n/a | 0.953 (1128/1184) | 10 | 0.996 | 0.344 | chained: 0.339, markers: 0.364, telemetry: 0.959 |
| S16 Decision model vs fixed rules | PASS | 0.992 (258/260) | 0 | None | None | telemetry: 0.778 |
| S17 One source unavailable | PASS | 0.946 (454/480) | 3 | 0.88 | 0.173 | telemetry: 0.652 |
| S20 Dense scene | n/a | 0.942 (2458/2610) | 67 | None | None | telemetry: 0.735 |
| OW1 Walk, then sprint at the responder | PASS | 0.996 (478/480) | 0 | 1.0 | 0.173 | chained: 0.145, markers: 0.149 |
| OW2 Walk behind a tree for 3 s | PASS | 0.876 (353/403) | 15 | 1.0 | 0.271 | telemetry: 0.721 |
| OW3 Exit the frame at 5 m/s (FOLLOW) | n/a | 0.969 (348/359) | 2 | 1.0 | 0.935 | telemetry: 0.975 |
| OW4 Similar people cross (ID-swap trap) | n/a | 0.902 (496/550) | 10 | 0.978 | 0.487 | chained: 0.3, markers: 0.307 |
| OW5 Subject stops, responder approaches | PASS | 0.973 (389/400) | 6 | 1.0 | 0.061 | telemetry: 0.279, markers: 0.248, chained: 0.235 |
| OW6 Gust + frame-drop burst during the sprint | PASS | 0.988 (474/480) | 2 | 0.991 | 0.173 | chained: 0.14, markers: 0.14 |
| OW7 Decision-driven search (3D, MuJoCo X2) | PASS | 0.92 (1048/1139) | 15 | 0.991 | 0.41 | chained: 0.28, markers: 0.267, telemetry: 1.216 |

#### Metric limits

| Scenario | Metric | Value | Limit | Result |
|---|---|---|---|---|
| S09 | `detection.recall_by_size.16-32px.recall` | 0.993 | >= 0.9 | PASS |
| S09 | `detection.recall_by_size.8-16px.recall` | 0.907 | >= 0.75 | PASS |
| S13 | `phone_vision.landscape.range_error_m.p50` | 0.003 | <= 0.25 | PASS |
| S13 | `phone_vision.portrait.range_error_m.p50` | 0.003 | <= 0.25 | PASS |
| S13 | `phone_vision.portrait.range_coverage` | 1.0 | >= 0.6 | PASS |
| S13 | `phone_vision.landscape.bearing_error_deg.p50` | 0.34 | <= 2.0 | PASS |
| S13 | `phone_vision.portrait.bearing_error_deg.p50` | 0.23 | <= 2.0 | PASS |
| S13 | `cross_view.landscape.accuracy` | 1.0 | >= 0.9 | PASS |
| S13 | `cross_view.portrait.accuracy` | 1.0 | >= 0.9 | PASS |
| S13 | `cross_view.portrait.associated` | 26 | >= 5 | PASS |
| S16 | `decision_adjudication.logged` | 6 | >= 6 | PASS |

#### Responder phone: LiDAR range in vision boxes, cross-view association

| Scenario | Orientation | Boxes with GT person | Range coverage | Range err p50 (m) | Bearing err p50 (deg) | Drone-track association accuracy |
|---|---|---|---|---|---|---|
| demo | landscape | 213 | 0.141 | 0.003 | 0.2 | 0.992 (125/126), coverage 0.592 |
| S01 | landscape | 200 | 0.0 | None | 0.24 | 1.0 (93/93), coverage 0.465 |
| S02 | landscape | 162 | 0.012 | 1.938 | 0.32 | 1.0 (50/50), coverage 0.309 |
| S03 | landscape | 220 | 0.668 | 0.003 | 0.33 | 1.0 (144/144), coverage 0.655 |
| S04 | landscape | 129 | 0.0 | None | 0.33 | 1.0 (41/41), coverage 0.318 |
| S05 | landscape | 237 | 0.0 | None | 0.32 | 1.0 (109/109), coverage 0.46 |
| S06 | landscape | 200 | 0.0 | None | 0.175 | 1.0 (118/118), coverage 0.59 |
| S07 | landscape | 240 | 0.0 | None | 0.17 | n/a |
| S08 | landscape | 200 | 0.0 | None | 0.18 | n/a |
| S09 | landscape | 1020 | 0.213 | 0.005 | 0.22 | n/a |
| S13 | landscape | 40 | 1.0 | 0.003 | 0.34 | 1.0 (17/17), coverage 0.425 |
| S13 | portrait | 33 | 1.0 | 0.003 | 0.23 | 1.0 (26/26), coverage 0.788 |
| S14 | landscape | 141 | 0.0 | None | 0.14 | n/a |
| S15 | landscape | 213 | 0.141 | 0.003 | 0.2 | 0.992 (118/119), coverage 0.559 |
| S17 | landscape | 180 | 0.45 | 0.007 | 0.13 | 1.0 (90/90), coverage 0.5 |
| S20 | landscape | 1427 | 0.011 | 0.016 | 0.26 | n/a |
| OW1 | landscape | 180 | 0.578 | 0.003 | 0.32 | 1.0 (156/156), coverage 0.867 |
| OW2 | landscape | 237 | 0.0 | None | 0.32 | 1.0 (109/109), coverage 0.46 |
| OW3 | landscape | 300 | 0.0 | None | 0.11 | 1.0 (58/58), coverage 0.193 |
| OW4 | landscape | 299 | 0.017 | 3.26 | 0.29 | 0.953 (101/106), coverage 0.355 |
| OW5 | landscape | 200 | 0.685 | 0.004 | 0.16 | 1.0 (174/174), coverage 0.87 |
| OW6 | landscape | 180 | 0.578 | 0.003 | 0.32 | 1.0 (151/151), coverage 0.839 |
| OW7 | landscape | 752 | 0.0 | None | 0.34 | 1.0 (145/145), coverage 0.193 |

#### S16: decision engine vs fixed rules (engines: mock - MOCK, not a model)

AI 3/6 · rules 3/6 · announced 3/6 correct · 0 AI/rules disagreements

| Conflict | Candidates | Ground truth | AI | Rules | Announced | Why |
|---|---|---|---|---|---|---|
| C1 | REACQUIRED, POSITION_CALLOUT | REACQUIRED | REACQUIRED | REACQUIRED | REACQUIRED | Same position; the reacquisition is the new information. |
| C2 | POSITION_CALLOUT | no_alert | POSITION_CALLOUT | POSITION_CALLOUT | POSITION_CALLOUT | The responder is handling a close obstacle; a distant stationary subject can wait. |
| C3 | ZONE_ENTRY, POSITION_CALLOUT | POSITION_CALLOUT | ZONE_ENTRY | ZONE_ENTRY | ZONE_ENTRY | An approaching subject at 9 m matters more than a far zone entry. |
| C4 | ZONE_ENTRY, POSITION_CALLOUT | ZONE_ENTRY | ZONE_ENTRY | ZONE_ENTRY | ZONE_ENTRY | Entering the responder's own zone carries the position plus context. |
| C5 | REACQUIRED, POSITION_CALLOUT | POSITION_CALLOUT | REACQUIRED | REACQUIRED | REACQUIRED | The reacquisition report is 3.5 s old; the fresh position supersedes it. |
| C6 | REACQUIRED | REACQUIRED | REACQUIRED | REACQUIRED | REACQUIRED | A single fresh, useful update. |

### Small targets (`make bench-small`)

Simulator frames from S09 (synthetic): the sim detector finds 1.00 of people at 16–32 px, 0.90 at 8–16 px and 0.19
below 8 px; tiling does not change that, because the sim detector already sees full resolution. YOLO26 n/s find
nobody in these cartoon top-down renders, with or without tiles, which says nothing about real footage.

Composite of the two real photos bundled with Ultralytics, shrunk so people are 8–64 px tall on a 1920×1080 canvas
(pseudo-labels from YOLO26s on the originals, IoU ≥ 0.3, 30 frames, CPU):

| Model @ 960 | 8–16 px | 16–32 px | 32–64 px | p50 latency |
|---|---|---|---|---|
| yolo26n | 0.00 | 0.00 | 0.00 | 30 ms |
| yolo26n + tiles | 0.00 | 0.11 | 0.87 | 447 ms |
| yolo26s | 0.00 | 0.03 | 0.34 | 105 ms |
| yolo26s + tiles | 0.02 | 0.53 | 0.96 | 1541 ms |

Tiling is what makes 16–64 px people detectable at all; nothing here finds people under ~16 px.

### Decision engines

OpenAI Decisions (`gpt-6-luna`, live): p50 ~185 ms, designed-case agreement D1 0.8, D2 1.0, D3 1.0, D4 0.67,
D5 1.0; in S16 with the engine in the loop it matches the written answer 6/6 against the fixed rules' 3/6 (the D5
wording was revised once after a first 2/6 run; see docs/decision_engines.md). TypeSafe and Strands are NOT RUN
(no key / no local server). The headless suite below uses the seeded mock so it stays deterministic.

## Known gaps

* No real drone footage has been evaluated; detector accuracy on the Mini 2 feed is unknown.
* The Mini 2 live-view intrinsics are from the spec sheet; calibrate before trusting telemetry projection.
* The iOS apps have not been compiled; see `ios/README.md` for the VERIFY list.
* Cross-view association (phone box ↔ drone track) works from compass bearings (±3° noise in the simulator). It
  makes no claim when the responder's position is GPS-only (S09, S20), when a runner-up candidate is within 3° of the
  best, or when two boxes want the same track, so it misses frames: coverage is reported next to accuracy. It labels
  overlays only and never drives an alert.
