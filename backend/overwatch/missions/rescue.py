"""Simulated image-derived rescue beacon, visual hold and assessed crew movement.

Only the simulated responder actor is animated. Casualty positions are obtained from
current calibrated detections, never the hidden actor role or position.
"""
from __future__ import annotations

import math
import numpy as np

from ..geometry.camera import heading_from_vector
from .crew_routing import flood_route, fire_routes


class RescueMission:
    def __init__(self, pipeline):
        self.p = pipeline
        self.sim_provider = None
        self.reset()

    def reset(self):
        self.phase = "IDLE"
        self.beacon = None
        self.route = []
        self.callouts = []
        self.ping = None
        self.last_ping = self.last_plan = self.last_callout = -1e12
        self.arrived_ms = None
        self.last_signature = None
        self.distance = self.max_water_depth = 0.
        self.unsafe_steps = self.move_checks = 0
        self.no_route = False
        self.look_until = self.search_plan = self.vantage = None
        self.confirmed = False
        self.last_assessment = None
        self.rejected = set()

    def sim(self):
        return self.sim_provider() if self.sim_provider else None

    def active(self):
        sim = self.sim()
        return (self.p.mode == "SIMULATED" and self.p.incident.active and sim is not None and
                bool(getattr(sim.scenario, "rescue", None)) and self.p.autonomy.target_is_sim)

    def event(self, kind, text, now):
        self.p.outbox.append(dict(type="rescue", kind=kind, text=text, ts_ms=now,
                                  beacon=self.beacon, provenance="simulated"))

    def say(self, text, now, urgent=False):
        """Short spoken callouts, through the pipeline's single list (the page speaks the newest only)."""
        self.p.say("rescue", text, now, urgent=urgent)
        self.callouts = self.p.callouts[-12:]
        self.event("crew_callout", text, now)

    def confirm(self, by="operator"):
        if not self.active() or self.phase != "REVIEW" or not self.beacon:
            raise ValueError("no current rescue beacon awaiting review")
        self.confirmed = True
        self.beacon.update(status="confirmed", confirmed_by=by)
        self.phase = "HOLD"
        self.say("Beacon confirmed. Crew moving.", self.p.clock.now_ms(), True)

    def reject(self, by="operator"):
        if not self.active() or self.phase != "REVIEW" or not self.beacon:
            raise ValueError("no current rescue beacon awaiting review")
        self.rejected.add(self.beacon["track_id"])
        self.beacon.update(status="rejected", reviewed_by=by)
        self.event("rescue_rejected", "Assistance candidate rejected; no crew dispatch.", self.p.clock.now_ms())
        self.route = []
        self.phase = "REJECTED"

    def tick(self, now):
        sim = self.sim()
        if not self.active():
            return
        spec = sim.scenario.rescue
        crew = sim.actor("responder")
        if self.phase in ("SEARCH", "REVIEW", "HOLD") and self.p.autonomy.mode in ("MANUAL", "ESTOP"):
            self.phase = "PAUSED"
            self.route = []
            self.event("rescue_paused", "Operator stopped the simulation rescue; restart to resume.", now)
            return
        if self.phase == "IDLE" and now >= 1000 and self.p.telemetry:
            if self.p.autonomy.plan is None:
                self.p.dispatch(spec["description"], tuple(spec["last_seen_xy"]), spec.get("radius_m", 25),
                                altitude_m=spec.get("altitude_m", 24), by="simulated rescue dispatch")
            self.p.autonomy.plan.gimbal_deg = -50.
            self.vantage = np.array(spec["last_seen_xy"], float) + np.array([0., 18.])
            self.p.autonomy.plan.waypoints.insert(0, tuple(self.vantage))
            self.phase = "SEARCH"
            self.event("rescue_dispatched", "Searching for " + spec["description"], now)
        if self.phase == "SEARCH" and self.p.autonomy.plan and self.p.autonomy.plan.phase == "SEARCH":
            self.p.autonomy.plan.speed_mps = 3.
        if self.phase == "SEARCH" and self.vantage is not None:
            plan = self.p.autonomy.plan
            if self.look_until is None and plan and plan.wp_i > 0:
                self.search_plan = plan
                self.p.autonomy.start_goto([tuple(self.vantage)], tuple(spec["last_seen_xy"]),
                                           "inspect reported rescue location", now, "rescue search")
                self.look_until = now + 12000
            elif self.look_until is not None and now >= self.look_until and self.search_plan is not None:
                self.p.autonomy.start_dispatch(self.search_plan, now, "resume rescue sweep")
                self.search_plan = None
        subject = self.p.locks["subject"]
        if self.phase == "SEARCH" and subject.state == "LOCKED" and subject.verified and self.p.calib.valid:
            info = self.p._track_info(subject.track_id)
            raw = next((t for t in self.p.raw_tracks if t.track_id == subject.track_id), None)
            age = self.p._age("drone", now)
            if (info is not None and info.world_xy is not None and raw is not None and raw.matched_now
                    and age is not None and age <= self.p.cfg.sources.drone.max_age_ms
                    and subject.track_id not in self.rejected
                    and self.p.counters["drone"].last_capture_ms is not None
                    and -100 <= now - self.p.counters["drone"].last_capture_ms <= self.p.cfg.sources.drone.max_age_ms):
                xy = tuple(map(float, info.world_xy))
                self.beacon = dict(xy=xy, label="SIMULATED rescue beacon", created_ms=now,
                                   track_id=subject.track_id, status="pending", uncertainty_m=info.world_err)
                from ..incident.geo import local_to_latlon
                geo = self.p.cfg.world.geo_reference
                if geo:
                    self.beacon["lat"], self.beacon["lon"] = local_to_latlon(*xy, geo, self.p.cfg.world.north_offset_deg)
                self.phase = "REVIEW"
                self.p.autonomy.set_mode("HOLD", now, "rescue candidate awaiting human review")
                self.p.autonomy.rescue_target = xy
                self.event("rescue_beacon", "Person located: candidate beacon awaiting human review.", now)
                if crew is not None:  # the way in is assessed straight away and drawn, before anyone moves
                    self.last_plan = now
                    self.plan(now, crew.pos)
                self.say("Found. Review the beacon.", now, True)
        if self.phase == "REVIEW" and crew is not None and self.beacon and now - self.last_plan >= 2000:
            self.last_plan = now  # keep the route current while a person reviews the beacon
            self.plan(now, crew.pos)
        if crew is not None and now - self.last_ping >= 1000:
            self.last_ping = now
            self.ping = dict(xy=crew.pos.tolist(), heading=crew.heading, t=now / 1000,
                             speed=float(np.linalg.norm(crew.vel)))
        if self.phase == "HOLD" and crew is not None and self.beacon:
            info = self.p._track_info(self.beacon["track_id"])
            age = self.p._age("drone", now)
            if info is None or not info.matched_now or age is None or age > self.p.cfg.sources.drone.max_age_ms:
                self.route = []
                self.no_route = True
                return
            self.beacon["xy"] = tuple(map(float, info.world_xy)) if info.world_xy is not None else self.beacon["xy"]
            self.p.autonomy.rescue_target = self.beacon["xy"]
            if now - self.last_plan >= 2000:
                self.last_plan = now
                self.plan(now, crew.pos)
            arrival_radius = 3 + min(3, max(0, self.beacon.get("uncertainty_m") or 0))
            if math.dist(crew.pos, self.beacon["xy"]) <= arrival_radius and not self.no_route:
                self.phase = "CREW_ON_SCENE"
                self.arrived_ms = now
                self.route = []
                self.say("Crew on scene. Holding.", now, True)
            elif now - self.last_callout >= 6000:
                self.last_callout = now
                delta = np.array(self.beacon["xy"]) - np.array(self.ping["xy"])
                bearing = heading_from_vector(*delta) % 360
                compass = "north north-east east south-east south south-west west north-west".split()[int((bearing + 22.5) // 45) % 8]
                clock = int(round(((bearing - self.ping["heading"] + 180) % 360 - 180) / 30)) % 12 or 12
                self.say((f"Person {np.linalg.norm(delta):.0f} metres {compass}, your {clock} o'clock." if self.route else
                          "No safe route. Hold."), now)
        if self.phase == "CREW_ON_SCENE" and now - self.arrived_ms >= 10000:
            self.phase = "RETURN"
            self.p.autonomy.rescue_target = None
            self.p.autonomy.set_mode("RTL", now, "simulated rescue complete")
            self.event("rescue_return", "Crew on scene; drone returning to the launchpad.", now)
        if self.phase == "RETURN" and sim.world.launchpad and math.dist(sim.drone.pos[:2], sim.world.launchpad) < 3:
            self.phase = "COMPLETE"
            self.event("rescue_complete", "Simulation: rescue handoff complete; drone returned to pad.", now)

    def assess(self, start, now):
        from .crew_routing import assessed_route
        sim = self.sim()
        layers = sim.scenario.hazards
        return assessed_route(tuple(start), tuple(self.beacon["xy"]), world=sim.world,
                              flood=getattr(layers, "flood", None), fire=getattr(layers, "fire", None),
                              t_s=sim.t_s, max_depth_m=sim.scenario.rescue.get("max_depth_m", .15),
                              fire_margin_s=getattr(layers, "margin", 120.),
                              walk_mps=sim.scenario.rescue.get("crew_speed_mps", 3))

    def plan(self, now, start):
        assessment = self.assess(start, now)
        self.last_assessment = assessment
        result = None
        if assessment["status"] in ("access_route", "approach_only"):
            result = dict(assessment, waypoints=assessment["points"], target="beacon", keep_out_cells=0)
        self.set_route(result, start, now)

    def set_route(self, result, start, now):
        self.route = [np.array(v, float) for v in result["waypoints"]] if result else []
        signature = ";".join(f"{x:.0f},{y:.0f}" for x, y in result["waypoints"]) if result else None
        if result:
            result.update(signature=signature, id="rescue", label="to rescue beacon")
        self.p.crew.state = dict(kind=self.sim().scenario.rescue["kind"], label="SIMULATED rescue crew",
                                 route=result, crews=[dict(id="responder", xy=list(start))],
                                 why=None if result else (self.last_assessment or {}).get("reason") or "no assessed route")
        self.p.crew.history.append(dict(ts_ms=now, depart_s=now / 1000, route=signature))
        if signature != self.last_signature:
            self.event("rescue_route", "Crew route updated." if result else "Crew holding: no assessed route.", now)
        self.last_signature, self.no_route = signature, result is None

    def safe(self, point, t):
        sim = self.sim()
        spec = sim.scenario.rescue
        x0, x1, y0, y1 = sim.world.bounds
        if not (x0 <= point[0] <= x1 and y0 <= point[1] <= y1):
            return False
        for box in sim.world.boxes:
            if box.size_lwh[2] < .15:
                continue
            h = math.radians(box.heading_deg)
            dx, dy = point[0] - box.center_xy[0], point[1] - box.center_xy[1]
            along, across = dx * math.sin(h) + dy * math.cos(h), dx * math.cos(h) - dy * math.sin(h)
            if abs(along) < box.size_lwh[0] / 2 + .5 and abs(across) < box.size_lwh[1] / 2 + .5:
                return False
        if spec["kind"] == "flood":
            return sim.scenario.hazards.flood.depth(float(point[0]), float(point[1]), t) <= spec.get("max_depth_m", .15)
        if spec["kind"] == "fire":
            from ..sim.worlds.fire import KEEP_OUT_MARGIN_S, static_blocked
            fire = sim.scenario.hazards.fire
            if not (fire.grid.x0 <= point[0] < fire.grid.x0 + fire.grid.nx * fire.grid.res_m and
                    fire.grid.y0 <= point[1] < fire.grid.y0 + fire.grid.ny * fire.grid.res_m):
                return False
            r, c = fire.grid.cell(*point)
            return not static_blocked(fire)[r, c] and not fire.keep_out(t, KEEP_OUT_MARGIN_S)[r, c]
        return True

    def step_sim(self, dt):
        sim = self.sim()
        if (not self.active() or self.phase != "HOLD" or not self.confirmed or not self.route
                or self.p.autonomy.mode != "HOLD"):
            return
        age = self.p._age("drone", self.p.clock.now_ms())
        capture = self.p.counters["drone"].last_capture_ms
        if (age is None or age > self.p.cfg.sources.drone.max_age_ms or capture is None
                or not -100 <= self.p.clock.now_ms() - capture <= self.p.cfg.sources.drone.max_age_ms):
            return
        crew = sim.actor("responder")
        if crew is None:
            return
        before = crew.pos.copy()
        travel = sim.scenario.rescue.get("crew_speed_mps", 3) * dt
        while self.route and travel > 1e-6:
            delta = self.route[0] - crew.pos
            distance = float(np.linalg.norm(delta))
            if distance < 1e-6:
                self.route.pop(0)
                continue
            step = min(travel, distance, .5)
            target = crew.pos + delta / distance * step
            if not self.safe(target, sim.t_s):
                self.route = []
                self.last_plan = -1e12
                self.no_route = True
                break
            self.move_checks += 1
            if sim.scenario.rescue["kind"] == "flood":
                self.max_water_depth = max(self.max_water_depth, sim.scenario.hazards.flood.depth(*target, sim.t_s))
            crew.pos = target
            travel -= step
            if distance <= step + 1e-6:
                self.route.pop(0)
        movement = crew.pos - before
        crew.vel = movement / dt
        crew.speed = float(np.linalg.norm(crew.vel))
        crew.phase += crew.speed * dt * 2.8
        if crew.speed > 0:
            crew.heading = heading_from_vector(*movement)
        self.distance += float(np.linalg.norm(movement))

    def view(self):
        if not self.active():
            return None
        return dict(phase=self.phase, beacon=self.beacon, ping=self.ping, callouts=self.callouts[-12:],
                    route=[[round(float(v[0]), 2), round(float(v[1]), 2)] for v in self.route] or None,
                    route_status=(self.last_assessment or {}).get("status"),
                    crew_speed_mps=self.sim().scenario.rescue.get("crew_speed_mps", 3),
                    crew_distance_m=round(self.distance, 2), movement_checks=self.move_checks,
                    max_water_depth_m=self.max_water_depth, no_safe_route=self.no_route,
                    unsafe_steps=self.unsafe_steps, arrived_s=self.arrived_ms / 1000 if self.arrived_ms is not None else None)
