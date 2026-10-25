# Limitations

What Overwatch does not do, does not know, or has not been tested on. Read this before trusting any output.

## Safety and scope

- **Real flight stays manual.** `autonomy.allow_real_flight` is false. The autonomy stack flies only the simulator.
  Coverage paths and routes are shown to people; nothing sends commands to a real aircraft.
- **No model output reaches flight control.** Model proposals are limited to eight recommendations
  (`decisions/recommendations.py`). Proposals that mention flight terms or carry their own measurements are
  rejected and logged.
- **No facial recognition, no identity beyond "the track an operator designated", and no intent or threat
  labels.** Follow never switches identity on its own; any re-binding is shown as unverified until a person
  confirms it.
- **No detection does not mean the area is clear.** A sector's probability drops only where the camera actually
  looked. (SR3 used to put the person under canopy to show this; since 25 Oct every marked target is in the open,
  so the drone can see, detect and beacon them.)
- **Nothing declares an entry safe or a structure stable.** No software output can say "safe" (FF scenarios check
  this).
- **Near-IR is not thermal.** Near-IR shows reflected light. Enhancement is labelled image processing. No
  temperature is ever reported. Simulated thermal exists only in the simulator and is labelled.

## Not tested on hardware

- **DJI Mini 2:** the iOS bridge (DJI Mobile SDK V4) has not been run against a real aircraft. The Mini 2 source
  reports "not connected" until a real bridge says hello. A test harness is reported as simulated.
- **iPhone and AirPods:** the responder PWA was tested in desktop Chrome at phone size, not on an iPhone. Speech
  needs a tap first on iOS. The geolocation flow was tested with the simulated position.
- **Drone models:** the kinematic Mini 2 stand-in and the MuJoCo X2 have not been fitted to recordings.
  - The DJI simulator step test is skipped: no recordings exist yet.
  - The two models disagree above about 6 m/s. For a 10 m/s step the X2 reaches 7.8 m/s; the kinematic model has
    no tilt or thrust limit.
  - PX4 SITL was not run: no Docker daemon on the development machine.
- **Native iOS LiDAR export** (needs Xcode on a Mac) and **ESP32 haptics** are not built.

## Perception

- The simulator's detector (`sim-blob-v1`) finds saturated clothing colours in synthetic images. It is not a
  person detector for real video. YOLO weights are optional and were not evaluated on real footage here.
- Detection recall in the scenario suite is measured against simulator ground truth only.
- Open-vocabulary door and window detection (OWLv2, Grounding DINO) is not installed. Structure works from manual
  tags plus a simulator fixture that is labelled as a fixture.
- Decoys that look like people to the camera, such as a red jacket or an orange tarp, become candidates for review
  (SR2). That is the intended behaviour, not something the system can avoid.

## Positions and maps

- World positions come from a ground-plane calibration (markers, telemetry or operator ground points). Without a
  valid calibration, positions read "not verified" and nothing is placed on the map.
- The geographic origin of the simulator is a configured reference, not surveyed. A real incident needs a
  surveyed origin (`POST /api/incidents/{id}/georef`).
- Scenes (meshes, point clouds, splats) are only aligned to the incident map through stored transforms. The
  operator-entered alignment in the dashboard is labelled as such. No reconstruction was run here (no GPU); the
  scripts are documented only.
- Ground is flat in the renderer: the search world has no terrain relief.

## Simulation worlds

- The worlds are at plan size (pursuit 400 by 300 m, search and rescue 1.5 by 1.5 km, flood 500 by 500 m, fire
  200 by 200 m), but flat: no terrain relief, no OpenStreetMap import, no glTF assets.
- 24 world scenarios exist (PP0 to PP8, PJ1 and PJ2, CT1 and CT2, SR1 to SR4, FR1 and FR2, FF1 and FF2, and the
  voice scenarios VC1 and VC2). The rest of the plan's 34 are listed as not built in the dashboard.
- The city is a grid of plain boxes with no windows, traffic or people inside; the drone has no collision model, so
  it is kept above the roofs by its altitude floor rather than by avoiding them.
- The phone controller drives a simulated person over a Cloudflare quick tunnel: the tunnel address is public for as
  long as it runs, so set `OVERWATCH_TOKEN` before exposing a server you care about.
