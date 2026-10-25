# Repo status (merge audit) (): audit of the GPT merge and redesign What the GPT session between and on changed, what it claimed, what holds up in the code, and
where the whole repo stands now. | | |
|---|---|
| Repository | `txhack_init_claude` (Overwatch), branch `main` |
| HEAD | `1cbfec8` "Merge teammate live bridge and movement controls with current autonomy", **5 commits ahead of `origin/main`, not pushed** |
| Working tree | **not committed**: 46 modified files, 1 deleted (`test12/views.py`), 13 untracked paths; +1,434 / −1,095 lines in tracked files plus about 3,700 lines of new files. This is all of the test12 integration, the rescue flow and the dashboard redesign. |
| Other worktree | `/home/surya/repos/txhack_hazards_3d` (`feat/fire-water-3d` at `9b34d6d`): already merged into `main`; clean apart from three dependency symlinks |
| Other branches | `origin/drone-v1`: fully merged into `main` (`1cbfec8`) |
| Method | Read every diff and new file; static checks (Ruff, `py_compile`, `tsc --noEmit`, a Vite build into a scratch folder); read-only API probes against the merge's own server on :8030; re-ran test12 seeds 0 to 2 and PP0 on the current tree (section 4.3; the rescue re-runs were stopped before finishing). **No pytest**, per the standing rule. No code was changed for this audit. |
| Earlier audits | [basic_doc2.md](basic_doc2.md) (, before the merge), [TEST12_MERGE_PLAN.md](TEST12_MERGE_PLAN.md) (the plan this work followed), [MERGE_AND_DASHBOARD_REPORT.md](MERGE_AND_DASHBOARD_REPORT.md) (the GPT session's own report) | --- ## 1. In short - **Most of the plan is built.** City chase now launches test12 from the main page; test12's loss behaviour is 360° → 10 s orbit → road search; test12 feeds the shared 3D view; the 3D drone is a DJI-style quad; flood and fire have a beacon → hold → moving crew → callouts → return flow; and `/` is a new scenario picker with a survey map, a callouts tab and diagnostics below the fold. The two git merges (hazards, teammate) are committed.
- **None of the integration or redesign is committed.** It exists only in this working tree. The GPT report reads as if it were committed; it is not.
- **The four flood and fire scenarios were overwritten, not extended.** FR1, FR2, FF1 and FF2 now all run the same rescue, so FR1 = FR2 and FF1 = FF2 in practice. Their original checks (route alternatives, closed roads, door routing, smoke-obscured openings, D10/D11) no longer run anywhere.
- **The rescue numbers are easier than they look.** The dispatch report is the casualty's exact position, only seed 0 was run, and the scenario's `unsafe_steps` metric can never be anything but 0.
- **The redesign removed several demo controls from every page**: dispatch from the pad, Wrong person, driving people, the phone tunnel and QR code, and the Tiger Data panel. `/dashboard` is now the picker, so the live-drone docs that say "open `/dashboard`" point at the wrong page (it is `/command` now).
- **Two servers are still running**: the merge's verification server on :8030 (~190% CPU) and your pre-merge server on :8012 (~130% CPU), which serves the new dashboard build against old Python code. --- ## 2. What the session did, in order From the commits, file times and evidence files in `evaluation/merge/`. | Time | What |
|---|---|
| | `9b34d6d`: committed the hazards worktree's richer flood and fire 3D visuals on `feat/fire-water-3d` |
| | `f14030f`: merged `feat/fire-water-3d` into `main`, keeping `main`'s pursuit, belief and story overlays |
| | `1cbfec8`: merged `origin/drone-v1` (DJI live bridge, keyboard and visual-follow control, person marking, Swift changes). `start_goto` now sets the mode before storing the route, because The teammate's `set_mode` clears the route. |
| to | test12 headless batch, 12 seeds × 240 s (`evaluation/merge/test12-batch`) |
| to | City launcher (`usecases.py`, `scenarios.py`), autonomy HOLD for rescue, `missions/rescue.py`, test12 orbit/search, `test12/sim3d.py`, survey and visibility modules |
| to | Platform regression runs PP0, PP5 to PP8, SR4 (`evaluation/merge/platform`) |
| | The design reference `Overwatch Pursuit Dashboard.html` arrives (untracked, repo root) |
| to | Rescue runs FR1, FR2, FF2, FF1 (`evaluation/merge/rescue`) |
| | Fire spread slowed from 0.9 to 0.45 m/s for every fire scenario |
| | test12's rendered third-person and bird's-eye views removed |
| to | Dashboard redesign (`Demo.tsx` rewritten, `SimulationDashboard.css`, `SurveyMap.tsx`, navy theme, photos) |
| | Verification server started on :8030 (still running) |
| | Last edits to `pipeline.py`, `runtime.py`, `survey.py`, `visibility.py` |
| | `dashboard/dist` rebuilt (it matches the current source; checked in section 4) | --- ## 3. The GPT report's claims, checked | Claim | Verdict | Detail |
|---|---|---|
| Hazards and teammate branches merged | **True** | `f14030f`, `1cbfec8`. Both branches are fully contained in `main`. |
| "Commit the completed integration and redesign" (its last step) | **Not done** | Everything after `1cbfec8` is uncommitted (header). |
| CT1/CT2 retired; 48 main-engine scenarios plus T12; 30 picker choices | **True** | Counted from the registry and `/api/usecases`. `sim/worlds/city.py` is kept unlisted (plan Q5). |
| City runs test12's own process; stop waits for the child; restarts stop test12 | **True** | `test12/server.py` `Manager.stop()` waits up to 15 s for the child's `end`; `Runtime.control("restart")` calls it. See 5.12 for the failure path. |
| 360° → 20 m orbit for 10 s → road search | **True** | `test12/sim.py` phases SCAN → ORBIT → SEARCH; `follow.orbit_command`. |
| test12 rendered chase/top views removed; shared 3D used | **True** | `views.py` deleted; `/ws/test12/sim3d` at 20 Hz; `Scene3DPanel` takes a `stream` prop. |
| DJI Mini 2 stand-in in 3D | **True, with a nit** | Procedural quad in `sim3d.ts`. It is still drawn at 2× (chase) and 5× (top, overview) while its label says "0.29 m span"; the plan asked for the drawn scale to be labelled too. |
| Rescue: beacon from perception, hold, crew on a hazard-checked route, callouts, 1 s pings, hold 10 s, return | **True** | `missions/rescue.py`. The beacon is the subject lock's Kalman position, not the actor's. |
| Beacon error 1.38 m (flood) / 1.02 m (fire); crew arrived 53.5 s / 51.6 s | **True for seed 0**, but see 5.2 to 5.4 | FR1 and FR2 produced identical files, as did FF1 and FF2. |
| "Zero unsafe moving steps" | **True as measured by the verifier**, vacuous in the scenario | `verify_rescue.py` re-checks each crew position independently, which is meaningful. The scenario's own `world.unsafe_steps` limit reads a counter nothing increments (5.4). |
| City median following 72.1%, 60 losses / 54 re-finds, 6 runs ended lost | **True** | Re-computed from `summary.md`; seeds 0 to 2 reproduce exactly on the current tree (section 4.3). |
| PP0, PP8, PP5 to PP7, SR4 meet their criteria | **Partly confirmed** | Run to , before the survey, rescue gating and detector-interval changes. PP0 reproduces exactly on the current tree; the others were not re-run. |
| `/api/locks/describe` reachable | **True** | Registered before `/api/locks/{role}`. |
| TTS, test12 API/socket and rescue voice enforce a configured token | **True** | Only when `OVERWATCH_TOKEN` is set; it is still unset by default. 6 of 10 WebSockets still skip the check (5.11). |
| "Phone-drive QR credentials use a fragment" | **Moot** | `api.ts` now reads a `#token=` fragment, but the redesign deleted the tunnel/QR panel, so nothing generates that link any more (5.6). |
| Production build passes; Python compiles; Ruff undefined-name checks pass | **True** | Re-run in section 4. |
| "No pytest run" | **True** | `tests/unit/test_worlds.py` was edited to match the new registry but not run. | --- ## 4. Checks made for this audit ### 4.1 Static | Check | Result |
|---|---|
| Ruff `F` and `E9` (undefined names, unused imports, syntax) on every changed and new Python file | pass |
| `py_compile` on the same files | pass |
| Full Ruff ruleset on the four new Python files | 23 style findings (mostly `dict(...)` calls and import order); none are correctness issues |
| `tsc --noEmit -p dashboard/tsconfig.json` | pass (`tsconfig.tsbuildinfo` left untouched) |
| `vite build` into a scratch folder | pass; asset names identical to `dashboard/dist`, so the served build matches the current source |
| Main bundle size | `index-*.js` 881 kB (258 kB gzip); Vite warns about chunk size, as before | ### 4.2 Read-only probes of the merged server (:8030) - `GET /api/usecases`: City = `[T12]`, Flood = `[FR1, FR2]`, Fire = `[FF1, FF2]`. The same call on your :8012 server still returns `[CT1, CT2]`, because that process was started at , before the merge.
- OpenAPI lists 146 HTTP operations on 133 paths (the audit counted 117 + 12 test12 routes before the teammate merge), plus 10 WebSockets.
- `/ws/sim3d` during FF2: 97 frames in 5 s, **18.9 kB per frame, of which 13.5 kB is the survey grid**, so about 360 kB/s per socket (5.8).
- `/api/health` during FF2: the drone source was running at **2.7 fps** with tiled YOLO at 1280 px on CPU, on a machine also carrying the :8012 server (5.9). ### 4.3 Headless re-runs on the current tree Runs are deterministic per seed, so if the code the merge measured is the code in the tree, these reproduce its
numbers exactly. All output went to a scratch folder; nothing in `evaluation/` was touched. | Re-run | Result |
|---|---|
| test12, seeds 0 to 2 × 240 s | **Identical** to rows 0 to 2 of `evaluation/merge/test12-batch/summary.md` (following 75.1 / 70.4 / 73.8%, the same losses, re-finds, errors and police times). The batch reflects the test12 code in the tree, despite the later edits to `sim.py`, `follow.py` and `los.py`. |
| PP0 | **Identical** to `evaluation/merge/platform/PP0.json` apart from run time; expected pass. |
| FR1, FF1 (rescue) | **Not completed.** Stopped on request before they finished. The rescue evidence still predates the last edits to `pipeline.py` () and is unconfirmed on the current tree. |
| PP5 to PP8, SR4, seeds 3 to 11 | Not re-run | --- ## 5. Findings Ranked by how much they matter for the demo and for trusting the numbers. ### 5.1 Nothing after `1cbfec8` is committed (high) The integration, the rescue flow, the redesign, the evidence in `evaluation/merge/` and the GPT report all exist
only in this working tree, and the 5 local commits are not pushed. One `git checkout .` or a bad stash loses the
session. Before committing, decide 5.2 and 5.6, since they change what the commit should contain. Also in the tree:
the regenerated `dashboard/tsconfig.tsbuildinfo` (a committed build file) and the 496 kB design reference
`Overwatch Pursuit Dashboard.html` at the repo root (commit it under `docs/` or leave it out). ### 5.2 Flood and fire scenarios were overwritten (high) `sim/worlds/flood.py` and `sim/worlds/fire.py` end with a loop that replaces, for every scenario in the file, the
description, limits, world evaluation, duration (160 s), start (on the staging pad), detector (YOLO) and adds a
`rescue` spec. - **FR1 and FR2 are now the same run** (FR2 just has fewer bystanders), and so are **FF1 and FF2** (FF1 gained the FF2 occupant). The evidence files confirm it: FR1.json and FR2.json are identical apart from the scenario id and run time, and so are FF1.json and FF2.json.
- **Their old checks no longer run anywhere**: stranded-found fraction and false tracks (FR1), route exists / max water depth / live matches offline (FR2), crew door route outside keep-out and live matches offline (FF1), unclear openings under smoke and occupant found (FF2), and the D10 dispatch and D11 opening proposals inside them. The two `_eval` functions are now dead code.
- **Names no longer match behaviour.** FR2 is still "Flood: route the crew around closed roads"; FF1 is still "Fire: route a crew to a door outside the keep-out zone".
- **The fire got easier for everyone.** Spread was halved (0.9 → 0.45 m/s) for all fire scenarios, because at the original rate no safe route existed by the time the drone found the casualty. That is disclosed in the report, but it also changes the 3D hazard previews and the old FF stories.
- **The flood casualty moved out of the water** to a dry refuge at (−48, 12), so FR1's "stranded people in rising water" story now has its subject on dry ground.
- While a rescue runs, `pipeline.py` skips `crew.tick()`, so the live crew-routing service and its D9 proposals are off in every flood and fire scenario. The plan asked for rescue to be *added* to flood and fire. Restoring the originals and adding the rescue as new ids
(for example FR3 and FF3) would keep both. ### 5.3 The rescue search is told exactly where to look (high, for honesty of the numbers) `rescue["last_seen_xy"]` is the casualty's exact start position in both worlds ((−48, 12) and (4.0, 8.5)). The drone
first flies to a point 18 m north of it and looks, so the "search" is a fly-to-and-look, not a search. The code
comment says the report, not truth, picks the viewpoint, which is true only because the report was set equal to
truth. Combined with one seed per world, the 1.0 to 1.4 m beacon errors show the projection works, not that the
drone can find someone. A report offset by 20 to 40 m and a few seeds would test it. ### 5.4 `world.unsafe_steps` can never fail (medium) `RescueMission.unsafe_steps` is set to 0 in `reset()` and never incremented; the movement code stops the crew
*before* an unsafe step instead. The scenario limit `world.unsafe_steps max 0` therefore always passes. The safety
is real (each step is checked before it is taken, at 0.07 to 0.13 m per step), and `verify_rescue.py` re-checks it
independently, but the metric should count the refused steps or be renamed. ### 5.5 Crew speed and timing (low) The flood crew moves at 4 m/s (a jog, 14 km/h) and the fire crew at 2 m/s, in uncompressed simulation time. The
plan's default (Q2) was a walk compressed like the flood model. Fine for a demo, but say so if the arrival times are
quoted. ### 5.6 The redesign removed controls from every page (high, if you still want them) The old `/` had panels that no page has now. The API behind each is still there. | Removed from the UI | Still reachable |
|---|---|
| Dispatch from the launchpad (clothing words, last-seen point, radius), Cancel, Wrong person | API only (`/api/autonomy/dispatch`, `/cancel`); `actions.dispatch` in `incident.ts` is now unused |
| Drive people (send to a point, stop, resume, arrow keys) | `/drive` page and API; `actions.person` unused |
| Drive the target from a phone (open tunnel, QR code) | API only (`/api/tunnel/start`); the `qrcode` package is now unused |
| Tiger Data live metrics | API only (`/api/tiger`) |
| Route-reasoning card, voice-command panel | Only on `/command` (right rail) |
| Story captions over the video | Shown only as a line in the Callouts tab, and hidden for rescue runs | You asked for a simpler landing page, so some of this is intended; but nothing moved to `/command`, so the
pursuit dispatch and the phone-chase demo are no longer usable from the browser. ### 5.7 `/dashboard` changed meaning (high, for the live drone) `main.tsx` now sends `/` and `/dashboard` to the picker and `/command` to incident command. The teammate's live-drone
instructions all say to open `/dashboard` for the bridge controls ("Enable aircraft controls", "Who to follow",
"Control relay connected"): `basic_doc1.md`, `docs/RUN_ON_THIS_MAC.md`, `docs/SDK_WEBSITE_CONTROL_FIX.md`,
`docs/CAMERA_FOLLOW_TEST.md`, `docs/DRONE_LIVE_INTEGRATION.md`. In LIVE mode the picker only shows a link to
`/command`. Either update those docs or keep `/dashboard` pointing at incident command. ### 5.8 3D and map traffic (medium, for the tunnel) `api/sim3d.py` puts the whole survey grid (base64) into every 20 Hz frame, although it changes at most twice a
second. Measured on FF2 (a 200 m world): 13.5 of 18.9 kB per frame, ~360 kB/s per socket. By the grid sizes in
`sim/survey.py`, the flood and SAR grids encode to about 37 kB, nearly three times that. The new page also opens two `/ws/sim3d` sockets while the 3D tab is open (`Scene3DPanel` and
`useSimulationFeed`, which throttles to 4 Hz on the client but still receives 20 Hz). Over a Cloudflare tunnel this
is a few MB/s. Sending the grid only when it changes would remove most of it. ### 5.9 Rescue video rate (medium, for the live demo) Rescue scenarios switch the main pipeline to tiled YOLO at 1280 px, every 200 ms (`detector_interval_ms`). On this
machine, with two servers running, the drone source on :8030 was at 2.7 fps. The plan's risk 4 called this out. Also
from reading (not seen on screen): frames between detections are sent with `tracks=[]`, so boxes in Drone POV are
likely to blink at the detection rate. ### 5.10 Servers left running (medium, today) | Port | Process | State |
|---|---|---|
| :8012 | `overwatch.main:app`, started | Old Python code behind the **new** `dashboard/dist`: City lists CT1/CT2 instead of T12, `/ws/test12/sim3d` does not exist, and flood and fire run the old scenarios without rescue. Restart it to get the merge. ~130% CPU. |
| :8030 | `/tmp/overwatch_merge_server.py`, the merge's verification server, started | Merged code from before the edits, isolated `/tmp` database, no token. ~190% CPU. Safe to stop. | ### 5.11 Access control (medium, before any tunnel) - `OVERWATCH_TOKEN` is still unset by default, so everything is open on the dev servers.
- When set, the new checks cover `/api/tts`, `/api/test12/*`, `/ws/test12/sim3d` and `/api/rescue/voice/*`. Still unchecked: `/ws/state`, `/ws/events`, `/ws/alerts`, `/ws/video/{src}`, `/ws/sim3d`, `/ws/incidents/{iid}` (6 of 10 sockets), and all GET routes as before.
- The token travels as `?token=` on images, audio and sockets, so it lands in access logs and tunnel logs.
- `/api/rescue/voice` and test12 voice spend ElevenLabs characters whenever someone turns Voice on. ### 5.12 Smaller defects (low) - **test12 stop failure path.** If the child does not acknowledge Stop within 15 s, `Manager.stop()` raises `RuntimeError`; neither `/api/test12/stop` nor `/api/simulation/control` catches it, so the user gets an HTTP 500 and the main simulation stays paused. The 15 s wait also holds a worker thread.
- **Reloading City stops the chase.** The page posts `/api/test12/stop` on `pagehide`, so a refresh, or closing any second tab, ends the run for everyone.
- **Spawn picking** only takes effect on Restart; Launch always starts with a random spawn.
- **Rescue start assumes dispatch succeeded.** `rescue.tick` writes `autonomy.plan.gimbal_deg` right after `p.dispatch(...)`; if dispatch were refused, that line would raise on every tick.
- **3D city overlay** rebuilds the police car, belief points, rings and beacon (with a new DOM label) 20 times a second.
- **Survey map** uses a fixed dark palette in the light theme, and zoom has no pan.
- **Docstrings** in `test12/render.py` and `test12/sim.py` (`render_scene`) still mention the removed views. ### 5.13 Skydio removal, phase B (open, unchanged) The 3D model is replaced, but `backend/third_party/skydio_x2/`, `sim/mujoco_drone.py`, the jev-x2 stack (PJ1, PJ2),
the `/api/sim3d/asset/x2.obj` route and the doc and pitch mentions remain. ### 5.14 Stale docs (low, but many) | File | What is out of date |
|---|---|
| `DEMO_RUNBOOK.md` | Whole walkthrough: CT1 phone chase with the QR panel, `/dashboard` → "Start demo incident", "four worlds" |
| `IMPLEMENTATION_STATUS.md` | No entry after E7; says incident command is at `/dashboard` |
| `README.md` | Line 85 says the route-reasoning card is on `/` and `/dashboard`; line 132 sends people to `/dashboard`; hero image is the old console |
| `test12/README.md` | Still describes the third-person and bird's-eye views and lists `views.py` |
| The teammate's docs (5.7) | `/dashboard` should be `/command` |
| `docs/MERGE_AND_DASHBOARD_REPORT.md` | Reads as committed; see sections 3 and 5.1 to 5.4 |
| Everything listed in [basic_doc2.md](basic_doc2.md) 6.4 | Unchanged apart from the README/ARCHITECTURE/LIMITATIONS paragraphs the merge added | --- ## 6. The merge plan, package by package | Package | Status | Notes |
|---|---|---|
| WP0 DJI quad (required) | Done | Drawn scale not labelled (section 3) |
| WP0 hazards merge (optional) | Done | `f14030f` |
| WP1 test12 loss behaviour | Done | Batch target met (no run without a re-find; median following 72.1%), but 4 of 12 runs follow under 50% and 6 end while lost |
| WP2 City launches test12 | Done | Picker, Drone POV MJPEG, survey map with belief and pings, callouts with voice, Stop/Start/Restart, clean switch back to the main engine |
| WP3 test12 in the 3D tab | Done | Police car, belief, survey, scan/orbit ring, callout line, ping |
| WP4 flood rescue | Done with caveats | 5.2 to 5.5 |
| WP5 fire rescue | Done with caveats | Spread rate halved; 5.2 to 5.4 |
| WP6 verification and docs | Partly | Headless and browser checks made; dashboard rebuilt; docs only partly updated (5.14); nothing committed | Open questions answered by default: Q1 YOLO for flood and fire (yes, tiled); Q2 crew speed (4 and 2 m/s, not
compressed walking); Q3 hold 10 s then return (yes); Q4 digital beacon (event, map pin, 3D marker, callout);
Q5 `city.py` kept unlisted; Q6 `/test12` kept; Q7 `/drive` does not steer test12. --- ## 7. The repo as a whole now | Area | Size | State |
|---|---|---|
| `backend/overwatch/` | 126 Python files, 25,135 lines | Simulator, perception, autonomy, decisions D1 to D13, incidents, voice, Tiger mirror, tunnel, live DJI bridge (teammate), rescue mission |
| `backend/tests/` | 43 files, 231 test functions | Not run since before E7; `test_worlds.py` edited for the new registry. Tests that call `create_app()` still read `.env` (basic_doc2 6.7). |
| `test12/` | 18 Python files (4,367 lines) + 833 lines of web | Integrated as City chase; `/test12` kept as a developer page with the drone view only |
| `dashboard/src/` | 51 TS/TSX files (11,800 lines) + CSS | Pages: `/` and `/dashboard` picker and workspace, `/command` incident command, `/ops` console, `/3d`, `/drive`, `/responder`, `/responder/sensors` |
| `ios/` | 35 Swift files, 6,440 lines | teammate added manual-override and camera-only limits with Swift tests; still never built here (no Xcode) |
| API | 146 HTTP operations, 10 WebSockets | Section 4.2 |
| Scenarios | 48 registered + T12 | Registry: training area 27 (demo, DEMO-1, S01 to S17, S20, OW1 to OW7), pursuit world 11 (PP0 to PP8, PJ1, PJ2), voice 2 (VC1, VC2), SAR 4, flood 2, fire 2. The picker offers 30: Police pursuit 19 (with OW1 to OW7 and VC1), Search and rescue 5 (with VC2), Training 1 (DEMO-1), City 1 (T12), Flood 2, Fire 2. |
| Tracked files / commits | 455 / 63 | 5 commits unpushed | Unchanged from [basic_doc2.md](basic_doc2.md): real hardware is untested (iOS apps not built, DJI bridge and
responder phone not flown), detector accuracy on real video is unmeasured, the decision engines other than rules
have not been exercised for D12 and D13, and the config/hygiene items in its 6.8. --- ## 8. What to do next 1. **Stop or restart the servers** (5.10): stop :8030; restart :8012 so it runs the merged code.
2. **Decide the scenario question** (5.2): keep the overwrite, or restore FR1, FR2, FF1, FF2 and add the rescue as new ids. Decide too whether the removed demo controls (5.6) should come back on `/command`, and whether `/dashboard` should stay the picker (5.7).
3. **Commit** the integration in reviewable pieces (test12 loss behaviour; City launcher and 3D adapter; rescue; dashboard redesign; evidence and report), then push.
4. **Make the rescue numbers honest** (5.3, 5.4): offset the dispatch report from the casualty, count refused crew steps, run several seeds per world.
5. **Cut the 3D traffic** (5.8) before demoing over the tunnel, and set `OVERWATCH_TOKEN` (5.11).
6. **Refresh the docs** in 5.14, starting with `DEMO_RUNBOOK.md` and the `/dashboard` → `/command` change.
7. Then the standing priority from [basic_doc2.md](basic_doc2.md): **better pursuit**. test12 still loses the suspect at the end of half its runs; next is the Police pursuit maps (follow cap, look-alike under load).
