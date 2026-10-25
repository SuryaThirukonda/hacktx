"""Simulated image-derived rescue beacons and assessed crew movement.

The drone sweeps the search area and puts a beacon on every person the detector sees (nobody is matched
against a description: in a rescue everyone found is tagged), assesses the crew's way in to each one at once
(A* on the hazard grid), and keeps sweeping until the area is covered. The crew walks to the beacons the
operator confirms, one after another. Only the simulated responder actor is animated. Casualty positions come
from current calibrated detections, never the hidden actor role or position.
"""
from __future__ import annotations

import math
import numpy as np

from ..geometry.camera import heading_from_vector

MERGE_M = 6.0  # a person seen again within this distance of a beacon is that beacon, not a new one


class RescueMission:
    def __init__(self, pipeline):
        self.p = pipeline
        self.sim_provider = None
        self.reset()

    def reset(self):
        self.phase = "IDLE"
        self.beacons = []  # every person found, in order: id, xy, track_id, status, route, route_status
        self.target = None  # the beacon the crew is walking to (its id)
        self.route = []
        self.callouts = []
        self.ping = None
        self.last_ping = self.last_plan = self.last_callout = self.last_replan = -1e12
        self.arrived_ms = self.covered_ms = None
        self.last_signature = None
        self.distance = self.max_water_depth = 0.
        self.unsafe_steps = self.move_checks = 0
        self.no_route = False
        self.look_until = self.search_plan = self.vantage = None
        self.confirmed = False
        self.last_assessment = None
        self.rejected = set()

    # ---------------------------------------------------------------------------------------------------
    @property
    def beacon(self):
        """The crew's current beacon, else the first one still pending (older callers expect one beacon)."""
        if self.target is not None:
            return next((b for b in self.beacons if b["id"] == self.target), None)
        return next((b for b in self.beacons if b["status"] == "pending"), self.beacons[0] if self.beacons else None)

    def find(self, beacon_id):
        return next((b for b in self.beacons if b["id"] == beacon_id), None)

    def sim(self):
        return self.sim_provider() if self.sim_provider else None

    def active(self):
        sim = self.sim()
        return (self.p.mode == "SIMULATED" and self.p.incident.active and sim is not None and
                bool(getattr(sim.scenario, "rescue", None)) and self.p.autonomy.target_is_sim)

    def event(self, kind, text, now, beacon=None):
        self.p.outbox.append(dict(type="rescue", kind=kind, text=text, ts_ms=now,
                                  beacon=self._public(beacon) if beacon else None, beacons=len(self.beacons),
                                  provenance="simulated"))

    def say(self, text, now, urgent=False):
        """Short spoken callouts, through the pipeline's single list (the page speaks the newest only)."""
        self.p.say("rescue", text, now, urgent=urgent)
        self.callouts = self.p.callouts[-12:]
        self.event("crew_callout", text, now)

    def confirm(self, by="operator", beacon_id=None):
        """A person confirms a beacon: the crew walks to it (after any it is already walking to)."""
        b = self.find(beacon_id) if beacon_id else next((x for x in self.beacons if x["status"] == "pending"), None)
        if not self.active() or b is None or b["status"] != "pending":
            raise ValueError("no rescue beacon awaiting review")
        now = self.p.clock.now_ms()
        b.update(status="confirmed", confirmed_by=by)
        self.confirmed = True
        if self.target is None:
            self._walk_to(b, now)
        self.say(f"Beacon {b['n']} confirmed. Crew moving.", now, True)

    def reject(self, by="operator", beacon_id=None):
        b = self.find(beacon_id) if beacon_id else next((x for x in self.beacons if x["status"] == "pending"), None)
        if not self.active() or b is None or b["status"] != "pending":
            raise ValueError("no rescue beacon awaiting review")
        self.rejected.add(b["id"])
        b.update(status="rejected", reviewed_by=by, route=None, route_status="rejected")
        self.event("rescue_rejected", f"Beacon {b['n']} rejected; no crew dispatch.", self.p.clock.now_ms(), b)

    # ---------------------------------------------------------------------------------------------------
    def tick(self, now):
        sim = self.sim()
        if not self.active():
            return
        spec = sim.scenario.rescue
        crew = sim.actor("responder")
        if self.phase in ("SEARCH", "COVERED") and self.p.autonomy.mode in ("MANUAL", "ESTOP"):
            self.phase = "PAUSED"
            self.route = []
            self.event("rescue_paused", "Operator stopped the simulation rescue; restart to resume.", now)
            return
        if self.phase == "IDLE" and now >= 1000 and self.p.telemetry:
            if self.p.autonomy.plan is None:
                # a sweep, not a description match: everyone the detector sees gets a beacon
                self.p.dispatch("", tuple(spec["last_seen_xy"]), spec.get("radius_m", 25),
                                altitude_m=spec.get("altitude_m", 24), by="simulated rescue dispatch",
                                sweep_label="the rescue area")
            self.p.autonomy.plan.gimbal_deg = -50.
            self.vantage = np.array(spec["last_seen_xy"], float) + np.array([0., 18.])
            self.p.autonomy.plan.waypoints.insert(0, tuple(self.vantage))
            self.phase = "SEARCH"
            self.event("rescue_dispatched", "Sweeping the area: every person found gets a beacon.", now)
        plan = self.p.autonomy.plan
        if self.phase == "SEARCH" and plan and plan.phase == "SEARCH":
            plan.speed_mps = 3.
        if self.phase == "SEARCH" and self.vantage is not None:
            if self.look_until is None and plan and plan.wp_i > 0:
                self.search_plan = plan
                self.p.autonomy.start_goto([tuple(self.vantage)], tuple(spec["last_seen_xy"]),
                                           "inspect reported rescue location", now, "rescue search")
                self.look_until = now + 12000
                self.say("Searching.", now)
            elif self.look_until is not None and now >= self.look_until and self.search_plan is not None:
                self.p.autonomy.start_dispatch(self.search_plan, now, "resume rescue sweep")
                self.search_plan = None
        if self.phase in ("SEARCH", "COVERED") and self.p.calib.valid:
            self._tag_people(now, crew)
        if self.phase == "SEARCH" and plan and plan.phase == "COVERED" and self.search_plan is None:
            self.phase = "COVERED"
            self.covered_ms = now
            n = len([b for b in self.beacons if b["status"] != "rejected"])
            self.say(f"Area covered. {n} {'person' if n == 1 else 'people'} found." if n else "Area covered. Nobody found.",
                     now, True)
            self.event("rescue_covered", f"Sweep complete: {n} beacon(s).", now)
        if crew is not None and now - self.last_ping >= 1000:
            self.last_ping = now
            self.ping = dict(xy=crew.pos.tolist(), heading=crew.heading, t=now / 1000,
                             speed=float(np.linalg.norm(crew.vel)))
        if crew is not None:
            self._routes(now, crew)
            self._crew(now, crew)
        if (self.phase == "COVERED" and self.covered_ms is not None and now - self.covered_ms >= 15000
                and self.target is None):
            self.phase = "RETURN"
            self.p.autonomy.set_mode("RTL", now, "simulated rescue sweep complete")
            self.event("rescue_return", "Sweep complete; drone returning to the launchpad.", now)
        if self.phase == "RETURN" and sim.world.launchpad and math.dist(sim.drone.pos[:2], sim.world.launchpad) < 3:
            self.phase = "COMPLETE"
            self.event("rescue_complete", "Simulation: rescue handoff complete; drone returned to pad.", now)

    def _fresh(self, now):
        age = self.p._age("drone", now)
        capture = self.p.counters["drone"].last_capture_ms
        return (age is not None and age <= self.p.cfg.sources.drone.max_age_ms and capture is not None
                and -100 <= now - capture <= self.p.cfg.sources.drone.max_age_ms)

    def _tag_people(self, now, crew):
        """A beacon on every confirmed person track with a calibrated ground position."""
        if not self._fresh(now):
            return
        responder = self.p.locks["responder"].track_id
        for t in self.p.raw_tracks:
            if (t.state != "CONFIRMED" or not t.matched_now or t.class_name != "person"
                    or t.track_id == responder or t.hits < 6):
                continue
            info = self.p._track_info(t.track_id)
            if info is None or info.world_xy is None:
                continue
            xy = tuple(map(float, info.world_xy))
            near = next((b for b in self.beacons if b["track_id"] == t.track_id
                         or math.dist(b["xy"], xy) <= MERGE_M + min(4., b.get("uncertainty_m") or 0.)), None)
            if near is not None:
                if near["status"] == "pending" and near["track_id"] == t.track_id:
                    near["xy"], near["uncertainty_m"] = xy, info.world_err  # the same person, a fresher fix
                continue
            n = len(self.beacons) + 1
            b = dict(id=f"B{n}", n=n, xy=xy, label=f"SIMULATED rescue beacon {n}", created_ms=now,
                     track_id=t.track_id, status="pending", uncertainty_m=info.world_err,
                     route=None, route_status=None, route_reason=None, length_m=None, planned_ms=-1e12)
            from ..incident.geo import local_to_latlon
            geo = self.p.cfg.world.geo_reference
            if geo:
                b["lat"], b["lon"] = local_to_latlon(*xy, geo, self.p.cfg.world.north_offset_deg)
            self.beacons.append(b)
            if crew is not None:  # the way in is assessed and drawn at once; the drone keeps sweeping
                self._plan_beacon(b, crew.pos, now)
            self.event("rescue_beacon", f"Person found: beacon {n} placed, crew route assessed.", now, b)
            self.say(f"Found. Beacon {n}.", now, True)

    # ---------------------------------------------------------------------------------------------------
    def assess(self, start, target_xy, now):
        from .crew_routing import assessed_route
        sim = self.sim()
        layers = sim.scenario.hazards
        return assessed_route(tuple(start), tuple(target_xy), world=sim.world,
                              flood=getattr(layers, "flood", None), fire=getattr(layers, "fire", None),
                              t_s=sim.t_s, max_depth_m=sim.scenario.rescue.get("max_depth_m", .15),
                              fire_margin_s=getattr(layers, "margin", 120.),
                              walk_mps=sim.scenario.rescue.get("crew_speed_mps", 3))

    def _plan_beacon(self, b, start, now):
        a = self.assess(start, b["xy"], now)
        b["planned_ms"] = now
        ok = a["status"] in ("access_route", "approach_only")
        b.update(route=[[round(float(x), 2), round(float(y), 2)] for x, y in a["points"]] if ok else None,
                 route_status=a["status"], route_reason=a.get("reason"), length_m=a.get("length_m"))
        return a

    def _routes(self, now, crew):
        """Keep every live beacon's assessed route current: the crew's target every 2 s, the rest every 10 s."""
        for b in self.beacons:
            if b["status"] == "rejected":
                continue
            period = 2000 if b["id"] == self.target else 10000
            if now - b["planned_ms"] >= period:
                a = self._plan_beacon(b, crew.pos, now)
                if b["id"] == self.target:
                    self.last_assessment = a
                    self.set_route(a if b["route"] else None, crew.pos, now)

    def _walk_to(self, b, now):
        self.target = b["id"]
        self.route = [np.array(v, float) for v in b["route"] or []]
        self.no_route = not self.route
        self.last_assessment = None
        b["planned_ms"] = -1e12  # replanned on the next tick from the crew's position

    def _crew(self, now, crew):
        b = self.find(self.target) if self.target else None
        if b is None:
            nxt = next((x for x in self.beacons if x["status"] == "confirmed"), None)
            if nxt is not None:
                self._walk_to(nxt, now)
            return
        arrival_radius = 3 + min(3, max(0, b.get("uncertainty_m") or 0))
        if math.dist(crew.pos, b["xy"]) <= arrival_radius and not self.no_route:
            b.update(status="reached", reached_ms=now)
            self.arrived_ms = now
            self.route = []
            self.target = None
            self.say(f"Crew at beacon {b['n']}.", now, True)
            self.event("rescue_reached", f"Crew on scene at beacon {b['n']}.", now, b)
        elif now - self.last_callout >= 6000 and self.ping:
            self.last_callout = now
            delta = np.array(b["xy"]) - np.array(self.ping["xy"])
            bearing = heading_from_vector(*delta) % 360
            compass = "north north-east east south-east south south-west west north-west".split()[int((bearing + 22.5) // 45) % 8]
            clock = int(round(((bearing - self.ping["heading"] + 180) % 360 - 180) / 30)) % 12 or 12
            self.say((f"Beacon {b['n']} {np.linalg.norm(delta):.0f} metres {compass}, your {clock} o'clock." if self.route else
                      "No safe route. Hold."), now)

    def set_route(self, result, start, now):
        self.route = [np.array(v, float) for v in result["points"]] if result else []
        signature = ";".join(f"{x:.0f},{y:.0f}" for x, y in result["points"]) if result else None
        route = dict(result, waypoints=result["points"], target="beacon", keep_out_cells=0, signature=signature,
                     id="rescue", label=f"to beacon {self.find(self.target)['n']}" if self.target else "to rescue beacon") if result else None
        self.p.crew.state = dict(kind=self.sim().scenario.rescue["kind"], label="SIMULATED rescue crew",
                                 route=route, crews=[dict(id="responder", xy=list(start))],
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
        if (not self.active() or self.target is None or not self.route
                or self.p.autonomy.mode in ("MANUAL", "ESTOP")):
            return
        if not self._fresh(self.p.clock.now_ms()):
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
                b = self.find(self.target)
                if b:
                    b["planned_ms"] = -1e12
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

    # ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _public(b):
        return {k: v for k, v in b.items() if k != "planned_ms"}

    def view(self):
        if not self.active():
            return None
        b = self.beacon
        return dict(phase=self.phase, beacon=self._public(b) if b else None, target=self.target,
                    beacons=[self._public(x) for x in self.beacons],
                    found=len([x for x in self.beacons if x["status"] != "rejected"]),
                    ping=self.ping, callouts=self.callouts[-12:],
                    route=[[round(float(v[0]), 2), round(float(v[1]), 2)] for v in self.route] or None,
                    route_status=(self.last_assessment or {}).get("status"),
                    crew_speed_mps=self.sim().scenario.rescue.get("crew_speed_mps", 3),
                    crew_distance_m=round(self.distance, 2), movement_checks=self.move_checks,
                    max_water_depth_m=self.max_water_depth, no_safe_route=self.no_route,
                    unsafe_steps=self.unsafe_steps, arrived_s=self.arrived_ms / 1000 if self.arrived_ms is not None else None)
