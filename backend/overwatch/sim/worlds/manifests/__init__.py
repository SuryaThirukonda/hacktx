"""Scenario manifests: the story each scenario tells on the demo page (title, the problem in one line, timed beats
with a caption, the entity to highlight and a camera preset, and the success line). One JSON file per scenario,
named after it. Captions describe what the scenario is built to show; the live state shows what actually happens."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DIR = Path(__file__).resolve().parent
CAMERA_PRESETS = ("chase", "overview", "top")


@lru_cache(maxsize=64)
def load_manifest(scenario_id: str) -> dict[str, Any] | None:
    path = DIR / f"{scenario_id}.json"
    if not path.exists():
        return None
    m = json.loads(path.read_text())
    for b in m.get("beats", []):
        if b.get("camera_preset") not in (None, *CAMERA_PRESETS):
            raise ValueError(f"{path.name}: unknown camera preset {b['camera_preset']!r}")
    m["beats"] = sorted(m.get("beats", []), key=lambda b: float(b["t"]))
    return m


def manifest_or_default(scenario: Any) -> dict[str, Any]:
    """The scenario's manifest, or one beat made from its description when it has none."""
    m = load_manifest(scenario.id)
    if m is not None:
        return {**m, "scenario": scenario.id}
    return {"scenario": scenario.id, "title": scenario.name, "problem_line": scenario.description, "beats": [],
            "success_line": None, "default": True}
