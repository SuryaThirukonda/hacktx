"""Use cases (simulation worlds) offered on the mission home page, with their scenarios and honest status."""

from __future__ import annotations

from typing import Any

USECASES: list[dict[str, Any]] = [
    {"id": "training", "name": "Training area A", "mission": "search", "status": "ready",
     "summary": "Guaranteed demo: one building (sides A to D), a blocked garage door, a supply cache, a volunteer "
                "playing a missing person, a responder.",
     "scenarios": ["DEMO-1"]},
    {"id": "pursuit", "name": "Police pursuit", "mission": "follow", "status": "ready",
     "summary": "Follow an operator-designated, consenting volunteer along a suburban street, under carports and "
                "street trees and past look-alikes; report the trail. PP5 to PP7: autonomous pursuit in the simulator (route "
                "choice by the decisions engine, hold on unverified re-bindings, operator \"look for\").",
     "scenarios": ["PP0", "PP8", "PJ1", "PJ2", "PP1", "PP2", "PP3", "PP4", "PP5", "PP6", "PP7", "VC1", "OW1", "OW2", "OW3", "OW4", "OW5", "OW6", "OW7"],
     "not_built": "PP9 and PP10 (vehicles, multiple officers, night)"},
    {"id": "city", "name": "City chase", "mission": "follow", "status": "ready",
     "summary": "Downtown towers hide the target. The drone launches from the base's pad, finds the described person "
                "and follows; drive the target from a phone (/drive) and lead it round the corners.",
     "scenarios": ["CT1", "CT2"], "not_built": "traffic, night, more than one drone"},
    {"id": "sar", "name": "Search and rescue", "mission": "search", "status": "ready",
     "summary": "Find a lost person in woods: sectors with a probability-of-area prior, a lawnmower sweep, decoys, "
                "and a person in a far clearing reached on the last legs of the sweep.",
     "scenarios": ["SR6", "SR1", "SR2", "SR3", "SR4", "SR5", "VC2"], "not_built": "SR7 and SR8 (terrain relief, second looks)"},
    {"id": "flood", "name": "Flood rescue", "mission": "search", "status": "ready",
     "summary": "Standing water over the south of a neighbourhood with a few dry mounds. The drone launches from the "
                "pad, sweeps the flooded blocks, puts a beacon on the stranded person and guides the crew along dry "
                "roads. The water does not rise.",
     "scenarios": ["FL1"], "not_built": "the earlier rising-water scenarios FR1 to FR3 and SAR1 stay in the registry, unlisted"},
    {"id": "fire", "name": "Wildfire rescue", "mission": "search", "status": "ready",
     "summary": "Burning patches in a forest and a ring of fire round a clearing with someone inside; nothing "
                "spreads. The drone launches from the fire camp, sweeps the burn, puts a beacon on the person and the "
                "crew is routed round the fire.",
     "scenarios": ["WF1"], "not_built": "the earlier spreading-fire scenarios FF1 to FF3 stay in the registry, unlisted"},
]


def usecases_with_scenarios() -> list[dict[str, Any]]:
    from .sim.scenarios import all_scenarios

    sc = all_scenarios()
    out = []
    for u in USECASES:
        items = [{"id": s, "name": sc[s].name, "description": sc[s].description, "duration_s": sc[s].duration_s}
                 for s in u["scenarios"] if s in sc]
        if u["id"] == "city":
            items.insert(0, {"id": "T12", "name": "City chase · isolated engine",
                            "description": "Base launch, camera search, cover loss and recovery, police pings and shared 3D.",
                            "duration_s": 240})
        out.append({**u, "scenarios": items})
    return out
