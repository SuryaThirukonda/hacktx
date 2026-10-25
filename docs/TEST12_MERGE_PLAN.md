# Merge plan: test12 into the main platform

City chase becomes test12, and flood rescue and firefighter routing get test12's way of finding someone and calling
it out to the responders.

| | |
|---|---|
| Written | , against `main` at `ef7c29d` ("test12 done"): pushed, working tree clean |
| Status | scope agreed with Surya on ; **not started** |
| Estimate | about 2½ days of work, 3 with the optional hazards merge; about 1¼ days for the city part |
| Companion | [basic_doc2.md](basic_doc2.md): the repo audit taken at the same time |

The plan is at work-package level: what changes, where, and how we know it works. It deliberately stops short of
code-level design.

---

## 1. Decisions

All of these were settled with Surya on . Rows 1 to 7 are the merge decisions; rows 8 to 13 are standing
decisions from earlier that still apply.

| # | Topic | Decision |
|---|---|---|
| 1 | Which map | **City chase becomes test12.** Its scenarios CT1 and CT2 are retired. **Police pursuit stays exactly as it is** (PP0, PP8, PP1 to PP7, PJ1, PJ2, VC1, OW1 to OW7). |
| 2 | How deep | **Plug test12 in as it is.** It keeps its own engine, process and API (`/api/test12/*`); the main page launches and shows it. Its search, YOLO, follow and police logic are not ported into `pipeline.py`. |
| 3 | Views | **The main platform's 3D views are used everywhere, the city included.** test12 feeds the main page's "3D world" tab (chase, top and overview cameras). test12's own rendered third-person and bird's-eye views are removed; its drone-camera video (30 fps, YOLO boxes) stays as the "Drone camera" tab for that map. |
| 4 | Losing the suspect (city) | **One 360° turn, then circle the spot where they vanished for 10 s, then the road search.** The circle is about 20 m out round the last-seen point, camera on that point. If nobody turns up in those 10 s, the drone runs test12's standard search along the roads, and the map keeps highlighting the ground it has already scanned. This replaces the current "circle until found" (see 3.5 for why). |
| 5 | Flood and wildfire target | **The person stays put.** When the drone finds them it **sets a beacon at their position** for the authorities, then **holds over them and keeps them in view until the crew arrives**. |
| 6 | Flood and wildfire responders | **The existing crews** (flood ground crew, fire crew; both already on those maps) ping their positions to the drone; the callouts are relative to them, written and spoken, as in test12. |
| 7 | Uncommitted work elsewhere | "Just perform the merge." Since then the other session's pursuit, belief-routing and voice-command work was committed in `ef7c29d`, so nothing on `main` is uncommitted. The 3D hazards work in the separate worktree is still uncommitted (see 3.5). |
| 8 | Aircraft | DJI only. The simulated aircraft is a DJI Mini 2 stand-in, and the 3D view must draw a DJI-style quad, not the Skydio X2 mesh it uses today. |
| 9 | Voice | The ElevenLabs voice on test12 stays as built (spoken callouts, browser voice as a fallback). |
| 10 | Language models | No Muse. Pursuit stays rules-first; the decisions API is not needed for the demo. |
| 11 | Labels | Everything simulated is labelled SIMULATED; simulator-only views and truth markers say so. |
| 12 | Verification | Headless scenario runs and the live page, not the pytest suite. |
| 13 | Priority | Making the drone pursuit work better is the next step: see section 8. |

---

## 2. What changes for someone using the demo

**City chase (test12).**
- The "City chase" entry on the main page (`/`) now starts test12's 911 dispatch. A crime is reported, the drone takes off from the station pad, searches the streets, finds the person in the red shirt with YOLO26n plus a shirt-colour check, and follows 18 m behind.
- The police car is dispatched at the first report, pings its position every second, and gets spoken callouts relative to itself ("Suspect 45 meters north-east of you, your 2 o'clock …").
- The run goes on until Stop.
- The "Drone camera" tab shows test12's 30 fps camera. The "3D world" tab shows the test12 map in the main 3D view with the drone, the people and the police car.
- You can still pick where the suspect starts on the map.

