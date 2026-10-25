"""The people in a run: the suspect in the red shirt and a few bystanders.

Each person moves along the right-hand sidewalk of the public streets, turning at random at intersections (straight
on is twice as likely as each turn). The suspect runs at a speed drawn per run (3 to 5 m/s) and hides when chased:
once the drone has stayed within 40 m of them for a few seconds, they duck into the nearest cover away from it (a
carport, an awning, a bus shelter, the garage, the park's big trees, or behind a kiosk or a parked truck, keeping it
between them and the drone), wait there for a random time, come out and run on, maybe back the way they came.
They run from the police car too: within 45 m of it they sprint, turn away from it at corners and turn round when
it is ahead of them; within 22 m they go for cover. So the chase goes on after the police get there.
People never read the drone's beliefs, only where the drone and the car physically are. The simulator's ``Actor``
objects are only used to draw them; motion is computed here.
"""

from __future__ import annotations

import math

import numpy as np

from overwatch.geometry.camera import heading_from_vector, wrap_deg
from overwatch.sim.actors import Actor, ActorSpec, Appearance

from .streets import Streets
from .world import HideSpot

RED_SHIRT = (40, 40, 200)
DARK_JEANS = (88, 60, 44)  # dark enough not to read as a saturated blue next to the red shirt
BYSTANDER_SHIRTS = [(40, 170, 210), (150, 60, 120), (60, 150, 60), (150, 140, 30), (128, 128, 128), (40, 80, 120),
                    (44, 44, 44), (90, 140, 170)]
BYSTANDER_PANTS = [(60, 60, 64), (40, 70, 110), (110, 116, 120), (50, 50, 50)]


class Walker:
    """Moves a person along the sidewalks: the street route, or a queue of detour waypoints (to and from cover) that
    take priority; speed changes with bounded acceleration; keeps the animation state the renderer draws."""

    def __init__(self, actor: Actor, streets: Streets, rng: np.random.Generator, e: int, s: float, speed: float) -> None:
        self.a = actor
        self.st = streets
        self.rng = rng
        self.avoid: np.ndarray | None = None  # somewhere to run away from (the police car), when there is one
        self.speed = 0.0
        self.a.pos = streets.lane_xy(e, s).astype(float)
        self.a.heading = heading_from_vector(*streets.dir[e])
        self.e = e
        self.e_next = self._pick(e)
        self.target = streets.corner(e, self.e_next)
        self.queue: list[np.ndarray] = []
        self.default_speed = speed

    def _away(self, opts) -> np.ndarray:
        """How much each way leads away from ``avoid`` (+1 straight away, -1 straight at it)."""
        away = self.a.pos - self.avoid
        n = float(np.linalg.norm(away))
        return np.array([float(np.dot(self.st.dir[o], away / n)) if n > 1e-6 else 0.0 for o in opts])

    def _pick(self, e: int) -> int:
        opts = self.st.turns(e)
        w = np.array([2.0 if float(np.dot(self.st.dir[e], self.st.dir[o])) > 0.9 else 1.0 for o in opts])
        if self.avoid is not None:  # running from the police car: away from it, seldom towards it
            k = self._away(opts)
            w = w * np.where(k > 0.3, 4.0, np.where(k < -0.3, 0.25, 1.0))
        return int(opts[self.rng.choice(len(opts), p=w / w.sum())])

    def resume_from(self, xy: np.ndarray, back_the_way: bool | None = None) -> None:
        """Continue along the streets from a point after a detour: away from the police car if it is near, else in a
        random direction unless told."""
        e, _, _ = self.st.project(xy, lane=True)
        if back_the_way is None and self.avoid is not None:
            back_the_way = bool(self._away([e])[0] < 0.0)
        elif back_the_way is None:
            back_the_way = bool(self.rng.random() < 0.4)
        self.e = int(self.st.rev(e)) if back_the_way else e
        self.e_next = self._pick(self.e)
        self.target = self.st.corner(self.e, self.e_next)

    def step(self, dt: float, speed_goal: float | None = None) -> None:
        goal = self.default_speed if speed_goal is None else speed_goal
        self.speed += float(np.clip(goal - self.speed, -4.0 * dt, 3.0 * dt))
        travel = self.speed * dt
        moved = np.zeros(2)
        for _ in range(6):
            if travel <= 1e-6:
                break
            detour = bool(self.queue)
            tgt = self.queue[0] if detour else self.target
            to = tgt - self.a.pos
            d = float(np.linalg.norm(to))
            if d > travel:
                step = to / d * travel
                self.a.pos = self.a.pos + step
                moved += step
                break
            self.a.pos = np.array(tgt, float)
            moved += to
            travel -= d
            if detour:
                self.queue.pop(0)
                if not self.queue:
                    break  # the end of a detour: stand there until told what next
            else:
                self.e = self.e_next
                self.e_next = self._pick(self.e)
                self.target = self.st.corner(self.e, self.e_next)
        self.a.vel = moved / dt if dt > 0 else np.zeros(2)
        dist = float(np.linalg.norm(moved))
        if dist > 1e-4:
            dh = wrap_deg(heading_from_vector(*moved) - self.a.heading)
            self.a.heading = (self.a.heading + math.copysign(min(abs(dh), 420.0 * dt), dh)) % 360.0
        else:
            self.speed = min(self.speed, 0.5)
        self.a.distance += dist
        stride = 0.72 + 0.12 * min(self.speed, 5.0)
        self.a.phase = (self.a.phase + 2 * math.pi * dist / (2 * stride)) % (2 * math.pi)
        self.a.speed = self.speed