- Water, fire, keep-out areas and smoke are drawn in the drone camera image, on the map and in the 3D view, and
  drive crew routing. The 3D water is a flat sheet at a height that follows the level, not a terrain-following
  surface.
- People are jointed figures with clothing, hair, skin and shoe detail, not scanned humans. The simulator's default
  detector (``sim-blob-v1``) segments saturated clothing from the pixels; YOLO26 (tiled) finds 34 of 40 of these
  figures at pursuit altitude and runs at about 200 ms a frame on this CPU, so it is a per-scenario option.
- The low-altitude (jev-drone) stack's range sectors come from the simulator's geometry, a stand-in for a depth
  camera with no noise and no misses. Nothing in it reads where the people are.
- A dispatch matches a person by clothing colour words only (never a face, never a name). The colour names come from
  pixels at 28 m, so "jeans" may read as grey; the operator can reject any match.
- Fire spread, smoke and flood water are screening models with time compressed for the demo (stated in the model
  label and the scenario text), never forecasts.
- Lost-person profile parameters are illustrative simulation values, not published statistics.

## Pursuit, voice and crew routing (simulator)

- **The street belief's motion model is hand-set, not fitted.** Turn probabilities (straight 0.6, left and right
  0.18, reverse 0.04), the stop-and-hide rates and the detection rates in the open and under roofs or trees are
  chosen for the simulator; no real movement or detection data stands behind them. The street graph is the
  simulator's map standing in for OpenStreetMap.
- **Hypotheses come only from people the object memory has seen.** A person never seen cannot become a hypothesis.
  Look-alike similarity is clothing colour (histograms and colour words), never a face.
- **D12 has no report against a real model engine.** `decisions.pursuit_engine: rules` is the default, so every
  headless run uses the rules baseline.
- **PP6 and PP7 search for 45 s instead of the default 20 s** (a per-scenario lock setting), because the covered
  walkway hides the subject for longer and in PP6 the drone checks the look-alike's cloud first (it is in the open,
  so it is the better bet on equal weights). The subject is found about 30 s after the loss there; on a slow machine
  the live run can still time out and leave the choice to the operator, which is the intended fallback.
- **The scripted demo operator rejects a wrong re-binding** (PP5 to PP7) the way a person watching the video would,
  and confirms a right one after 1.2 s. The drone never moves towards an unconfirmed re-binding either way.
- **Voice commands are a keyword grammar over a closed set of verbs.** Speech-to-text is the browser's Web Speech
  API (Chrome sends the audio to its vendor's service); the server only sees text. ORBIT and LOOK are recognised and
  declined in this build. Places resolve only against named map entities (streets, intersections, houses, sectors,
  landmarks); anything ambiguous becomes a question, and nothing is guessed.
- **In the simulator, voice SEARCH and GOTO run without approval by default** (a toggle on the panel asks for it);
  FOLLOW always asks, SEARCH and GOTO ask whenever the aircraft is real, and HOLD and RTL never wait for anyone.
- **Crew routes assume a walking speed of 1.25 m/s on flat ground** and judge water depth and fire arrival from the
  screening models at the time the crew would get there. The fire route runs on the fire model's 2 m grid and stops
  2.5 m outside the door; at FF1's planning time of 28 s no door can be reached before the fire within the margin,
  so FF1 is evaluated with the crew leaving at 20 s.

## Decisions

- Real-engine results are a small paired sample on generated cases (6 seeds per task): agreement with the rules,
  not accuracy against field outcomes.
- The decision engines compared against the rules have no ground truth for D6 to D11 beyond the designed cases.
- When no key is set, the dashboard says "rules only (no API key)". In the simulator a labelled mock engine stands
  in, and its agreement with the rules is by construction.

## Data and privacy

- The incident store is local SQLite. There is no multi-user access control beyond an optional shared token.
- Evidence images are stored on the backend's disk without encryption at rest.
- The Tiger Data mirror is one-way and best effort: SQLite remains the system of record and replays come from it.
  While the service is unreachable the newest 20,000 rows are queued and older ones dropped (the status says how
  many). The rollups are TimescaleDB continuous aggregates with real-time aggregation, so they include rows not yet
  materialised.