**Flood rescue and firefighter routing.**
- The drone searches for a described person, finds them and sets a beacon. It then hovers over them with the camera on them, calling out where they are relative to the crew, while the crew walks the safe route the crew-routing service already computes.
- When the crew reaches them, the run reports it.
- The 3D tab shows the water or the fire. `main` already draws a rising water sheet, fire and keep-out areas, smoke
  and the crew route; a richer version waits in the hazards worktree (WP0, optional).

**Police pursuit.** Unchanged.

**`/test12`.** Kept as a standalone developer page unless decided otherwise (open question Q6).

---

## 3. Context: the repo today

### 3.1 Layout

| Path | What |
|---|---|
| `backend/overwatch/` | The platform: FastAPI app, runtime loop, simulator (worlds, scenarios, renderer), perception, tracking, autonomy, the bounded decision layer (D1 to D13), incidents and persistence, voice, integrations. |
| `dashboard/` | React 19 + Vite + Three.js. `/` is the simulation demo; `/dashboard` the full incident console; `/3d` the full-screen 3D view; `/drive` the phone joystick; `/responder` the responder PWA; `/ops` the engineering console. |
| `test12/` | The self-contained police drone dispatch: its own engine, child process, renderer, YOLO pipeline, API and page at `/test12`. Mounted on the backend by `backend/overwatch/main.py` (routes inserted before the dashboard's catch-all). |
| `ios/` | Two Swift apps (responder ARKit/LiDAR app, DJI MSDK bridge); written, not built here (needs Xcode). |
| `config/default.yaml` | All platform settings (video, perception, autonomy, decisions, voice, Tiger Data). |
| `evaluation/` | Scenario and test12 run results. |
| `docs/` | Architecture, plans, operating guide, the  functionality audit, this plan and the  audit. |

Sizes and module counts are in the audit, [basic_doc2.md](basic_doc2.md).

### 3.2 The main platform: what `/` runs

- **Runtime** (`runtime.py`): a 30 Hz simulator step (`SimulationDriver`). The synthetic drone video is 960 × 540 at 15 fps, and the pipeline ticks at 10 Hz with autonomy at 20 Hz. It publishes `WorldState` at 10 Hz and video over `/ws/video/drone`. Scenarios are switched with `rt.control("restart", <scenario id>)` (`POST /api/simulation/control`).
- **Perception**:
  - detector `sim-blob-v1` (HSV blobs) on synthetic frames; YOLO26 is used on real video or when a scenario sets `detector`, which the runtime honours as `p.detector_override`;
  - ByteTracker with appearance histograms;
  - ground projection (marker, chained, telemetry ray);
  - object memory;
  - subject and responder locks (LOCKED → COAST → REACQUIRE → HOLD).
- **Autonomy**:
  - dispatch FSM: TAKEOFF → TRANSIT → lawnmower SEARCH → match by clothing words → TRACK;
  - follow by image-based visual servoing at a 12 m standoff, capped at 4 m/s (6 m/s reacquiring), 45°/s yaw;
  - reacquire primitives and D12 street-graph routes;
  - safety supervisor (geofence, altitude, speed, battery).
- **Decisions**: D1 to D13, rules-first for pursuit (`decisions.pursuit_engine: rules`).
- **Callouts**: position callouts relative to the responder every 8 s while locked (`callout_interval_s: 8`). On `/` they are text only: nothing is spoken by default, and the store's `speak` flag is off. ElevenLabs is wired for `/responder/sensors` through `GET /api/tts`.
- **3D world tab** (`Scene3DPanel` / `dashboard/src/lib/sim3d.ts`):
  - It is fed by `api/sim3d.py`. `GET /api/sim3d/world` and the first `/ws/sim3d` message send the static world (bounds, boxes, trees, markers, zones, geofence, ortho texture URL, drone mesh).
  - `/ws/sim3d` then streams a 20 Hz frame: the true drone pose, actors with their clothing, the camera footprint, subject estimate and last-seen point, the autonomy state, D1 decisions, pursuit belief particles, hazards and crew route.
  - Everything is read from `rt.sim` and `rt.pipeline`, the main runtime only.
  - Cameras: chase (11 m behind, 5 m up), top (85 m) and overview (orbit).
  - The drone is drawn with the Skydio X2 mesh at 2 to 5 times its size, labelled.
  - Since `ef7c29d` (E7 in `IMPLEMENTATION_STATUS.md`) it also draws the hazards: water as a sheet that rises with
    the level, fire and keep-out areas, smoke sprites and the live crew route. It also shows the pursuit belief's
    particles and viewpoints.
- **Worlds and scenarios on the demo page** (`usecases.py`): Training area A (DEMO-1); Police pursuit (19 scenarios); City chase (CT1, CT2); Search and rescue (SR1 to SR4, VC2); Flood rescue (FR1, FR2); Firefighter routing (FF1, FF2).
- **Flood world** (`sim/worlds/flood.py`): 500 × 500 m low-lying neighbourhood.
  - A road graph with elevations (closed above 0.3 m of water) and a bathtub flood model, time-compressed 240×.
  - Staging at (0, 82) with a ground crew: a responder actor at (0, 78) that waits.
  - FR1: three stranded people who wait.
  - `missions/crew_routing.py` plans time-dependent safe routes live, but **moves nobody** ("a route is a proposal for a person").
- **Fire world** (`sim/worlds/fire.py`): 200 × 200 m with a two-storey house (openings on sides A to D), a parked fire engine (`engine-1`), a grass fire spread model, a smoke plume and keep-out zones.
  - Staging at (40, 45) with a crew: a responder actor that waits.
  - FF2 adds an occupant outside.
  - Crew routes avoid keep-out cells. Again, nobody moves.

### 3.3 test12: what `/test12` runs

**Story**:
1. A 911 call reports a red-shirted runner 90 to 250 m from the police station, or at a spot picked on the map.
2. A DJI Mini 2 stand-in climbs from the station pad to 30 m and searches.
3. YOLO26n with a torso-colour check finds them (3 sightings in 2.5 s).
4. The drone follows 18 m behind at 28 m and reports "base +x m E, +y m N" by dead reckoning every 2 s.
5. The police car is dispatched at the first report, pings every second, and gets callouts relative to itself.
6. The suspect flees and hides from both, and the run goes on until Stop (30-minute cap).

**Processes**:
- The web server only relays: it serves the API, plays frames out at an even 30 fps, and relays voice.
- A spawned child process (`live.py`) runs the simulation, YOLO on a thread (5 Hz, every 6th frame, fixed 0.2 s latency) and a pool of 3 render worker processes (`frames.py`) that draw the 30 fps video.
- Runs are deterministic per seed, live and headless (`run_headless.py`).

**Modules**:
- `world.py`: the map, built on `overwatch.sim.world.World`. Pursuit-style streets, a downtown, a garage, a park, carports, awnings, a walkway, shelters, kiosks, trucks, and 71 hiding spots.
- `render.py`: depth-tested people, vehicles and the drone.
- `search.py`: a 1,500-particle belief on the sidewalks, a survey log, and a predictive planner that does not circle.
- `follow.py`: target filter, matcher, follow, 360° scan and search flight.
- `police.py`: the car and its ping.
- `people.py`: the suspect and bystanders.
- `vision.py`: YOLO, the torso check and the light-bar detector.
- `views.py`: the rendered chase and top cameras, to be removed in WP3.
- `server.py` and `web/`: the API and the page.

**API** (all under `/api/test12/`):

| Route | What |
|---|---|
| `POST start` | `{seed, spawn}` |
| `POST stop` | stop the run |
| `GET` / `POST view` | which view the video shows |
| `GET voice` | whether ElevenLabs is available |
| `GET voice/{run}/{n}.mp3` | callout `n`, spoken |
| `GET state` | the page state, 5 Hz |
| `GET world` | the map |
| `GET stream.mjpg` | the 30 fps video |
| `GET frame.jpg` | the latest frame |

Plus `GET /test12` and its assets.

**While a run is going**: the main simulation is paused (`rt.control("pause")`) and resumed afterwards.

**Measured on **:
- **Live**: 30 fps in all three views, 0 dropped frames, YOLO about 50 ms a pass.
- **Headless, 12 seeds × 240 s, "circle until found"** ([evaluation/test12/run-004758](../evaluation/test12/run-004758/summary.md)):
  - all 12 found, 0 wrong matches;
  - the drone holds 18.2 m median behind the suspect (about 21 m at the 90th percentile);
  - reports 0.6 m off on average; callouts relative to the car 1.3 m on average, up to 11 m when the car is fast between pings.

### 3.4 How the two differ

| | Main platform (`/`) | test12 |
|---|---|---|
| Video | 960 × 540, 15 fps, base renderer | 1280 × 720, 30 fps, depth-tested renderer, worker pool |
| Person detection | colour blobs on synthetic frames (YOLO optional per scenario) | pretrained YOLO26n at 5 Hz + torso colour |
| Search | lawnmower legs over a radius; D12 street routes when lost | belief over sidewalks, predictive planner, survey log |
| Follow | 12 m standoff, ≤ 4 m/s | 18 m behind at 28 m, ≤ 15 m/s |
| Lost | coast → yaw scans, orbit, climb → hold for the operator after 20 s | 360° (to be: 360°, orbit 10 s, road search) |
| Responder | person on foot; callouts every 8 s, text only on `/` | police car with 1 Hz pings; callouts every 2 s, spoken |
| 3D | Three.js tab over `/ws/sim3d` | none (rendered chase and top views, to be replaced) |
| State and logs | incidents (SQLite), Tiger Data mirror, decision log | its own page state and events; nothing persisted |

### 3.5 Work in flight that touches this merge

- **Richer 3D flood and fire visuals: uncommitted, in another worktree.**
  - Where: `/home/surya/repos/txhack_hazards_3d`, branch `feat/fire-water-3d` at `a945754`. It is behind `main` by the commits after `a945754`.
  - Size: 11 files, +1,109 / −86 lines. An animated water heightfield, closed-road barricades, instanced flames, a smoke plume and crew-route tubes in `sim3d.ts`, `Sim3D.tsx`, `Scene3DPanel.tsx`, `api/sim3d.py`, `sim/driver.py`, `sim/hazards/layers.py`, the flood, fire and sar worlds, and two test files.
  - Overlap: `main` has changed six of the same files since (`sim3d.ts` +231, `api/sim3d.py` +43, `driver.py` +25, `fire.py`, `flood.py`, `Scene3DPanel.tsx`), including its own simpler hazard drawing.
  - `main`'s simpler version is enough for the flood and wildfire work, so bringing the worktree over is optional polish (WP0).
- **Skydio removal, phase B: not done, now unblocked.** Still present:
  - `backend/third_party/skydio_x2/`, `sim/mujoco_drone.py`, the jev-x2 low-altitude stack;
  - scenarios OW7, PJ1 and PJ2;
  - the X2 mesh in `api/sim3d.py` and `sim3d.ts`, and doc mentions.

  It waited on uncommitted work that is now committed. The 3D view's drone must become a DJI-style quad for this merge either way (WP0).
- **Why the loss behaviour changes (decision 4).**
  - **Circle until found** (the current setting), 12 seeds × 4 min: the drone was following the suspect a median 34% of the time. Only 25 of 33 losses were found again, and 3 of 12 runs never found the suspect after the first loss.
  - **One 360° then the cover-first search** (an earlier batch): the drone was following 61% of the time and found 58 of 63 losses again. That batch's suspect also hid twice as often.
  - The circle only sees about 46 m around one spot.

---

## 4. Work packages

Each package lists its goal, what changes, how we will know it works, and its estimate. File lists are where the work
lands, not a code design.

### WP0: prerequisites (about 2 h; ½ day more with the optional hazards merge)

1. **A DJI-style quad in the 3D view (required).** Replace the X2 mesh (`api/sim3d.py` `drone_mesh`, `sim3d.ts` loader) with a procedural DJI Mini 2 shape, labelled with its true size and the drawn scale. This is the 3D-view part of Skydio phase B. The rest of phase B can follow separately.
2. **The richer 3D hazard visuals (optional).**
   - Commit the worktree's changes on `feat/fire-water-3d` (with Surya's OK: it is another session's work), merge it into `main`, and resolve the conflicts in the six shared files. Keep the better of the two hazard drawings.
   - Done when the 3D tab on `/` shows animated flood water and closed roads in FR1 and FR2, and flames, a smoke plume and keep-out zones in FF1 and FF2.
   - Do this before WP3 and WP4 if at all, since it touches the same 3D files.

### WP1: test12 loss behaviour (about 2 h)

- **Goal**: decision 4. Lost (out of sight 3.5 s):
  1. one 360° at the last-seen spot;
  2. **orbit** about 20 m out round that spot, camera on it, for 10 s;
  3. if still nobody, the road search: test12's belief planner, re-seeded from the last sighting, flying the streets;
  4. scanned ground stays highlighted on the map, as the survey layer does now.
- **Where**: `test12/sim.py` (phases: SCAN → ORBIT → SEARCH after a loss), `test12/follow.py` (an orbit command), `test12/web/app.js` (step labels, orbit ring), README.
- **Done when**: headless 12 seeds × 240 s shows no run that never finds the suspect again, and following time is back near the cover-search figure (about 60%). Live, the page shows "360°" → "orbit" → "search" with the scanned ground highlighted.

### WP2: City chase launches test12 (about ½ day)

- **Goal**: decisions 1 and 2.
- **Where**:
  - `backend/overwatch/usecases.py` and `sim/scenarios.py`: retire CT1 and CT2. The City chase entry becomes a test12 launcher (an id such as `T12`; it is not a main-runtime scenario).
  - `dashboard/src/pages/Demo.tsx` and a small test12 client module, the main part of the work:
    - starting City chase calls `POST /api/test12/start`, passing a spawn point picked on the map;
    - the "Drone camera" tab shows `/api/test12/stream.mjpg`;
    - the right-hand panels show test12's state: the steps, the map with belief, survey, police, ping and callout line, the latest callout and the summary;
    - the voice toggle and player move over from `test12/web/app.js`;
    - Stop stops test12.
  - Leaving City chase stops test12 and resumes the main simulation. Pausing and resuming already happen in `test12/server.py`.
  - `backend/overwatch/sim/worlds/city.py`: kept or deleted (open question Q5).
- **Done when**, on `/`:
  - City chase runs the whole test12 story;
  - picking another scenario cleanly stops test12 and resumes the main simulation;
  - Police pursuit scenarios behave as before.

### WP3: test12 in the 3D tab (about ½ day)

- **Goal**: decision 3.
- **Where**:
  - `test12/server.py` and `live.py`: a `/ws/test12/sim3d` stream in the same message format as `api/sim3d.py`.
    - World: bounds, boxes, trees, the ortho texture served by test12 from its ground texture.
    - 20 Hz frames: drone pose and gimbal, actors with their clothing, the footprint.
    - Additions the 3D view does not have yet: the **police car** (pose, light bar), test12's belief particles and survey grid, the scan or orbit ring, the latest callout line, and the ping position.
  - `dashboard/src/lib/sim3d.ts` and `Scene3DPanel.tsx`: choose the stream by the active map, and draw the police car and the test12 overlays.
  - `test12/views.py`, the inset code in `frames.py` and `hud.py`, and the view switcher on `/test12`: removed.
- **Done when**: on `/`, the 3D tab during City chase shows the test12 map with the drone (DJI quad), suspect, bystanders and police car moving as in the video, at 20 Hz, with the chase, top and overview cameras working. The 30 fps drone camera still runs at 30 fps while the 3D tab is open.

### WP4: flood rescue (about ½ to ¾ day)

- **Goal**: decisions 5 and 6 on the main engine.
- **Flow**:
  1. Dispatch from the staging pad with a description (e.g. FR1's "mustard" person). This uses the existing dispatch FSM: TAKEOFF → TRANSIT → SEARCH → match.
  2. On a match: **beacon**, an event with the person's position (base-relative and lat/lon from the simulator's geo-reference) to the crews and the incident log, with a pin on the map and the 3D view.
  3. **Hold**: hover over the person, camera on them. The lock keeps them in view. If smoke or debris hides them, the beacon stays and the drone stays.
  4. **Crew moves**: the crew walks the live safe route from `crew_routing` to the beacon. This is new; today routes move nobody.
  5. **Crew pings and callouts**: the crew's position every second, and callouts relative to the crew. On `/` they are spoken: ElevenLabs through `/api/tts` or test12's voice endpoint pattern.
  6. **Arrival**: the crew within about 10 m of the person ends the hold with "crew on scene" (what the drone does next is open question Q3).
- **Where**:
  - `backend/overwatch/pipeline.py` and `autonomy/controller.py`: the hold-over mode after a match;
  - `sim/worlds/flood.py`: the scenario, the crew's walk and the beacon evaluation;
  - `missions/crew_routing.py`: hand a route to the crew actor;
  - `events/`: beacon and crew callouts;
  - `api/sim3d.py` and `sim3d.ts`: beacon pin, crew route, crew moving;
  - `Demo.tsx`: the voice player.
- **Done when**: FR1-style runs find the described person, set a beacon within 2 m of their true position, hold them in view until the crew arrives, and the crew's route never crosses water above 0.3 m. Checked headless (scenario metrics) and live (3D tab with water).

### WP5: firefighter routing (about ½ day, after WP4)

- **Goal**: the same flow as WP4 on the fire world.
  - The FF2 occupant is the person.
  - The crew walks the keep-out-safe route.
  - Smoke may hide the person, so the beacon holds.
- **Where**: `sim/worlds/fire.py`, plus whatever WP4 made generic.
- **Done when**: the occupant is found and beaconed, the crew's route crosses no keep-out cell, and the hold lasts until the crew arrives. Checked headless and live (3D tab with fire and smoke).

### WP6: verification and docs (about ½ day)

- Headless: test12 12 seeds × 240 s, plus FR1, FR2, FF1, FF2 and the Police pursuit batch (PP0, PP8, SR4) to show they did not regress.
- Live on `/`: City chase (video and 3D at the same time, voice), Flood rescue, Firefighter routing, and one Police pursuit scenario.
- Rebuild `dashboard/dist` (`npm run build`) so the backend and the tunnel serve the merged page.
- Update `README.md`, `DEMO_RUNBOOK.md`, `IMPLEMENTATION_STATUS.md`, `test12/README.md` and the audit.

---

## 5. Estimate

| Package | Estimate |
|---|---|
| WP0 prerequisites (DJI quad; optional: hazards worktree) | ~2 h (+½ day optional) |
| WP1 test12 loss behaviour | ~2 h |
| WP2 City chase launches test12 | ~½ day |
| WP3 test12 in the 3D tab | ~½ day |
| WP4 flood rescue | ~½–¾ day |
| WP5 firefighter routing | ~½ day |
| WP6 verification and docs | ~½ day |
| **Total** | **about 3 days** with the optional hazards merge, about 2½ without (city part, WP1 to WP3: about 1¼ days) |

These are working-time estimates for this assistant, assuming no new requirements. The uncertain parts are the
optional hazards merge (conflicts in the 3D files) and WP4 (the crew walking a time-dependent route, and the
hold-over mode in a 1,900-line pipeline).

---

## 6. Risks

1. **Two engines side by side.** City chase runs test12's process while the main runtime is paused. Switching between them must always stop one and resume the other, or the CPU is shared and the video drops below 30 fps.
2. **The optional hazards merge** (WP0) touches the same 3D files as WP3 and WP4. Decide on it first, or the 3D
   work is done twice.
3. **The crew must actually move** (WP4 and WP5). With the flood's 240× time compression and a crew on foot, the wait for the crew can be long for a demo. Crew speed is open question Q2.
4. **Detection on flood and fire.** The main platform finds people with colour blobs on synthetic frames; test12 uses YOLO. Keeping blobs is honest only with the label it has today ("synthetic stand-in"). Switching those scenarios to YOLO costs CPU at 15 fps (open question Q1).
5. **ElevenLabs characters.** test12 uses about 700 characters a minute per listening page with voice on. Spoken crew callouts on flood and fire add to that.
6. **Callout accuracy at speed.** The 1 Hz police ping makes callouts up to about 11 m off while the car is fast. That is fine for the city; crews on foot are slow, so it does not matter for flood and fire.
7. **The backend serves a stale dashboard build.** `dashboard/dist` dates from  at 17:47 (audit 4.9). Unless it is rebuilt, the merged demo page appears only on the Vite dev server, not on :8000, :8012 or the tunnel. WP6 rebuilds it.
8. **Exposure through the tunnel.** `GET /api/tts` and test12's API have no auth (audit 6.7). The merge adds more spoken callouts, so close this before demoing over a public tunnel.

---

## 7. Open questions

Each has a suggested answer, used if nobody says otherwise.

| # | Question | Suggested |
|---|---|---|
| Q1 | Flood and fire: find the person with YOLO (like test12) or the existing colour-blob detector? | YOLO26n at 5 Hz in those scenarios, for the same honesty as the city. |
| Q2 | How fast does the crew travel to the beacon? | On foot, compressed like the flood model, so a crew arrives in 30 to 60 s of demo time. |
| Q3 | After "crew on scene", what does the drone do? | Hold 10 s, report, then return to the pad. |
| Q4 | What is a "beacon"? A Mini 2 cannot drop anything. | A shared position marker: incident event, map pin, 3D marker and a callout. No physical drop. |
| Q5 | Delete the old city world (`sim/worlds/city.py`, CT1, CT2) or keep it unlisted? | Keep the code unlisted until the merge is verified, then delete. |
| Q6 | Keep `/test12` as its own page after the merge? | Yes, as a developer page. |
| Q7 | The phone joystick (`/drive`) steered the CT1 target. Should it steer test12's suspect? | Not in this merge; possible later. |

---

## 8. Not in this merge: the next step

**Make the drone pursuit work better.** This is the stated next priority. The audit ([basic_doc2.md](basic_doc2.md)) lists the evidence and where to start:

- **test12:**
  - following time after a match: 34% median with circle-until-found;
  - 3 of 12 runs lose the suspect for good;
  - re-finding takes about 22 s median (7 to 158 s);
  - WP1 is the first step.
- **Police pursuit on the main platform:**
  - IBVS follow capped at 4 m/s at a 12 m standoff;
  - the belief routing committed in `ef7c29d` (E7) finds the subject again 19 to 31 s after the loss in PP5 to PP7, headless;
  - live runs on a loaded machine fell behind real time and some chased the look-alike (E7's own notes).

---

## Appendix: the agreed answers, as given

From the questions on :

- Which main map should test12 become? **City chase becomes test12.**
- How deep? **Plug test12 in as-is.**
- Flood and wildfire, which pieces? All four:
  - search for a described person;
  - follow + 360 when lost;
  - responder pings + callouts;
  - third-person + bird's-eye.

  In Surya's words: "use the third person and birds eye view from the original platform, those are better. also for 360, it should do 1 360, and then patrol the area that the person went under, and if it doesnt find anyone for 10 seconds, then it does a standard search, where it goes through the map, and scans for the person through roads. it highlights on the map where it has already scanned before."
- Other session's uncommitted work? "just perform the merge."
- Patrol shape? **Orbit where they vanished.**
- Does the flood or wildfire person move? "they should stay put, and it sends a beacon to where the person is so the authorities can find them. it then waits for authorities to come, and visually stays with the person."
- Who gets the pings and callouts? **The existing crews.**
- Should the city use the platform's 3D views too? **Yes.**