class Suspect:
    """The person in the red shirt. States: RUN, TO_COVER, HIDING, OUT_OF_COVER."""

    CHASE_RADIUS_M = 40.0
    CHASE_TRIGGER_S = 2.5
    MAX_HIDES = 6
    POLICE_FLEE_M = 45.0
    POLICE_HIDE_M = 22.0
    HIDE_COOLDOWN_S = (30.0, 50.0)  # after coming out of cover, this long (uniform) before hiding again

    def __init__(self, streets: Streets, spots: list[HideSpot], rng: np.random.Generator, e: int, s: float,
                 speed: float, blockers: np.ndarray | None = None) -> None:
        self.blockers = blockers if blockers is not None else np.zeros((0, 4))  # x0, x1, y0, y1 of walls and things
        h = heading_from_vector(*streets.dir[e])
        self.actor = Actor(ActorSpec("suspect", "subject", tuple(streets.lane_xy(e, s)), h,
                                     Appearance(shirt=RED_SHIRT, pants=DARK_JEANS, vary=False), [], 3.0),
                           int(rng.integers(1 << 30)))
        self.walker = Walker(self.actor, streets, rng, e, s, speed)
        self.spots = spots
        self.rng = rng
        self.speed = speed
        self.state = "RUN"
        self.chased_s = 0.0
        self.cooldown_until = 0.0
        self.hide_until = 0.0
        self.next_shift = 0.0
        self.hides = 0
        self.turned_until = 0.0
        self.spot: HideSpot | None = None
        self.log: list[tuple[float, str]] = []

    @property
    def xy(self) -> np.ndarray:
        return self.actor.pos

    def _inside(self, p: np.ndarray, skip=None) -> bool:
        B = self.blockers
        hit = (B[:, 0] - 0.3 <= p[0]) & (p[0] <= B[:, 1] + 0.3) & (B[:, 2] - 0.3 <= p[1]) & (p[1] <= B[:, 3] + 0.3)
        if skip is not None:
            hit &= ~((B[:, 0] >= skip[0] - 0.01) & (B[:, 1] <= skip[1] + 0.01) & (B[:, 2] >= skip[2] - 0.01) &
                     (B[:, 3] <= skip[3] + 0.01))
        return bool(hit.any())

    def clear_path(self, a, b, skip=None) -> bool:
        """No wall, building, truck or kiosk on the straight line from ``a`` to ``b`` (sampled every 0.5 m)."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = max(2, int(np.linalg.norm(b - a) / 0.5))
        return not any(self._inside(a + (b - a) * k / n, skip) for k in range(1, n))

    @staticmethod
    def _crosses(a: np.ndarray, b: np.ndarray, box, pad: float = 0.35) -> bool:
        x0, x1, y0, y1 = box[0] - pad, box[1] + pad, box[2] - pad, box[3] + pad
        n = max(2, int(np.linalg.norm(b - a) / 0.25))
        for k in range(1, n):
            p = a + (b - a) * k / n
            if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
                return True
        return False

    def around(self, a, b, box) -> list[np.ndarray]:
        """Waypoints from ``a`` to ``b`` that walk round ``box`` instead of through it."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        if not self._crosses(a, b, box):
            return [b]
        x0, x1, y0, y1 = box
        m = 0.8
        corners = [np.array(c) for c in ((x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m))]
        best, best_len = None, 1e9
        for i, c in enumerate(corners):
            if not self._crosses(a, c, box) and not self._crosses(c, b, box):
                L = float(np.linalg.norm(c - a) + np.linalg.norm(b - c))
                if L < best_len:
                    best, best_len = [c, b], L
            for c2 in (corners[(i + 1) % 4], corners[(i + 3) % 4]):  # round two corners
                if not self._crosses(a, c, box) and not self._crosses(c, c2, box) and not self._crosses(c2, b, box):
                    L = float(np.linalg.norm(c - a) + np.linalg.norm(c2 - c) + np.linalg.norm(b - c2))
                    if L < best_len:
                        best, best_len = [c, c2, b], L
        return best or [b]

    def behind_point(self, spot: HideSpot, drone_xy: np.ndarray) -> np.ndarray:
        """Just past the obstacle on the free side that faces most away from the drone."""
        x0, x1, y0, y1 = spot.obstacle  # type: ignore[misc]
        c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        away = c - np.asarray(drone_xy, float)
        n = float(np.linalg.norm(away))
        away = away / n if n > 1e-6 else np.array([0.0, 1.0])
        hx, hy = (x1 - x0) / 2, (y1 - y0) / 2
        best, best_dot = None, -2.0
        for ang in np.linspace(0.0, 2 * np.pi, 16, endpoint=False):
            u = np.array([math.sin(ang), math.cos(ang)])
            t = min(hx / max(abs(u[0]), 1e-6), hy / max(abs(u[1]), 1e-6))
            p = c + u * (t + 0.7)
            if self._inside(p, spot.obstacle):
                continue
            dot = float(np.dot(u, away))
            if dot > best_dot:
                best, best_dot = p, dot
        return best if best is not None else c + away * (min(hx, hy) + 0.7)

    def _choose_spot(self, drone_xy: np.ndarray) -> HideSpot | None:
        me = self.xy
        ahead = self.walker.st.dir[self.walker.e]
        best, best_cost = None, 1e9
        for sp in self.spots:
            ap = np.array(sp.approach)
            d = float(np.linalg.norm(ap - me))
            if d > 45.0 or math.dist(sp.hide, me) > 58.0:
                continue
            cost = d
            if float(np.dot(ap - me, ahead)) < -3.0:
                cost += 22.0  # back the way they came
            if float(np.dot(ap - me, drone_xy - me)) > 0.0:
                cost += 14.0  # towards the drone
            if cost < best_cost and self.clear_path(me, ap) and \
                    (sp.behind or self.clear_path(ap, sp.hide, skip=sp.obstacle)):
                best, best_cost = sp, cost
        return best

    def _ahead(self, xy) -> bool:
        """``xy`` is in front of them (within 60 deg of the way they are running)."""
        v = self.actor.vel
        to = np.asarray(xy, float) - self.xy
        nv, nt = float(np.linalg.norm(v)), float(np.linalg.norm(to))
        return nv > 0.5 and nt > 1e-6 and float(np.dot(v, to)) / (nv * nt) > 0.5

    def tick(self, t: float, dt: float, drone_xy: np.ndarray | None, drone_alt: float,
             police_xy: np.ndarray | None = None) -> None:
        near = drone_xy is not None and drone_alt > 5.0 and \
            float(np.linalg.norm(np.asarray(drone_xy) - self.xy)) < self.CHASE_RADIUS_M
        self.chased_s = self.chased_s + dt if near else max(0.0, self.chased_s - 0.5 * dt)
        w = self.walker
        cop = None if police_xy is None else float(np.linalg.norm(np.asarray(police_xy, float) - self.xy))
        fleeing = cop is not None and cop < self.POLICE_FLEE_M
        w.avoid = np.asarray(police_xy, float) if fleeing else None
        if self.state == "RUN":
            threat = None
            if self.chased_s >= self.CHASE_TRIGGER_S:
                threat = np.asarray(drone_xy, float)
            if cop is not None and cop < self.POLICE_HIDE_M:
                threat = np.asarray(police_xy, float)
            sp = None
            if threat is not None and t >= self.cooldown_until and self.hides < self.MAX_HIDES:
                sp = self._choose_spot(threat)
                if sp is not None:
                    self.spot = sp
                    ap = np.array(sp.approach, float)
                    if sp.behind:
                        w.queue = [ap] + self.around(ap, self.behind_point(sp, threat), sp.obstacle)
                    else:
                        w.queue = [ap, np.array(sp.hide, float)]
                    self.state = "TO_COVER"
                    self.log.append((t, f"runs for cover: {sp.label}"))
            if sp is None and fleeing and t >= self.turned_until and self._ahead(police_xy):
                w.resume_from(self.xy, back_the_way=True)  # the car is in front of them: turn round
                self.turned_until = t + 6.0
                self.log.append((t, "turns round, away from the police car"))
            w.step(dt, self.speed * (1.25 if fleeing else 1.12 if self.chased_s > 1.0 else 1.0))
        elif self.state == "TO_COVER":
            w.step(dt, self.speed * 1.15)
            if not w.queue:
                self.state = "HIDING"
                self.hides += 1
                self.hide_until = t + float(self.rng.uniform(6.0, 16.0))
                self.next_shift = t + 1.0
        elif self.state == "HIDING":
            sp = self.spot
            if sp is not None and sp.behind and drone_xy is not None and t >= self.next_shift:
                self.next_shift = t + 1.0  # keep the obstacle between them and the drone
                goal = self.behind_point(sp, drone_xy)
                if float(np.linalg.norm(goal - self.xy)) > 0.5:
                    w.queue = self.around(self.xy, goal, sp.obstacle)
            w.step(dt, 1.6 if w.queue else 0.0)
            if t >= self.hide_until:
                self.state = "OUT_OF_COVER"
                if sp is not None:
                    ap = np.array(sp.approach, float)
                    w.queue = self.around(self.xy, ap, sp.obstacle) if sp.behind else [np.array(sp.hide, float), ap]
                else:
                    w.queue = []
                self.log.append((t, "comes out of cover and runs on"))
        elif self.state == "OUT_OF_COVER":
            w.step(dt, self.speed)
            if not w.queue:
                self.state = "RUN"
                self.chased_s = 0.0
                self.cooldown_until = t + float(self.rng.uniform(*self.HIDE_COOLDOWN_S))
                w.resume_from(self.xy)


class Bystander:
    def __init__(self, i: int, streets: Streets, rng: np.random.Generator, e: int, s: float) -> None:
        shirt = BYSTANDER_SHIRTS[int(rng.integers(len(BYSTANDER_SHIRTS)))]
        pants = BYSTANDER_PANTS[int(rng.integers(len(BYSTANDER_PANTS)))]
        self.actor = Actor(ActorSpec(f"bystander-{i}", "bystander", tuple(streets.lane_xy(e, s)), 0.0,
                                     Appearance(shirt=shirt, pants=pants), [], 2.0), int(rng.integers(1 << 30)))
        self.walker = Walker(self.actor, streets, rng, e, s, float(rng.uniform(1.1, 1.6)))

    def tick(self, t: float, dt: float) -> None:
        self.walker.step(dt)
