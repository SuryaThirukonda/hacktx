"""Scripted simulator scenarios.

* ``demo`` — the 60-90 s judge walkthrough (loops): subject lock, occlusion under a tree, follow, sprint toward
  the responder, LiDAR proximity on a cardboard obstacle. Operator clicks are *scripted* and logged as such.
* ``S01``…``S20`` — Rescue Copilot synthetic scenarios (subset that needs a scene; the rest are unit tests).
* ``OW1``…``OW6`` — Overwatch L3 simulation scenarios.

Each scenario lists expected events (ground truth for the scenario runner); expectations are about *events the
pipeline should emit*, derived from the script, never from pipeline output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .actors import ActorSpec, Appearance, Face, Leave, Loop, MoveTo, Wait, Wander

RED = (40, 40, 210)
BLUE = (200, 90, 30)
PURPLE = (160, 50, 140)
PINK = (150, 80, 230)
MUSTARD = (30, 160, 215)
NAVY = (110, 40, 20)
MAROON = (40, 30, 150)
JEANS = (130, 90, 50)
DARK = (45, 45, 50)
KHAKI = (90, 130, 150)


@dataclass
class DroneInit:
    x: float = 10.0
    y: float = -10.0
    alt: float = 18.0
    heading: float = 0.0
    gimbal_pitch: float = -50.0
    climb: tuple[float, float, float] | None = None  # pilot climb: (target alt m, rate m/s, start s)


@dataclass
class FaultWindow:
    t0: float
    t1: float
    key: str
    value: Any = True


@dataclass
class ScriptedAction:
    t: float
    action: str  # lock_subject | lock_responder | autonomy | unlock | describe | operator_watch | look_for | dispatch
    arg: Any = None


@dataclass
class ExpectedEvent:
    kind: str
    t_min: float
    t_max: float
    role: str | None = None
    present: bool = True  # False => must NOT occur in the window


@dataclass
class MetricLimit:
    """A bound on a value in the evaluation report (dotted path), e.g. ``phone_vision.portrait.range_error_m.p50``."""

    path: str
    max: float | None = None
    min: float | None = None


@dataclass
class ConflictEvent:
    kind: str  # POSITION_CALLOUT | ZONE_ENTRY | REACQUIRED (informational: the ones D5 arbitrates)
    text: str
    range_m: float | None = None
    closing_mps: float | None = None
    observed_s_ago: float = 0.0


@dataclass
class Conflict:
    """A scripted alert-priority conflict (S16): informational events that become pending together, with the answer
    a careful dispatcher would give (``gt``: an event kind or ``no_alert``) and why. Written before running anything."""

    id: str
    t: float
    events: list[ConflictEvent]
    gt: str
    why: str


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    duration_s: float
    drone: DroneInit = field(default_factory=DroneInit)
    actors: list[ActorSpec] = field(default_factory=list)
    faults: list[FaultWindow] = field(default_factory=list)
    actions: list[ScriptedAction] = field(default_factory=list)
    expected: list[ExpectedEvent] = field(default_factory=list)
    limits: list[MetricLimit] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    dynamics: str = "kinematic"  # kinematic | mujoco (Skydio X2 rigid body from MuJoCo Menagerie)
    extra_boxes: list = field(default_factory=list)  # scenario-only static objects (world.BoxObj)
    loop: bool = False
    hide_markers: bool = False
    tags: list[str] = field(default_factory=list)
    scene: Any = None  # worlds.spec.SceneSpec when the scenario is built from a scene spec
    world: Any = None  # callable() -> World for scenarios outside the training field
    pilot_path: list = field(default_factory=list)  # scripted manual flight: [(x, y, alt, speed m/s), ...]
    hazards: Any = None  # object with overlays(t_s) / layers(t_s) for water, fire and smoke (screening models)
    world_eval: Any = None  # callable(res, scenario) -> dict merged into the report under "world"
    detector: str | None = None  # perception.detector override for this scenario (e.g. "ultralytics": YOLO26, tiled)
    stack: str = "mini2"  # mini2: the kinematic stand-in at pursuit altitude | jev-x2: MuJoCo X2 low, with the reflex and D6
    autonomy: dict | None = None  # autonomy config overrides for this scenario (altitude, standoff, speed caps)
    locks: dict | None = None  # lock config overrides (e.g. a longer search before the operator hand-off)
    crew_problem: Any = None  # callable() -> missions.crew_routing.CrewProblem: ground crews routed live
    rescue: dict | None = None  # simulator-only beacon/review/crew handoff
    dispatch: dict | None = None  # the dispatch request the demo page prefills: {description, last_seen_xy, radius_m}


def responder(start=(4.0, 0.0), script=None, heading=45.0) -> ActorSpec:
    return ActorSpec("responder", "responder", start, heading,
                     Appearance(shirt=NAVY, pants=DARK, vest=True, height_m=1.8), script or [Wait(999)])


def person(aid: str, role: str, start, shirt, script, pants=JEANS, heading=0.0, height=1.74, accel=2.4,
           vary: bool = True) -> ActorSpec:
    """``vary=False`` keeps the default skin, hair, sleeves and trousers: used for true look-alike pairs."""
    return ActorSpec(aid, role, start, heading, Appearance(shirt=shirt, pants=pants, height_m=height, vary=vary), script,
                     accel_mps2=accel)


# --------------------------------------------------------------------------------------------------------------
def _demo() -> Scenario:
    resp = responder((3.0, -1.0), [
        MoveTo(6.5, 3.0, 1.4), Wait(3.0), MoveTo(12.4, 4.0, 1.2), Wait(6.0), Face(355.0), Wait(10.0),
        MoveTo(11.5, 4.5, 0.8), Face(10.0), Wait(60.0)])
    subject = person("subject", "subject", (22.0, 14.0), RED, [
        MoveTo(21.5, 10.5, 1.3), MoveTo(17.3, 8.5, 1.3), Wait(2.5), MoveTo(14.3, 12.2, 1.4),
        MoveTo(12.5, 20.0, 3.0), MoveTo(11.0, 25.0, 1.5), Wait(3.0), Face(185.0), Wait(1.0),
        MoveTo(11.8, 7.4, 5.4), Wait(2.0), MoveTo(19.0, 3.0, 1.4), Wait(30.0)], heading=190.0, accel=3.2)
    b1 = person("bystander-1", "bystander", (-3.0, 4.0), PURPLE, [Wander((-8.0, 2.0, 0.0, 10.0), 200.0, 1.0)], pants=DARK)
    b2 = person("bystander-2", "bystander", (22.0, -2.0), BLUE, [Wander((18.0, 25.0, -5.0, 2.0), 200.0, 1.2)], pants=KHAKI)
    b3 = person("bystander-3", "bystander", (0.0, 9.0), PINK, [Wander((-2.0, 4.0, 7.0, 12.0), 200.0, 0.9)], pants=JEANS, height=1.62)
    return Scenario(
        "demo", "Judge walkthrough",
        "Responder enters, subject is locked by the operator, walks under a tree (occlusion), jogs out of the original "
        "view (drone follows), then sprints toward the responder; responder's LiDAR detects a cardboard obstacle.",
        duration_s=72.0, drone=DroneInit(10.0, -9.0, 18.0, 0.0, -52.0),
        actors=[resp, subject, b1, b2, b3],
        actions=[ScriptedAction(2.0, "lock_responder", "responder"), ScriptedAction(3.0, "lock_subject", "subject"),
                 ScriptedAction(7.0, "autonomy", "FOLLOW")],
        expected=[ExpectedEvent("LIDAR_PROXIMITY", 9.0, 30.0, "responder"),
                  ExpectedEvent("TRACK_LOST", 4.0, 24.0, "subject"),
                  ExpectedEvent("CLOSING_FAST", 22.0, 32.0, "subject")],
        loop=True, tags=["demo"])


def _s01() -> Scenario:
    return Scenario("S01", "Static overhead scene", "Nadir hover, two stationary people: tracks + metric projection.",
                    20.0, DroneInit(10.0, 6.0, 20.0, 0.0, -90.0),
                    [responder((6.0, 4.0)), person("subject", "subject", (14.0, 8.0), RED, [Wait(999)])],
                    actions=[ScriptedAction(2.0, "lock_responder", "responder"), ScriptedAction(2.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("CLOSING_FAST", 0, 20, "subject", present=False)], tags=["geometry"])


def _s02() -> Scenario:
    return Scenario("S02", "Actor crosses left-to-right", "Known 1.4 m/s crossing: smooth calibrated trajectory and speed.",
                    22.0, DroneInit(10.0, 6.0, 20.0, 0.0, -90.0),
                    [responder((4.0, 1.0)), person("subject", "subject", (1.0, 8.0), RED, [Wait(2.0), MoveTo(19.0, 8.0, 1.4), Wait(99)], heading=90)],
                    actions=[ScriptedAction(1.5, "lock_responder", "responder"), ScriptedAction(2.0, "lock_subject", "subject")],
                    tags=["geometry", "speed"])


def _s03() -> Scenario:
    return Scenario("S03", "Actor approaches responder", "Subject walks then jogs toward the responder: approach warning.",
                    22.0, DroneInit(10.0, 0.0, 18.0, 0.0, -70.0),
                    [responder((6.0, 2.0), heading=0.0),
                     person("subject", "subject", (12.0, 14.0), RED, [Wait(2.0), MoveTo(9.0, 9.0, 1.4), MoveTo(6.8, 4.0, 2.2), Wait(99)], heading=210)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("APPROACH", 5.0, 16.0, "subject")], tags=["events"])


def _s04() -> Scenario:
    return Scenario("S04", "Actor leaves FOV", "Subject walks out of a fixed view: TRACK_LOST with last-seen time, no invented continuation.",
                    24.0, DroneInit(10.0, 0.0, 18.0, 0.0, -65.0),
                    [responder((7.0, 3.0)), person("subject", "subject", (10.0, 9.0), RED, [Wait(2.0), MoveTo(36.0, 12.0, 2.0), Wait(99)], heading=80)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("TRACK_LOST", 8.0, 20.0, "subject")], tags=["lifecycle"])


def _s05() -> Scenario:
    return Scenario("S05", "Actor temporarily occluded", "Subject passes under a tree canopy: loss then unverified reacquire.",
                    24.0, DroneInit(14.0, 0.0, 20.0, 0.0, -62.0),
                    [responder((9.0, 3.0)), person("subject", "subject", (12.5, 8.0), RED, [Wait(2.0), MoveTo(16.5, 8.1, 1.2), Wait(3.0), MoveTo(21.0, 8.4, 1.3), Wait(99)], heading=90)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("TRACK_LOST", 3.0, 12.0, "subject"), ExpectedEvent("REACQUIRED", 6.0, 20.0, "subject")],
                    tags=["lifecycle", "reid"])


def _s06() -> Scenario:
    return Scenario("S06", "Drone camera wobble", "Strong camera jitter with stationary people: no false movement after compensation.",
                    20.0, DroneInit(10.0, 4.0, 18.0, 0.0, -80.0),
                    [responder((7.0, 4.0)), person("subject", "subject", (13.0, 8.0), RED, [Wait(999)])],
                    faults=[FaultWindow(0.0, 20.0, "wobble", True)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("APPROACH", 0, 20, "subject", present=False), ExpectedEvent("CLOSING_FAST", 0, 20, "subject", present=False)],
                    tags=["cmc"])


def _s07() -> Scenario:
    return Scenario("S07", "Gimbal turn", "Gimbal pans mid-run: calibration must update from markers or invalidate, never go stale.",
                    24.0, DroneInit(10.0, -2.0, 18.0, 0.0, -75.0),
                    [responder((7.0, 4.0)), person("subject", "subject", (13.0, 8.0), RED, [Wait(999)])],
                    faults=[FaultWindow(8.0, 16.0, "gimbal_pan", True)], tags=["calibration"])


def _s08() -> Scenario:
    return Scenario("S08", "Low light / blur", "Darkened, blurred frames: detection confidence drops, system degrades honestly.",
                    20.0, DroneInit(10.0, 4.0, 18.0, 0.0, -80.0),
                    [responder((7.0, 4.0)), person("subject", "subject", (13.0, 8.0), RED, [Wander((10, 16, 6, 10), 99, 1.0)])],
                    faults=[FaultWindow(6.0, 20.0, "low_light", True), FaultWindow(10.0, 20.0, "blur", True)], tags=["robustness"])


def _s09() -> Scenario:
    ppl = [person("subject", "subject", (9.0, 7.0), RED, [Wander((4.0, 16.0, 3.0, 11.0), 99, 1.0)]),
           person("bystander-1", "bystander", (5.0, 4.0), PURPLE, [Wander((2.0, 18.0, 2.0, 12.0), 99, 0.9)], pants=DARK),
           person("bystander-2", "bystander", (14.0, 9.0), BLUE, [Wander((2.0, 18.0, 2.0, 12.0), 99, 1.1)], pants=KHAKI),
           person("bystander-3", "bystander", (12.0, 4.0), MUSTARD, [Wander((2.0, 18.0, 2.0, 12.0), 99, 0.8)])]
    return Scenario("S09", "Small overhead targets",
                    "The pilot climbs from 20 m to 95 m over five people: targets shrink from ~27 px to ~6 px. Recall "
                    "by target size shows where the detector drops people (compare models with make bench-small).",
                    40.0, DroneInit(10.0, 4.0, 20.0, 0.0, -80.0, climb=(95.0, 2.5, 4.0)),
                    [responder((3.0, 3.0))] + ppl,
                    actions=[ScriptedAction(2.5, "lock_subject", "subject")],
                    limits=[MetricLimit("detection.recall_by_size.16-32px.recall", min=0.9),
                            MetricLimit("detection.recall_by_size.8-16px.recall", min=0.75)],
                    tags=["detection", "small-targets"])


def _s10() -> Scenario:
    return Scenario("S10", "Phone local obstacle", "Responder walks toward a crate: local LiDAR proximity alert, cloud-independent.",
                    16.0, DroneInit(6.0, -4.0, 16.0, 0.0, -60.0),
                    [responder((6.0, 1.0), [MoveTo(6.0, 5.3, 1.0), Wait(99)], heading=0.0)],
                    faults=[FaultWindow(0.0, 16.0, "network_loss", True)],
                    expected=[ExpectedEvent("LIDAR_PROXIMITY", 2.0, 10.0, "responder")], tags=["lidar"])


def _s11() -> Scenario:
    return Scenario("S11", "Ground floor only", "Responder walks across open grass: no obstacle warning from the floor.",
                    16.0, DroneInit(0.0, -6.0, 16.0, 0.0, -60.0),
                    [responder((-6.0, 0.0), [MoveTo(-6.0, 6.0, 1.2), Wait(99)], heading=0.0)],
                    expected=[ExpectedEvent("LIDAR_PROXIMITY", 0, 16, "responder", present=False)], tags=["lidar"])


def _s12() -> Scenario:
    return Scenario("S12", "Intermittent depth", "LiDAR confidence drops out: sectors become unknown, no metric assertion.",
                    16.0, DroneInit(6.0, -4.0, 16.0, 0.0, -60.0),
                    [responder((6.0, 1.0), [MoveTo(6.0, 5.2, 0.9), Wait(99)], heading=0.0)],
                    faults=[FaultWindow(3.0, 9.0, "lidar_dropout", True)], tags=["lidar"])


def _s13() -> Scenario:
    resp = responder((2.0, 0.0), [MoveTo(6.0, 0.8, 1.0), Face(0.0), Wait(6.5), MoveTo(6.0, 5.0, 0.7), Wait(99)], heading=80.0)
    subj = person("subject", "subject", (6.6, 3.8), RED, [Face(200.0), Wait(10.5), MoveTo(10.5, 3.4, 1.2), Wait(99)],
                  heading=200.0)
    return Scenario("S13", "Phone frame rotated",
                    "Responder holds the phone landscape, then upright (portrait): vision boxes, LiDAR ranges, bearings "
                    "and the map frustum must follow the rotation.",
                    22.0, DroneInit(6.0, -4.0, 16.0, 0.0, -60.0), [resp, subj],
                    faults=[FaultWindow(8.0, 22.0, "phone_portrait", True)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("LIDAR_PROXIMITY", 13.0, 22.0, "responder")],
                    limits=[MetricLimit("phone_vision.landscape.range_error_m.p50", max=0.25),
                            MetricLimit("phone_vision.portrait.range_error_m.p50", max=0.25),
                            MetricLimit("phone_vision.portrait.range_coverage", min=0.6),
                            MetricLimit("phone_vision.landscape.bearing_error_deg.p50", max=2.0),
                            MetricLimit("phone_vision.portrait.bearing_error_deg.p50", max=2.0),
                            MetricLimit("cross_view.landscape.accuracy", min=0.9),
                            MetricLimit("cross_view.portrait.accuracy", min=0.9),
                            MetricLimit("cross_view.portrait.associated", min=5)],
                    tags=["phone", "orientation"])


def _s14() -> Scenario:
    return Scenario("S14", "Unaligned cameras", "No responder lock/registration: map stays NOT REGISTERED, no fused distance.",
                    14.0, DroneInit(10.0, 0.0, 18.0, 0.0, -65.0),
                    [responder((7.0, 3.0)), person("subject", "subject", (12.0, 9.0), RED, [Wander((10, 14, 7, 11), 99, 1.0)])],
                    actions=[ScriptedAction(1.5, "lock_subject", "subject")], tags=["fusion"])


def _s15() -> Scenario:
    return Scenario("S15", "Internet/API loss", "Cloud decisions time out / 429 / refuse: rules fallback, banner, alerts still fire.",
                    40.0, DroneInit(10.0, -9.0, 18.0, 0.0, -52.0), _demo().actors,
                    faults=[FaultWindow(0.0, 14.0, "api_timeout", True), FaultWindow(14.0, 26.0, "api_429", True),
                            FaultWindow(26.0, 40.0, "network_loss", True)],
                    actions=_demo().actions, tags=["decisions"])


def _s16() -> Scenario:
    C, E = Conflict, ConflictEvent
    conflicts = [
        C("C1", 2.0, [E("REACQUIRED", "Subject reacquired (unverified), 11 o'clock, 18 meters", 18.0, 0.0),
                      E("POSITION_CALLOUT", "Subject 11 o'clock, 18 meters", 18.0, 0.0)],
          "REACQUIRED", "Same position; the reacquisition is the new information."),
        C("C2", 5.0, [E("POSITION_CALLOUT", "Subject 2 o'clock, 34 meters, stationary", 34.0, 0.0)],
          "no_alert", "The responder is handling a close obstacle; a distant stationary subject can wait."),
        C("C3", 10.0, [E("ZONE_ENTRY", "Subject entered Search sector B, 28 meters", 28.0, -0.5),
                       E("POSITION_CALLOUT", "Subject 1 o'clock, 9 meters, approaching", 9.0, 1.6)],
          "POSITION_CALLOUT", "An approaching subject at 9 m matters more than a far zone entry."),
        C("C4", 14.0, [E("ZONE_ENTRY", "Subject entered Staging area (your zone), 5 meters", 5.0, 0.8),
                       E("POSITION_CALLOUT", "Subject 10 o'clock, 5 meters", 5.0, 0.8)],
          "ZONE_ENTRY", "Entering the responder's own zone carries the position plus context."),
        C("C5", 18.0, [E("REACQUIRED", "Subject reacquired (unverified), 3 o'clock, 22 meters", 22.0, 0.0, 3.5),
                       E("POSITION_CALLOUT", "Subject 4 o'clock, 21 meters", 21.0, 0.0, 0.2)],
          "POSITION_CALLOUT", "The reacquisition report is 3.5 s old; the fresh position supersedes it."),
        C("C6", 22.0, [E("REACQUIRED", "Subject reacquired (unverified), 12 o'clock, 15 meters", 15.0, 0.0)],
          "REACQUIRED", "A single fresh, useful update."),
    ]
    resp = responder((6.0, 1.0), [MoveTo(6.0, 5.3, 1.0), Wait(2.5), MoveTo(6.0, 1.0, 1.0), Wait(99)], heading=0.0)
    return Scenario("S16", "Decision model vs fixed rules",
                    "Six scripted conflicts between informational alerts, each with a written ground-truth answer. "
                    "Both the decision engine's choice (D5) and the fixed-priority rules are scored; three conflicts "
                    "need context the fixed priorities ignore (busy responder, approaching subject, stale report).",
                    26.0, DroneInit(6.0, -4.0, 16.0, 0.0, -60.0), [resp],
                    actions=[ScriptedAction(c.t, "inject_conflict", c.id) for c in conflicts],
                    expected=[ExpectedEvent("LIDAR_PROXIMITY", 2.0, 8.0, "responder")],
                    limits=[MetricLimit("decision_adjudication.logged", min=len(conflicts))],
                    conflicts=conflicts, tags=["decisions"])


def _s17() -> Scenario:
    return Scenario("S17", "One source unavailable", "Drone video drops then phone drops: degraded but truthful UI.",
                    24.0, DroneInit(10.0, 0.0, 18.0, 0.0, -65.0),
                    [responder((7.0, 3.0), [MoveTo(9.0, 5.0, 0.8), Wait(99)]), person("subject", "subject", (12.0, 9.0), RED, [Wander((10, 14, 7, 11), 99, 1.0)])],
                    faults=[FaultWindow(6.0, 12.0, "drone_video_drop", True), FaultWindow(14.0, 20.0, "phone_drop", True)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("SOURCE_OFFLINE", 6.0, 9.0, None), ExpectedEvent("SOURCE_OFFLINE", 14.0, 17.0, None)],
                    tags=["faults"])


def _s20() -> Scenario:
    shirts = [RED, BLUE, PURPLE, PINK, MUSTARD, MAROON, (180, 60, 90), (60, 60, 190)]
    ppl = [person(f"p{i}", "subject" if i == 0 else "bystander", (4.0 + (i % 4) * 4.0, 4.0 + (i // 4) * 4.0), shirts[i],
                  [Wander((2.0, 18.0, 2.0, 12.0), 99, 1.2)]) for i in range(8)]
    return Scenario("S20", "Dense scene", "Eight people wander: ID switches counted, no identity claims.",
                    30.0, DroneInit(10.0, 0.0, 20.0, 0.0, -70.0), [responder((0.0, 0.0))] + ppl,
                    actions=[ScriptedAction(1.5, "lock_subject", "p0")], tags=["tracking"])


def _ow1() -> Scenario:
    return Scenario("OW1", "Walk, then sprint at the responder", "Critical closing alert from kinematics only.",
                    24.0, DroneInit(10.0, -6.0, 18.0, 0.0, -55.0),
                    [responder((8.0, 2.0), heading=0.0),
                     person("subject", "subject", (14.0, 16.0), RED, [Wait(2.0), MoveTo(12.0, 13.0, 1.4), Wait(1.0), MoveTo(8.6, 3.2, 5.5), Wait(99)], heading=200, accel=3.5)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("CLOSING_FAST", 6.0, 14.0, "subject")], tags=["alerts"])


def _ow2() -> Scenario:
    s = _s05()
    return Scenario("OW2", "Walk behind a tree for 3 s", s.description, s.duration_s, s.drone, s.actors,
                    actions=s.actions, expected=s.expected, tags=["reid"])


def _ow3() -> Scenario:
    return Scenario("OW3", "Exit the frame at 5 m/s (FOLLOW)", "Autonomy keeps the subject in frame; time-in-frame metric.",
                    30.0, DroneInit(10.0, -4.0, 18.0, 0.0, -55.0),
                    [responder((6.0, 2.0)), person("subject", "subject", (11.0, 8.0), RED, [Wait(4.0), MoveTo(14.0, 30.0, 4.8), MoveTo(30.0, 30.0, 3.0), Wait(99)], heading=10, accel=3.0)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject"),
                             ScriptedAction(2.5, "autonomy", "FOLLOW")],
                    tags=["autonomy"])


def _ow4() -> Scenario:
    return Scenario("OW4", "Similar people cross (ID-swap trap)", "Two red shirts cross: lock must not silently switch.",
                    24.0, DroneInit(10.0, 0.0, 18.0, 0.0, -75.0),
                    [responder((4.0, 2.0)),
                     person("subject", "subject", (4.0, 9.0), RED, [Wait(2.0), MoveTo(17.0, 9.0, 1.4), Wait(99)], heading=90, vary=False),
                     person("decoy", "bystander", (17.0, 9.6), (50, 45, 205), [Wait(2.0), MoveTo(4.0, 9.6, 1.4), Wait(99)], heading=270, vary=False)],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    tags=["reid"])


def _ow5() -> Scenario:
    return Scenario("OW5", "Subject stops, responder approaches", "No closing alert: the subject is not the one moving.",
                    20.0, DroneInit(10.0, 0.0, 18.0, 0.0, -70.0),
                    [responder((6.0, 1.0), [Wait(3.0), MoveTo(10.5, 7.0, 1.6), Wait(99)], heading=30),
                     person("subject", "subject", (12.0, 9.0), RED, [Wait(999)])],
                    actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject")],
                    expected=[ExpectedEvent("CLOSING_FAST", 0, 20, "subject", present=False),
                              ExpectedEvent("APPROACH", 0, 20, "subject", present=False)], tags=["alerts"])


def _ow6() -> Scenario:
    s = _ow1()
    return Scenario("OW6", "Gust + frame-drop burst during the sprint", "Alert must still fire, possibly later.",
                    s.duration_s, s.drone, s.actors,
                    faults=[FaultWindow(7.0, 10.0, "wind_gust", True), FaultWindow(8.0, 8.6, "drone_video_drop", True),
                            FaultWindow(7.0, 12.0, "frame_drop_pct", 0.05)],
                    actions=s.actions, expected=[ExpectedEvent("CLOSING_FAST", 6.0, 15.0, "subject")], tags=["alerts", "faults"])


def shelter(oid: str, xy: tuple[float, float], size=(6.0, 5.0), heading: float = 0.0) -> list:
    """A roof on four posts (2.7 m clearance). The roof is dark green so the pipeline's image test for an occluder
    at the last-seen position (canopy-coloured pixels) recognises it, as it would a tree."""
    from .world import BoxObj

    L, W = size
    h = math.radians(heading)
    fwd, right = (math.sin(h), math.cos(h)), (math.cos(h), -math.sin(h))
    out = [BoxObj(f"{oid}-roof", "shelter", xy, (L, W, 0.22), heading, (38, 88, 44), z0=2.7)]
    for i, (a, b) in enumerate(((1, 1), (1, -1), (-1, -1), (-1, 1))):
        cx = xy[0] + fwd[0] * a * (L / 2 - 0.3) + right[0] * b * (W / 2 - 0.3)
        cy = xy[1] + fwd[1] * a * (L / 2 - 0.3) + right[1] * b * (W / 2 - 0.3)
        out.append(BoxObj(f"{oid}-post{i}", "post", (cx, cy), (0.16, 0.16, 2.7), heading, (90, 92, 96)))
    return out


def _ow7() -> Scenario:
    resp = responder((4.0, -2.0), [MoveTo(7.0, 2.0, 1.2), Face(20.0), Wait(999)], heading=20.0)
    subject = person("subject", "subject", (11.0, 6.0), RED, [
        Wait(4.0), MoveTo(22.5, 9.5, 1.4), Wait(6.0),                          # 1: under shelter A
        MoveTo(14.0, 12.5, 1.5), MoveTo(2.5, 14.5, 1.5), Wait(6.0),            # 2: under shelter B
        MoveTo(12.0, 17.0, 1.5), MoveTo(25.0, 18.0, 1.6), Wait(60.0)],         # 3: hides under shelter C
        heading=80.0, accel=3.0)
    b1 = person("bystander-1", "bystander", (-3.0, 4.0), PURPLE, [Wander((-8.0, 2.0, 0.0, 8.0), 200.0, 0.9)], pants=DARK)
    b2 = person("bystander-2", "bystander", (8.0, -4.0), BLUE, [Wander((4.0, 14.0, -6.0, -1.0), 200.0, 1.0)], pants=KHAKI)
    return Scenario(
        "OW7", "Decision-driven search (3D, MuJoCo X2)",
        "A Skydio X2 rigid-body quadrotor follows the subject. Each time the subject disappears under a shelter - "
        "twice for six seconds, then long enough to need the operator - decision task D1 picks the bounded "
        "search manoeuvre the autonomy stack flies. Watch it in /3d.",
        duration_s=100.0, drone=DroneInit(10.0, -6.0, 18.0, 0.0, -55.0),
        actors=[resp, subject, b1, b2],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(1.5, "lock_subject", "subject"),
                 ScriptedAction(2.5, "autonomy", "FOLLOW")],
        expected=[ExpectedEvent("TRACK_LOST", 5.0, 80.0, "subject")],
        dynamics="mujoco", extra_boxes=shelter("shelter-a", (22.5, 9.5)) + shelter("shelter-b", (2.5, 14.5), heading=20.0)
        + shelter("shelter-c", (25.0, 18.0)), loop=True, tags=["autonomy", "decisions", "3d"])


def _demo1() -> Scenario:
    from .worlds.training import scenario
    return scenario()


def _world_scenarios() -> list[Scenario]:
    from .worlds import city, fire, flood, pursuit, sar, voice
    return pursuit.scenarios() + city.scenarios() + sar.scenarios() + flood.scenarios() + fire.scenarios() + \
        voice.scenarios()


_BUILDERS = [_demo, _demo1, _s01, _s02, _s03, _s04, _s05, _s06, _s07, _s08, _s09, _s10, _s11, _s12, _s13, _s14, _s15, _s16, _s17, _s20,
             _ow1, _ow2, _ow3, _ow4, _ow5, _ow6, _ow7]


def all_scenarios() -> dict[str, Scenario]:
    out = {}
    for b in _BUILDERS:
        s = b()
        out[s.id] = s
    for s in _world_scenarios():
        out[s.id] = s
    from .worlds.assistance import scenario as assistance_scenario
    scenario = assistance_scenario()
    out[scenario.id] = scenario
    from .worlds.rescue import scenarios as rescue_scenarios
    for rescue in rescue_scenarios():
        out[rescue.id] = rescue
    return out


def get_scenario(sid: str) -> Scenario:
    sc = all_scenarios()
    if sid not in sc:
        raise KeyError(f"unknown scenario {sid!r}; known: {', '.join(sc)}")
    return sc[sid]


__all__ = ["Scenario", "ScriptedAction", "FaultWindow", "ExpectedEvent", "MetricLimit", "Conflict", "ConflictEvent",
           "DroneInit", "all_scenarios", "get_scenario",
           "Leave", "Loop"]
