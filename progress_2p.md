# Progress 2p

Status notes, ober 2026. No new code goes with this note.

## Works

- **3D models.** The simulated worlds render in 3D: the drone, the people, buildings, trees and streets. This
  covers the dashboard's "3D world" tab and the full-screen `/3d` view.

## Does not work properly yet

- **Routing.** Route choice does not behave properly yet. This covers the pursuit route chosen by decision task
  D12 (`backend/overwatch/autonomy/pursuit.py`) and crew routing in the flood and fire worlds.

## Still to implement

- **Water** for the flood-relief world (`backend/overwatch/sim/worlds/flood.py`).
- **Fire** for the firefighter world (`backend/overwatch/sim/worlds/fire.py`).

Both need to be implemented before the flood-relief and firefighter scenarios are usable.

## Where things are

- How to run the demo: [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md). The demo page is at `/`; the full dashboard is at
  `/dashboard`.
- Done by phase, plus later work: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).
- The idea and the architecture: [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md).
- What is known not to work: [LIMITATIONS.md](LIMITATIONS.md).
