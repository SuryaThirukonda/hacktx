# Combined integration and simulation dashboard

This report describes the earlier platform integration. The current manual synchronization, repairs, feature inventory and measured validation are recorded in [6am.md](../6am.md). Test counts and implementation claims below are historical snapshots, not the current acceptance record.

## What was combined - `main` was clean at `2d75621` before integration. The previously uncommitted hazards work was checkpointed as `9b34d6d` in its worktree before merging.
- `f14030f` merges `feat/fire-water-3d`, preserving pursuit, belief and story overlays while adding richer flood and fire geometry. It also fixes scenario-specific locks being loaded from the previous scenario.
- `1cbfec8` merges `origin/drone-v1`: DJI live bridge, manual/keyboard and visual-follow controls, person matching and Swift support. Current dispatch/GOTO behavior, pursuit safety and command-epoch guards were retained. GOTO now sets its mode before storing the route, because changing mode clears a route.
- The subsequent integration connects **test12 functionality** to the platform. This is more than combining Git worktrees: City chase runs test12's own process, vision/search, police pings, reports and voice APIs from the main scenario picker, while sharing the platform's Three.js views. All commits are local; no push, deployment or hardware flight was performed.

## Dashboard `/` and `/dashboard` open a situation picker with six photographic cards and one scenario selector. The heading is in the top navigation. Hover/focus smoothly expands a card; selecting it changes the available scenarios. Photos are bundled locally, with credits in `dashboard/public/images/situations/CREDITS.md`. Launching opens a screen-filling workspace with Drone POV / 3D world tabs, chase/overview/top subviews, a shared Survey map / Callouts panel, playback controls, and four focused mission readouts. A fullscreen button is available. Timeline, audit log and source health begin below the first viewport. On narrow screens the panels stack. The survey map shows estimated tracks, report history (City), last-known subject position, crew/police pings, rescue beacons, camera footprints, fading surveyed ground and hazard/crew routes. Truth is an explicit opt-in layer. Main-platform coverage uses the same map-occlusion model as test12; roofs and canopies do not become surveyed just because the camera footprint crosses them. Coverage is a display history, not proof an area is clear, and does not steer the aircraft. Navy/light preference persists across the picker, `/ops` and `/command`. Operations console controls/layout are retained. `/command` retains the full incident-command tools. `/test12` remains a development page.

## Functional integration - CT1/CT2 are retired. There are **48 main-engine scenarios**, plus City chase T12; 30 choices are offered in the six-world picker.
- City loss behavior is one 360° scan, a 20 m orbit for ten seconds, then road search. Last-seen information and surveyed ground persist.
- City drone video keeps its 30 fps target and 5 Hz YOLO cadence. Its rendered chase/top video paths were removed; external views use the shared browser renderer. Stop waits for the child to acknowledge before resuming the main engine. Runtime scenario restarts also stop test12.
- Shared 3D draws a procedural DJI Mini 2 stand-in (0.29 m span), including City. Legacy MuJoCo/X2 dynamics/assets still exist in older evaluation presets; removing those physics presets is not part of the visual-model replacement.
- FR1/FR2/FF1/FF2 launch from staging, search camera images for a stationary yellow-shirt casualty, create an image-derived beacon, aim the drone at that beacon, move the existing crew along a hazard-checked route, publish relative callouts and one-second crew pings, then hold ten seconds after arrival and return to the pad.
- Small rescue actors require tiled YOLO at 1280 px, sampled at 5 Hz. The initial reported location gets an oblique inspection before the sweep; the report, not casualty truth, selects that viewpoint. Beacon coordinates come from the calibrated perception lock.
- The flood demonstration's first casualty now waits on a dry, road-reachable refuge at (-48, 12), rather than in deep water. Other stranded actors remain in FR1. Fire rescue uses a base spread rate of 0.45 m/s, allowing search/launch before a feasible crew handoff. The original 0.9 m/s model correctly yielded no safe route by detection time. Hazard limits were not relaxed: flood steps must stay at or below 0.3 m; fire steps must avoid static obstacles and the current keep-out margin.
- `/api/locks/describe` is registered before the dynamic role route. Configured access-token checks now cover TTS, test12 APIs/socket and rescue callout audio. Phone-drive QR credentials use a fragment, which the client consumes and removes. Setting a token is still required before exposing a dev server.

## Verification As requested by the merge plan: headless scenarios and live browser checks; **no pytest run**. Verification used YAML configuration without loading `.env`, rules decisions, local YOLO weights and an isolated SQLite database. No paid synthesis/model calls or hardware commands were used. | Check | Result |
|---|---|

| Production dashboard build | TypeScript + Vite passed |

| Python | Compilation and undefined-name/syntax Ruff checks passed |

| API contract probe | All 8 checks passed: unauthorized TTS/test12, authorized state, describe route reachability/order, socket rejection, City launcher |

| Existing platform behavior | PP0, PP8, PP5, PP6, PP7 and SR4 met their existing evaluation criteria |

| City pursuit | 12 seeds × 240 s; all found the suspect and reached police-on-scene; zero wrong matches |

| Flood rescue | FR1 and FR2: beacon error 1.38 m, crew arrived 53.5 s, 107.2 m walked, zero unsafe moving steps, maximum water depth 0.136 m |

| Fire rescue | FF1 and FF2: beacon error 1.02 m, crew arrived 51.6 s, 62.95 m walked, zero unsafe moving steps |

| Browser | Situation selection, photo loading, hover expansion, navy/light persistence, City shared 3D and drone tabs, callout history, scroll-down diagnostics, switch back to main engine, flood coverage/beacon/crew display | City's median following fraction is **72.1%** in this batch (the audit quoted an earlier 34%). There were 60 losses and 54 reacquisitions; six runs ended during a later loss. Every run with an initial loss eventually reacquired at least once. This is not a claim of perfect reacquisition. The median drone–suspect distance was 18.2 m. Runs last 240 s, whereas older audit numbers may use a different horizon; compare the raw reports before treating this as a controlled benchmark. Raw results and rerunnable probes are in `evaluation/merge/`. Rescue scenarios used seed 0; broader weather/seed testing remains. The 30 fps video target is not a throughput guarantee: tiled CPU vision and simultaneous evaluations slowed live simulation substantially on this machine. Swift/iOS and real DJI movement remain unverified on hardware. Existing tests that load `.env` were not executed or comprehensively isolated by this task.
