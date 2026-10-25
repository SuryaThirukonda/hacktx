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
                "and a person under canopy that the camera cannot see.",
     "scenarios": ["SR1", "SR2", "SR3", "SR4", "VC2"], "not_built": "SR5 to SR8 (terrain relief, second looks, ground-team guidance)"},
    {"id": "flood", "name": "Flood rescue", "mission": "search", "status": "ready",
     "summary": "Spot people stranded by rising water (screening model); route crews around roads closed above "
                "0.3 m of water.",
     "scenarios": ["FR1", "FR2"], "not_built": "FR3 to FR8 (radio reports, debris drift, boats)"},
    {"id": "fire", "name": "Firefighter routing", "mission": "structure", "status": "ready",
     "summary": "A grass fire spreads towards a house (screening model): keep-out areas, smoke over the openings, a "
                "crew route that avoids the keep-out cells, an occupant outside.",
     "scenarios": ["FF1", "FF2"], "not_built": "FF3 to FF8 (interior, roof hatch access, multiple crews)"},
]


def usecases_with_scenarios() -> list[dict[str, Any]]:
    from .sim.scenarios import all_scenarios

    sc = all_scenarios()
    out = []
    for u in USECASES:
        items = [{"id": s, "name": sc[s].name, "description": sc[s].description, "duration_s": sc[s].duration_s}
                 for s in u["scenarios"] if s in sc]
        out.append({**u, "scenarios": items})
    return out
