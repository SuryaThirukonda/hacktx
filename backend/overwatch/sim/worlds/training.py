"""The guaranteed-demo training area (scenario DEMO-1).

A small disaster-training yard on the existing training field: one building with a front door (side A), a window
(side B), a rear door (side C) and a garage door on side D that is blocked (the operator marks it manually in the
demo), one emergency supply cache, one volunteer playing a missing person, and one ground responder. Everything is
simulated and labelled SIMULATED wherever it surfaces.
"""

from __future__ import annotations

from .spec import BuildingSpec, DroneSpec, OpeningSpec, PersonSpec, SceneSpec, SupplySpec

TRAINING_AREA = SceneSpec(
    id="training-area-a",
    title="Training area A",
    seed=11,
    description="Disaster-training yard: one building (sides A to D), a blocked garage door, a supply cache, one "
                "volunteer playing a missing person, one responder.",
    buildings=[BuildingSpec(
        id="bldg-1", name="Training building", center=(-14.0, 18.0), size_ew_m=7.0, size_ns_m=10.0, height_m=3.6,
        side_a="S", color=(148, 152, 158),
        openings=[
            OpeningSpec(id="door-a1", kind="door", side="A", offset_m=-1.2, width_m=1.0, height_m=2.1),
            OpeningSpec(id="window-b1", kind="window", side="B", offset_m=1.5, width_m=1.4, height_m=1.1, sill_m=1.0),
            OpeningSpec(id="door-c1", kind="door", side="C", offset_m=0.8, width_m=1.0, height_m=2.1),
            OpeningSpec(id="garage-d1", kind="garage_door", side="D", offset_m=0.0, width_m=2.6, height_m=2.3,
                        blocked=True),
        ])],
    supplies=[SupplySpec(id="supply-1", label="Emergency supply cache", xy=(-7.5, 10.5))],
    people=[
        PersonSpec(id="volunteer", role="subject", label="Volunteer (missing person)", start=(-20.0, 12.5),
                   heading=120.0, shirt=(30, 140, 230),
                   script=[("wait", 45.0), (-19.0, 9.0, 0.9), (-16.0, 7.5, 0.9), ("wait", 120.0)]),
        PersonSpec(id="responder", role="responder", label="Ground responder", start=(2.0, -6.0), heading=330.0,
                   shirt=(90, 40, 20),
                   script=[("wait", 5.0), (-4.0, 4.0, 1.3), (-7.0, 8.5, 1.2), ("wait", 8.0), (-12.5, 10.0, 1.1),
                           ("wait", 200.0)]),
    ],
    drone=DroneSpec(x=-8.0, y=0.0, alt=24.0, heading=320.0, gimbal_pitch=-58.0),
)


def scenario():
    """DEMO-1 as a simulator scenario (measurement-only: the demo checks live in the incident tests)."""
    from ..actors import MoveTo, Wait
    from ..scenarios import DroneInit, Scenario, person, responder

    actors = []
    for p in TRAINING_AREA.people:
        script = [Wait(float(s[1])) if s[0] == "wait" else MoveTo(float(s[0]), float(s[1]), float(s[2]))
                  for s in p.script]
        if p.role == "responder":
            actors.append(responder(p.start, script, heading=p.heading))
        else:
            actors.append(person(p.id, p.role, p.start, p.shirt, script, heading=p.heading))
    d = TRAINING_AREA.drone
    sc = Scenario(
        "DEMO-1", "Guaranteed demo: training area A",
        TRAINING_AREA.description + " The drone hovers over the yard; nothing is locked or flown automatically.",
        duration_s=180.0, drone=DroneInit(d.x, d.y, d.alt, d.heading, d.gimbal_pitch),
        actors=actors, extra_boxes=TRAINING_AREA.boxes(), loop=True, tags=["demo", "incident"])
    sc.scene = TRAINING_AREA  # type: ignore[attr-defined]
    return sc
