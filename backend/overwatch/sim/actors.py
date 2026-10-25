"""Scripted people for the simulator: waypoint scripts, speed profiles, walking animation state."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

from ..geometry.camera import heading_from_vector, wrap_deg

BGR = tuple[int, int, int]


@dataclass
class MoveTo:
    x: float
    y: float
    speed: float = 1.4  # m/s: walk 1.4, jog 3.0, run 4.5, sprint 5.5


@dataclass
class Wait:
    seconds: float


@dataclass
class Face:
    heading_deg: float


@dataclass
class Wander:
    region: tuple[float, float, float, float]  # x0, x1, y0, y1
    seconds: float
    speed: float = 1.1


@dataclass
class Leave:
    """Actor leaves the scene (e.g. enters a building); removed from rendering."""


@dataclass
class Loop:
    """Restart the script from the beginning (positions continue from where the actor is)."""


Step = MoveTo | Wait | Face | Wander | Leave | Loop


SKIN_TONES: tuple[BGR, ...] = ((120, 150, 200), (95, 125, 180), (70, 95, 140), (55, 75, 110), (140, 170, 215))
HAIR_COLORS: tuple[BGR, ...] = ((30, 32, 38), (40, 60, 90), (60, 90, 130), (150, 170, 185), (35, 45, 120))


@dataclass
class Appearance:
    """What a person looks like: clothing colours (what the detector and the appearance model see), build, and
    the details that make one person differ from the next. Nothing here is an identity."""

    shirt: BGR = (40, 40, 200)
    pants: BGR = (120, 70, 40)
    skin: BGR = (120, 150, 200)
    hair: BGR = (30, 32, 38)
    height_m: float = 1.75
    shoulder_m: float = 0.46
    vest: bool = False  # hi-vis responder vest with reflective stripes
    sleeves: str = "long"  # long | short (short: forearms show skin)
    trousers: str = "long"  # long | shorts (shorts: shins show skin)
    jacket: BGR | None = None  # an open jacket over the shirt (torso sides and upper arms)
    hat: BGR | None = None  # a brimmed hat
    backpack: BGR | None = None  # a backpack on the back (changes the torso seen from above)
    vary: bool = True  # skin, hair, sleeves and trousers vary per person unless the scenario fixes them

    def varied(self, actor_id: str) -> "Appearance":
        """Per-person variety from the actor id (deterministic): skin tone, hair, sleeve and trouser length.
        Clothing colours, hats and backpacks stay as the scenario set them, so look-alikes stay look-alikes."""
        if not self.vary:
            return self
        h = sum((i + 1) * ord(c) for i, c in enumerate(actor_id))
        return replace(self, skin=SKIN_TONES[h % len(SKIN_TONES)], hair=HAIR_COLORS[(h // 5) % len(HAIR_COLORS)],
                       sleeves="short" if (h // 25) % 3 == 0 else "long",
                       trousers="shorts" if (h // 75) % 4 == 0 else "long", vary=False)


@dataclass
class ActorSpec:
    actor_id: str
    role: Literal["responder", "subject", "bystander"]
    start_xy: tuple[float, float]
    start_heading_deg: float = 0.0
    appearance: Appearance = field(default_factory=Appearance)
    script: list[Step] = field(default_factory=list)
    accel_mps2: float = 2.2


class Actor:
    def __init__(self, spec: ActorSpec, seed: int = 0) -> None:
        self.spec = spec
        self.id = spec.actor_id
        self.role = spec.role
        self.app = spec.appearance.varied(spec.actor_id)
        self.pos = np.array(spec.start_xy, dtype=float)
        self.heading = spec.start_heading_deg
        self.speed = 0.0
        self.vel = np.zeros(2)
        self.phase = 0.0
        self.active = True
        self._step = 0
        self._step_t = 0.0
        self._wander_target: np.ndarray | None = None
        self._step_t_total = 0.0
        self._rng = np.random.default_rng(seed + sum(ord(c) for c in spec.actor_id))
        self.distance = 0.0
        # operator control (the demo page): a point to walk to, or a direction to keep walking in. While set, the
        # scripted behaviour is paused; ``resume`` hands the person back to the script where it left off.
        self.command: dict | None = None

    def control(self, action: str, x: float | None = None, y: float | None = None, speed: float = 1.4,
                heading_deg: float | None = None, hold_s: float = 0.6) -> None:
        if action == "goto" and x is not None and y is not None:
            self.command = {"kind": "goto", "target": np.array([x, y], float), "speed": float(speed)}
        elif action == "drive" and heading_deg is not None:  # keep walking this way; expires unless renewed
            self.command = {"kind": "drive", "heading": float(heading_deg) % 360.0, "speed": float(speed),
                            "until": self._step_t_total + hold_s}
        elif action == "stop":
            self.command = {"kind": "stop"}
        elif action == "resume":
            self.command = None
        else:
            raise ValueError(f"unknown actor command {action}")
        self.active = True

    # ------------------------------------------------------------------------------------------------------
    def update(self, dt: float) -> None:
        if not self.active or dt <= 0:
            self.vel[:] = 0
            return
        script = self.spec.script
        target: np.ndarray | None = None
        target_speed = 0.0
        guard = 0
        self._step_t_total += dt
        cmd = self.command
        if cmd is not None and cmd["kind"] == "drive" and self._step_t_total > cmd["until"]:
            cmd = self.command = {"kind": "stop"}  # the key was released: come to a stop and wait
        if cmd is not None:
            if cmd["kind"] == "goto":
                if np.linalg.norm(cmd["target"] - self.pos) < max(0.15, self.speed * dt * 1.5):
                    self.command = cmd = {"kind": "stop"}
                else:
                    target, target_speed = cmd["target"], cmd["speed"]
            elif cmd["kind"] == "drive":
                hd = math.radians(cmd["heading"])
                target = self.pos + np.array([math.sin(hd), math.cos(hd)]) * max(3.0, cmd["speed"] * 2.0)
                target_speed = cmd["speed"]
            guard = 99  # the script waits while the operator has this person
        while self._step < len(script) and guard < 8:
            guard += 1
            step = script[self._step]
            if isinstance(step, MoveTo):
                tgt = np.array([step.x, step.y])
                if np.linalg.norm(tgt - self.pos) < max(0.15, self.speed * dt * 1.5):
                    self._next()
                    continue
                target, target_speed = tgt, step.speed
            elif isinstance(step, Wait):
                if self._step_t >= step.seconds:
                    self._next()
                    continue
            elif isinstance(step, Face):
                d = wrap_deg(step.heading_deg - self.heading)
                if abs(d) < 2.0:
                    self._next()
                    continue
                self.heading = (self.heading + math.copysign(min(abs(d), 240.0 * dt), d)) % 360.0
            elif isinstance(step, Wander):
                if self._step_t >= step.seconds:
                    self._wander_target = None
                    self._next()
                    continue
                if self._wander_target is None or np.linalg.norm(self._wander_target - self.pos) < 0.3:
                    x0, x1, y0, y1 = step.region
                    self._wander_target = np.array([self._rng.uniform(x0, x1), self._rng.uniform(y0, y1)])
                target, target_speed = self._wander_target, step.speed
            elif isinstance(step, Leave):
                self.active = False
                self.vel[:] = 0
                return
            elif isinstance(step, Loop):
                self._step, self._step_t = 0, 0.0
                continue
            break

        # speed profile with bounded acceleration (sprints have a visible ramp)
        if target is not None:
            to = target - self.pos
            dist = float(np.linalg.norm(to))
            stop_speed = math.sqrt(max(0.0, 2 * self.spec.accel_mps2 * dist))
            last_move = self._is_last_move() if cmd is None else cmd["kind"] == "goto"
            desired = min(target_speed, stop_speed if last_move else target_speed)
            dv = desired - self.speed
            self.speed += math.copysign(min(abs(dv), self.spec.accel_mps2 * dt), dv)
            desired_heading = heading_from_vector(to[0], to[1])
            dh = wrap_deg(desired_heading - self.heading)
            turn_rate = 300.0 if self.speed < 2.0 else 160.0
            self.heading = (self.heading + math.copysign(min(abs(dh), turn_rate * dt), dh)) % 360.0
            h = math.radians(self.heading)
            direction = np.array([math.sin(h), math.cos(h)])
            # blend: move mostly along heading but converge to target
            step_vec = direction * self.speed * dt
            if np.linalg.norm(step_vec) > dist:
                step_vec = to
            self.pos += step_vec
            self.vel = step_vec / dt
        else:
            self.speed = max(0.0, self.speed - self.spec.accel_mps2 * dt)
            self.vel = np.zeros(2)
        moved = float(np.linalg.norm(self.vel)) * dt
        self.distance += moved
        stride = 0.72 + 0.12 * min(self.speed, 5.0)
        self.phase = (self.phase + 2 * math.pi * moved / (2 * stride)) % (2 * math.pi)
        self._step_t += dt

    def _next(self) -> None:
        self._step += 1
        self._step_t = 0.0

    def _is_last_move(self) -> bool:
        nxt = self._step + 1
        script = self.spec.script
        return nxt >= len(script) or not isinstance(script[nxt], MoveTo)

    # ------------------------------------------------------------------------------------------------------
    @property
    def height(self) -> float:
        return self.app.height_m

    def state_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "xy": (float(self.pos[0]), float(self.pos[1])),
            "heading_deg": float(self.heading),
            "speed_mps": float(np.linalg.norm(self.vel)),
            "vel": (float(self.vel[0]), float(self.vel[1])),
            "active": self.active,
            "controlled": self.command is not None,
            "shirt": self.app.shirt,
            "pants": self.app.pants,
        }
