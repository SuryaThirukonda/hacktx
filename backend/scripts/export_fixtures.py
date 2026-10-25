#!/usr/bin/env python
"""Write the demo-incident fixtures (fixtures/demo_incident/) from the scene spec, so the demo, the tests and the
docs all read the same scene."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import REPO_ROOT  # noqa: E402
from overwatch.sim.worlds.training import TRAINING_AREA  # noqa: E402

EXPECTED = {
    "scenario": "DEMO-1",
    "provenance": "simulated",
    "steps": [
        "operator starts the demo incident (Search)",
        "a possible person is flagged from the simulated drone video (pending review)",
        "operator confirms it; a map marker appears only if its location is verified or manually anchored",
        "operator switches to Structure: openings appear by side (A front door, B window, C rear door, D garage)",
        "operator marks the side D garage door as blocked (MANUAL)",
        "operator briefs the responder; the phone announces it; the responder acknowledges",
        "the dashboard records the acknowledgment; the timeline replays the incident",
    ],
    "openings_by_side": {"A": ["door-a1"], "B": ["window-b1"], "C": ["door-c1"], "D": ["garage-d1"]},
    "blocked_entrances": ["garage-d1"],
    "supply": "supply-1",
    "missing_person": "volunteer",
}


def main() -> int:
    out = REPO_ROOT / "fixtures" / "demo_incident"
    TRAINING_AREA.save(out / "scene.json")
    (out / "ground_truth.json").write_text(json.dumps(TRAINING_AREA.ground_truth(), indent=2))
    (out / "expected.json").write_text(json.dumps(EXPECTED, indent=2))
    print(f"wrote fixtures to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
