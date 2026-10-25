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
        self.rejected = set()

    def sim(self):
        return self.sim_provider() if self.sim_provider else None

    def active(self):
        sim = self.sim()
        return (self.p.mode == "SIMULATED" and sim is not None and
                bool(getattr(sim.scenario, "rescue", None)) and self.p.autonomy.target_is_sim)

    def event(self, kind, text, now):
        self.p.outbox.append(dict(type="rescue", kind=kind, text=text, ts_ms=now,
                                  beacon=self.beacon, provenance="simulated"))

    def say(self, text, now, urgent=False):
        self.callouts.append(dict(n=len(self.callouts) + 1, t=now / 1000, speech=text, urgent=urgent))
        self.event("crew_callout", text, now)

    def confirm(self, by="operator"):
        if not self.active() or self.phase != "REVIEW" or not self.beacon:
            raise ValueError("no current rescue beacon awaiting review")
        self.confirmed = True
        self.beacon.update(status="confirmed", confirmed_by=by)
        self.phase = "HOLD"
        self.say("Simulation: rescue beacon confirmed. Crew guidance approved.", self.p.clock.now_ms(), True)

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
        if self.phase == "IDLE" and now >= 1000 and self.p.telemetry:
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
                    and subject.track_id not in self.rejected):
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
                self.say("Simulation: possible person located. Review the beacon before dispatching the crew.", now, True)
        if crew is not None and now - self.last_ping >= 1000:
            self.last_ping = now
            self.ping = dict(xy=crew.pos.tolist(), heading=crew.heading, t=now / 1000,
                             speed=float(np.linalg.norm(crew.vel)))
        if self.phase == "HOLD" and crew is not None and self.beacon:
            if now - self.last_plan >= 2000:
                self.last_plan = now
                self.plan(now, crew.pos)
            if math.dist(crew.pos, self.beacon["xy"]) <= 3:
                self.phase = "CREW_ON_SCENE"
                self.arrived_ms = now
                self.route = []
                self.say("Simulation: crew at the approach point. Drone holding for ten seconds.", now, True)
            elif now - self.last_callout >= 6000:
                self.last_callout = now
                delta = np.array(self.beacon["xy"]) - np.array(self.ping["xy"])
                bearing = heading_from_vector(*delta) % 360
                compass = "north north-east east south-east south south-west west north-west".split()[int((bearing + 22.5) // 45) % 8]
                clock = int(round(((bearing - self.ping["heading"] + 180) % 360 - 180) / 30)) % 12 or 12
                self.say((f"Simulation: person {np.linalg.norm(delta):.0f} meters {compass}, your {clock} o'clock. "
                          "Follow the assessed approach route." if self.route else
                          "Simulation: no assessed route. Crew holding; specialist access review needed."), now)
        if self.phase == "CREW_ON_SCENE" and now - self.arrived_ms >= 10000:
            self.phase = "RETURN"
            self.p.autonomy.rescue_target = None
            self.p.autonomy.set_mode("RTL", now, "simulated rescue complete")
            self.event("rescue_return", "Crew on scene; drone returning to the launchpad.", now)
        if self.phase == "RETURN" and sim.world.launchpad and math.dist(sim.drone.pos[:2], sim.world.launchpad) < 3:
            self.phase = "COMPLETE"
            self.event("rescue_complete", "Simulation: rescue handoff complete; drone returned to pad.", now)

    def plan(self, now, start):
        sim = self.sim()
        spec = sim.scenario.rescue
        speed = spec.get("crew_speed_mps", 3)
        goal = self.beacon["xy"]
        if spec["kind"] == "flood":
            from ..sim.worlds.flood import roads
            graph = roads()
            road_edges = list(graph.edges)
            for key, point in (("crew-now", tuple(start)), ("beacon", goal)):
                p = np.array(point, float)
                def projection(edge):
                    a, b = (np.array(graph.nodes[k], float) for k in edge)
                    delta = b - a
                    return a + delta * np.clip(np.dot(p - a, delta) / max(np.dot(delta, delta), 1e-9), 0, 1)
                edge = min(road_edges, key=lambda e: np.linalg.norm(p - projection(e)))
                road_key = key + "-road"
                graph.nodes[key], graph.nodes[road_key] = point, tuple(projection(edge))
                graph.edges.extend([(key, road_key), (road_key, edge[0]), (road_key, edge[1])])
            result = flood_route(graph, sim.scenario.hazards.flood, now / 1000, "crew-now", "beacon", walk_mps=speed)
            if result:
                result.update(waypoints=[graph.nodes[n] for n in result["path"]], target="beacon")
        elif spec["kind"] == "fire":
            from ..sim.worlds.fire import static_blocked, KEEP_OUT_MARGIN_S
            fire = sim.scenario.hazards.fire
            result = fire_routes(fire, now / 1000, tuple(start), {"beacon": goal}, static_blocked(fire),
                                 KEEP_OUT_MARGIN_S, walk_mps=speed, smoke=fire.smoke(now / 1000))["beacon"]
        else:
            result = None  # the integrated guidance adapter supplies forest paths
        self.set_route(result, start, now)

    def set_route(self, result, start, now):
        self.route = [np.array(v, float) for v in result["waypoints"]] if result else []
        signature = ";".join(f"{x:.0f},{y:.0f}" for x, y in result["waypoints"]) if result else None
        if result:
            result.update(signature=signature, id="rescue", label="to rescue beacon")
        self.p.crew.state = dict(kind=self.sim().scenario.rescue["kind"], label="SIMULATED rescue crew",
                                 route=result, crews=[dict(id="responder", xy=list(start))],
                                 why=None if result else "no assessed route")
        self.p.crew.history.append(dict(ts_ms=now, depart_s=now / 1000, route=signature))
        if signature != self.last_signature:
            self.event("rescue_route", "Crew route updated." if result else "Crew holding: no assessed route.", now)
        self.last_signature, self.no_route = signature, result is None

    def safe(self, point, t):
        sim = self.sim()
        spec = sim.scenario.rescue
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
        if not self.active() or self.phase != "HOLD" or not self.confirmed or not self.route:
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
                    crew_speed_mps=self.sim().scenario.rescue.get("crew_speed_mps", 3),
                    crew_distance_m=round(self.distance, 2), movement_checks=self.move_checks,
                    max_water_depth_m=self.max_water_depth, no_safe_route=self.no_route,
                    unsafe_steps=self.unsafe_steps, arrived_s=self.arrived_ms / 1000 if self.arrived_ms is not None else None)
