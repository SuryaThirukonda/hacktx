# Operating guide

## 0. Setup (once)

```bash
make setup          # uv venv (Python 3.12) + backend deps, npm install for the dashboard
cp .env.example .env  # add whichever keys you have; everything works without them
make build          # build the dashboard; the backend serves it at http://<mac>:8000/
```

Optional extras: `uv pip install --python backend/.venv/bin/python ultralytics av` (YOLO + H.264 decode),
`rfdetr`, `google-genai`. Put YOLO weights in `backend/weights/` (they download automatically on first use).

## 1. Simulator (no hardware)

```bash
make backend        # SIMULATED mode, scenario "demo" (loops), on :8000
```

Open `http://localhost:8000` (console) and `http://<mac-ip>:8000/responder` on a phone. Sim & faults tab:
load any scenario (S01–S17, S20, OW1–OW6; S18/S19 are integration tests), pause, change speed, inject faults (video
loss, low light, wobble, covered markers, LiDAR dropout, phone held upright, API timeout/429/refusal, internet down,
wind, GPS degradation…).

### 3D decision view

Open `http://localhost:8000/3d` (or the **3D** button in the console's top bar) and press **OW7**. A Skydio X2
rigid-body quadrotor (MuJoCo Menagerie model, the jev-drone setup) follows the subject; each time the subject
disappears under a shelter, decision task D1 picks a bounded search manoeuvre and the X2 flies it for 3 s. The
view shows the true camera footprint, the flight trail coloured by who chose each manoeuvre (following / decision
model / rules / hold), pins where each answer reached the drone, live rotor thrusts, and every D1 answer with its
engine, latency, probabilities and whether it was flown, gated to the rules, or fell back after an engine error.
Cameras: chase, overview (drag to orbit), top. Needs `mujoco` (`pip install mujoco`, in the `sim3d` extra);
without it OW7 flies the kinematic model.

## 2. Live devices

```bash
make backend-live   # OVERWATCH_MODE=LIVE
```

* **Drone video + telemetry** — either run `ios/OverwatchDroneBridge` on the iPhone attached to the DJI RC (it
  connects to `ws://<mac>:8000/ws/bridge`), or point `OVERWATCH_DRONE_URL` at any OpenCV-readable stream (e.g. your
  existing bridge publishing RTSP/RTMP to MediaMTX: `rtsp://127.0.0.1:8554/drone`). Without telemetry the system
  still works from ground markers.
* **Responder phone** — run `ios/OverwatchResponder` (LiDAR iPhone); it connects to `ws://<mac>:8000/ws/phone`.
  The web page `/responder` is a stand-in that only receives alerts (no LiDAR, no iOS haptics).
* **No devices yet?** `make devices` runs `scripts/fake_devices.py`, which plays the simulator through the real
  device protocols (frames are labelled SYNTHETIC).
* **Access token** — set `OVERWATCH_TOKEN` when exposing the Mac beyond a trusted LAN (e.g. Cloudflare Tunnel);
  clients then add `?token=…`.

Live checklist (order matters):

1. Start an **incident** (Ops tab) and tick the consent box — locks are disabled without it.
2. Check the CALIB chip: lay markers (docs/calibration.md) until the holdout error is within your gate.
3. **Alt-click the responder** in the drone view, then have them walk a few meters: registration turns valid.
4. **Shift-click the subject** (or type a description and confirm the D3 proposal).
5. Optional: FOLLOW (simulator or DJI flight simulator only unless `autonomy.allow_real_flight: true`).
   E-STOP latches; any pilot stick input exits virtual stick on the aircraft.

## 3. Tabletop / expo station

`OVERWATCH_CONFIG=config/tabletop.yaml make backend-live` — drone camera on a ~2.5 m stand over a taped 3×3 m
floor area with A4 markers, YOLO26s detector, lunge-scale alert thresholds, autonomy off. Re-measure the marker
positions and edit the YAML.

### High altitude / small targets

Above ~40 m people are only a few pixels tall. Turn on sliced inference in the config:

```yaml
perception:
  tiling: {enabled: true, tile_px: 640, overlap: 0.2, full_frame: true}
```

It runs the detector on overlapping 640 px tiles plus the whole frame (`make bench-small` measures the effect:
on small real-photo people YOLO26s recall rises from 0.34 to 0.96 at 32–64 px). It costs roughly one inference per
tile (≈ 9 for 1080p): about 1.5 s per frame for YOLO26s on this Linux CPU, so on CPU it suits search, not
closed-loop FOLLOW. MPS on an M-series Mac should be much faster but was not measured here.

## 4. Recording and replay

* Record: Ops tab → **Record session** (allowed in simulation, or in LIVE with an active consented incident).
  Output: `sessions/<stamp>_<session>/` (MP4 + JSONL inputs, events, decimated state, operator actions).
* Replay: `make replay SESSION=sessions/<name>` — same pipeline, inputs labelled RECORDED, operator clicks re-applied
  at the same moments, transport controls (play/pause/0.25–2×/seek) in the Sim tab. Replays never command an
  aircraft.

## 5. Decision engines

| Engine | Enable | Notes |
|---|---|---|
| OpenAI Decisions (`gpt-6-luna`) | `OPENAI_API_KEY` | primary by default |
| TypeSafe Jev | `TYPESAFE_API_KEY` | default shadow engine (comparison panel) |
| Strands Decider (local) | `strands-decider serve <model> --port 8099` | offline fallback, text-only |
| mock | automatic in SIMULATED without a key; headless `run_scenarios.py` uses it unless `--engine` is given | clearly labelled `MOCK` everywhere |
| rules | always | deterministic baseline and final fallback |

Switch the primary engine from the AI panel dropdown. Voice: ElevenLabs keys can be given for several accounts
(`ELEVENLABS_API_KEY`, `ELEVENLABS_API_KEY2`, `ELEVENLABS_ACCOUNT` picks the preferred one); a rejected key is
skipped automatically and `/api/health` shows which account and voice are active. Images are never sent to cloud engines unless both
`decisions.allow_images` and `privacy.send_images_to_cloud` are true.

## 6. Evaluation

```bash
make test                       # unit + integration tests, dashboard typecheck
make eval                       # every scenario headless -> evaluation/reports/<stamp>/summary.md
make bench-models SRC=clip.mp4  # detector latency (+ P/R if you pass --labels COCO json)
make bench-small                # small targets: plain vs tiled detectors, recall by target height
make bench-decisions            # engines on designed cases; NOT RUN without keys
cd backend && .venv/bin/python scripts/run_scenarios.py S16 --engine openai   # real engine vs rules, in the loop
```

See docs/evaluation.md for what each metric means.

All synthetic numbers are labelled synthetic: they validate geometry, tracking logic, alerts and fallbacks, not
detector accuracy on real video. Record consented footage and label it before claiming real-world accuracy.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| Console says "Backend not reachable" | `make backend` running? port 8000 free? |
| Drone pane OFFLINE in LIVE | bridge connected (`/api/health` → drone status), or `OVERWATCH_DRONE_URL` reachable |
| CALIB INVALID | fewer than 3 fit markers visible, or holdout error over the gate (Calibration tab shows which) |
| No range/clock readout | responder not marked/registered, subject not locked, track settling, or drone video stale (reason shown) |
| AI PAUSED · RULES ACTIVE | engine timeout/429/refusal or no key — alerts still work |
| `make dashboard` port in use | the dev server uses 5188; production UI is served by the backend on 8000 |
