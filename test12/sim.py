"""One run of the test12 scenario: the 911 call, the search, then following the suspect for as long as the run goes
on and calling out to the police car where they are.

Phases: REPORTED (the call comes in; the drone is on the pad) -> TAKEOFF (straight up to 30 m) -> SEARCH (fly where
the belief says the suspect most likely is now) -> FOLLOW (matched: 18 m behind, reports every 2 s) -> SCAN (out of
sight for 3.5 s: hover over where they were last seen and turn full circles, looking all round, until they are in
view again) -> FOLLOW again, and so on until the page's Stop (STOPPED) or the time limit (TIME_LIMIT, half an hour
live).

The police car leaves the station at the first report and pings its own position to the drone once a second; from
then on each report is a callout relative to the car ("46 m north-east of you, your 2 o'clock"). The car keeps closing
on the latest callout and the suspect keeps running from it and hiding, so the police getting there does not end
the run.

The camera runs at 30 frames a second (one per physics step). The detector runs on every sixth frame, five times a
second, which is plenty for people moving at most a few metres a second and leaves the CPU for the video; its answer
is applied six frames (0.2 s) later, whether it ran inline (headless) or on a worker thread (the live page, which
waits for it if the CPU is slower than that), so a seed always replays the same run. Ground truth is read only by
people (where the drone physically is), by the renderer, and by the scoring in ``summary()``; the drone's side never
sees it.
"""

from __future__ import annotations

import math
import threading
from concurrent.futures import Future

import numpy as np

from overwatch.geometry.camera import heading_from_vector, wrap_deg

from .drone import CAMERA, DeadReckoning, Mini2, Stick, true_camera
from .follow import (
    FOLLOW_BACK,
    SEARCH_ALT,
    SEARCH_PITCH,
    Confirmer,
    TargetFilter,
    follow_command,
    goto_command,
    scan_command,
)
from .los import Occluders
from .people import Bystander, Suspect
from .police import PoliceCar
from .render import DepthRenderer, drone_model, police_car
from .search import Belief, Planner, SurveyLog
from .streets import Streets
from .views import INSET, INTRINSICS, VIEW_NAMES, ViewCams
from .vision import FrameDetections, find_light_bars, ground_xy
from .world import CAR_HOME, PAD, to_base, world_bundle

FPS = 30
DT = 1.0 / FPS  # one physics step per camera frame
DETECT_FRAMES = 6  # the detector looks at every 6th frame: 5 times a second
LATENCY_FRAMES = 6  # and its answer arrives 6 frames (0.2 s) later
TIME_LIMIT = 1800.0  # half an hour unless stopped from the page (headless runs set their own)
LOST_AFTER_S = 3.5
REPORT_EVERY_S = 2.0
PING_EVERY_S = 1.0
ON_SCENE_M = 40.0  # the police car this close to the suspect is on scene
PHASE_LABEL = {"REPORTED": "Crime reported", "TAKEOFF": "Taking off", "SEARCH": "Searching", "FOLLOW": "Following",
               "SCAN": "Lost from view: 360° scan",
               "STOPPED": "Stopped", "TIME_LIMIT": "Time limit reached"}
COMPASS = "N NE E SE S SW W NW".split()
COMPASS_WORDS = {"N": "north", "NE": "north-east", "E": "east", "SE": "south-east", "S": "south", "SW": "south-west",
                 "W": "west", "NW": "north-west"}

_shared: dict = {}
_shared_lock = threading.Lock()


def shared() -> dict:
    """Per process: the map, the renderer, the street graphs and the occluders (the map never changes; built once,
    even when the page's first camera frame and the first run ask for it at the same moment)."""
    with _shared_lock:
        if not _shared:
            w, spots, layout, tex = world_bundle()
            blockers = []  # what a person cannot walk through: walls, buildings, trucks, kiosks, shelter panels, cars
            for b in w.boxes:
                top = b.z0 + b.size_lwh[2]
                if b.kind not in ("pad", "pad_mark", "post") and b.z0 < 1.0 and top > 1.2:
                    cs = b.corners()
                    blockers.append((cs[:, 0].min(), cs[:, 0].max(), cs[:, 1].min(), cs[:, 1].max()))
            tops = []
            for b in w.boxes:
                cs = b.corners()
                tops.append((cs[:, 0].min(), cs[:, 0].max(), cs[:, 1].min(), cs[:, 1].max(), b.z0 + b.size_lwh[2]))
            _shared.update(world=w, spots=spots, layout=layout, renderer=DepthRenderer(w, tex),
                           streets=Streets(w.streets, True), roads=Streets(w.streets, False), occl=Occluders(w),
                           blockers=np.array(blockers, float).reshape(-1, 4), box_tops=np.array(tops, float).reshape(-1, 5))
    return _shared


def compass(deg: float) -> str:
    return COMPASS[int(((deg % 360.0) + 22.5) // 45.0) % 8]


def at_place(label: str) -> str:
    """'at Oak St & Main St' for a corner, 'on Main St near Ash St' along a street."""
    return f"at {label}" if "&" in label else f"on {label}"


def spoken_place(label: str) -> str:
    """'Oak St & Main St' as it is said: 'Oak Street and Main Street'."""
    words = label.replace(" & ", " and ").split(" ")
    return " ".join({"St": "Street", "Rd": "Road", "Ave": "Avenue"}.get(w, w) for w in words)


def spoken_m(d: float) -> str:
    return f"{int(5 * round(d / 5.0)) if d < 100 else int(10 * round(d / 10.0))} meters"


class PersonView:
    """Just what the renderer reads from a person, rebuilt from ``actor_state`` (in this or another process)."""

    def __init__(self, s: dict) -> None:
        self.id, self.role, self.app, self.active = s["id"], s["role"], s["app"], s["active"]
        self.pos = np.array(s["pos"], float)
        self.vel = np.array(s["vel"], float)
        self.heading, self.phase = s["heading"], s["phase"]

    @property
    def height(self) -> float:
        return self.app.height_m


def actor_state(a) -> dict:
    return {"id": a.id, "role": a.role, "app": a.app, "active": a.active, "pos": (float(a.pos[0]), float(a.pos[1])),
            "vel": (float(a.vel[0]), float(a.vel[1])), "heading": float(a.heading), "phase": float(a.phase)}


def render_scene(renderer: DepthRenderer, scene: dict, with_gt: bool = False, view: str | None = None):
    """Draw a scene from ``Run.scene``: the drone camera, or a simulator view with the drone drawn in it (``view``
    overrides the scene's own, e.g. "inset" for the small drone camera in the corner of the other views)."""
    from overwatch.geometry.camera import CameraPose, PinholeCamera

    view = view or scene.get("view", "drone")
    pos, heading, pitch = scene["inset"] if view == "inset" else scene["cam"]
    cam = PinholeCamera(INTRINSICS[view], CameraPose(np.array(pos, float), heading, pitch))
    people = [PersonView(s) for s in scene["people"]]
    vehicles = [police_car(*scene["police"])]
    if view in ("chase", "top") and scene.get("drone") is not None:
        vehicles.append(drone_model(*scene["drone"]))
    return renderer.render(cam, people, vehicles=vehicles, with_gt=with_gt)


def fmt_base(b) -> str:
    return f"base {b[0]:+.1f} m E, {b[1]:+.1f} m N"


class Run:
    def __init__(self, seed: int, detect, asynchronous: bool = False, spawn=None, time_limit: float | None = TIME_LIMIT) -> None:
        """``detect(image, t, crop_centre)`` returns FrameDetections, or a Future of them when ``asynchronous``.
        ``spawn``: where the suspect starts (world x, y; moved to the nearest sidewalk), else a random spot 90 to
        250 m from the base. ``time_limit``: seconds, or None for no limit."""
        S = shared()
        self.S = S
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.detect = detect
        self.asynchronous = asynchronous
        self.time_limit = float(time_limit) if time_limit else math.inf
        st: Streets = S["streets"]
        self.st = st
        self.spawn_picked = spawn is not None
        if spawn is not None:  # picked on the map: the nearest sidewalk, running the way that side of the street goes
            e, s, _ = st.project(np.asarray(spawn, float), lane=True)
            s = float(np.clip(s, min(2.0, st.length[e] / 2), max(st.length[e] - 2.0, st.length[e] / 2)))
            xy = st.lane_xy(e, s)
        while spawn is None:  # the crime: on a sidewalk in the station's neighbourhood, 90 to 250 m from the base
            e = int(self.rng.integers(len(st.frm)))
            if st.length[e] < 20.0:
                continue
            s = float(self.rng.uniform(6.0, st.length[e] - 6.0))
            xy = st.lane_xy(e, s)
            if 90.0 <= math.dist(xy, PAD) <= 250.0:
                break
        self.crime_xy = np.asarray(xy, float)
        self.crime_label = st.place_label(xy)
        self.crime_dir = st.dir[e].copy()  # the caller saw which way they ran
        self.crime_street = st.und_name[e // 2]
        self.head_start = float(self.rng.uniform(5.0, 12.0))
        self.suspect = Suspect(st, S["spots"], self.rng, e, s, float(self.rng.uniform(3.0, 5.0)), S["blockers"])
        self.bystanders = []
        for i in range(5):
            e2 = int(self.rng.integers(len(st.frm)))
            self.bystanders.append(Bystander(i, st, self.rng, e2, float(self.rng.uniform(0.0, st.length[e2]))))
        t = -self.head_start
        while t < 0.0:  # they have been running before anyone called it in
            t += 0.1
            self.suspect.tick(t, 0.1, None, 0.0)
            for b in self.bystanders:
                b.tick(t, 0.1)
        self.t = 0.0
        hd = heading_from_vector(*(self.crime_xy - np.array(PAD)))
        self.drone = Mini2(PAD[0], PAD[1], hd)
        self.nav = DeadReckoning()
        self.nav.heading = hd
        self.belief = Belief(st, S["spots"], S["occl"], self.rng)
        self.belief.seed_report(self.crime_xy, -self.head_start, 0.0, heading_dir=self.crime_dir)
        self.survey = SurveyLog(S["occl"], st)
        self.planner = Planner(SEARCH_ALT, SEARCH_PITCH)
        self.police = PoliceCar(S["roads"], CAR_HOME, 90.0)
        self.views = ViewCams(self.drone, S["box_tops"])
        self.phase = "REPORTED"
        self.filter: TargetFilter | None = None
        self.confirm = Confirmer()
        self.pending: list[tuple] = []
        self.frame_no = 0
        self.next_detect_frame = 0
        self.detect_n = 0
        self.last_det: FrameDetections | None = None
        self.last_bars: list = []
        self.box_v: tuple[float, float] | None = None
        self.pick = None
        self.reports: list[dict] = []
        self.events: list[dict] = []
        self.trail: list[tuple[float, float]] = []
        self.next_report = 0.0
        self.scan: dict | None = None
        self.ping: dict | None = None
        self.next_ping = 0.0
        self.callouts: list[dict] = []
        self.on_scene = False
        self._bar_seen = False
        self._alongside_t = -1e9
        self._refound = False
        self.lost_seen_t: float | None = None
        self.done = False
        self.outcome: str | None = None
        self.frame: tuple[float, np.ndarray] | None = None
        self.truth_visible = 0.0
        self._belief_t = 0.0
        self._plan_t = -10.0
        self._trail_t = -1.0
        self.last_seen: dict | None = None
        self.m = {"first_true_red_t": None, "match_t": None, "match_error_m": None, "losses": 0, "reacquired": 0,
                  "survey_seen": 0, "survey_recent": 0, "wrong_matches": 0, "light_bar_frames": 0, "follow_frames": 0,
                  "follow_dist": [], "refind": [], "callout_err": [], "police_gap": [],
                  "on_scene_t": None, "alongside": 0}
        way = {"N": "north", "E": "east", "S": "south", "W": "west"}.get(compass(heading_from_vector(*self.crime_dir)), "")
        self.event("call", f"911 call: robbery. The suspect is a person in a red shirt, last seen running {way} on "
                           f"{self.crime_street} near {self.crime_label}, {self.head_start:.0f} s ago"
                           f"{' (the start point picked on the map)' if self.spawn_picked else ''}.")

    # ------------------------------------------------------------------------------------------------------------
    def event(self, kind: str, text: str) -> None:
        self.events.append({"t": round(self.t, 1), "kind": kind, "text": text})

    def nav_xy(self) -> np.ndarray:
        return np.array(PAD, float) + self.nav.xy

    def step(self) -> None:
        """One physics step, which is one camera frame."""
        if self.done:
            return
        self.frame_no += 1
        self.t = self.frame_no / FPS
        t = self.t
        d = self.drone
        pol = self.police
        car_xy = np.array(pol.pose()[:2]) if pol.state != "PARKED" else None
        self.suspect.tick(t, DT, d.pos[:2].copy() if d.flying else None, float(d.pos[2]), car_xy)
        for b in self.bystanders:
            b.tick(t, DT)
        pol.step(t, DT)
        if pol.state != "PARKED" and t >= self.next_ping:
            self.next_ping = t + PING_EVERY_S
            self.ping = pol.ping(t)
        h0 = self.nav.heading
        stick, gimbal = self._control(t)
        d.step(DT, stick, gimbal)
        self.nav.update(DT, d.imu_velocity(), d.compass_deg(), d.baro_alt(), d.gimbal)
        if self.phase == "SCAN" and self.scan is not None and not self.scan["facing"]:
            self.scan["turned"] += abs(wrap_deg(self.nav.heading - h0))
        self.views.update(DT, d)
        if self.phase == "FOLLOW":
            self.m["follow_frames"] += 1
        if self.frame_no % DETECT_FRAMES == 0:
            self.belief.predict(t - self._belief_t, self.nav_xy())
            self._belief_t = t
        if self.nav.alt >= 12.0:
            if self.frame_no >= self.next_detect_frame:
                self._capture(t)
                self.next_detect_frame = self.frame_no + DETECT_FRAMES
        else:
            self.next_detect_frame = self.frame_no + 1
        self._apply_ready()
        if self.done:
            return
        if self.phase == "SEARCH" and t - self._plan_t >= 1.0:
            self._plan_t = t
            self.pick = self.planner.update(t, self.nav_xy(), self.belief, self.st.place_label)
        if self.phase == "FOLLOW" and t >= self.next_report:
            self._report(t)
        elif self.phase == "SCAN" and t >= self.next_report and self.last_seen is not None:
            self._report_lost(t)
        if self.frame_no % DETECT_FRAMES == 0:  # scoring only: how close the drone stays, how close the police get
            if self.phase == "FOLLOW":
                self.m["follow_dist"].append(round(math.dist(d.pos[:2], self.suspect.xy), 1))
            if self.on_scene:
                self.m["police_gap"].append(round(math.dist(pol.pose()[:2], self.suspect.xy), 1))
        if t - self._trail_t >= 0.5:
            self._trail_t = t
            p = self.nav_xy()
            self.trail.append((round(float(p[0]), 1), round(float(p[1]), 1)))
        if t >= self.time_limit:
            self._finish("TIME_LIMIT", f"Time limit ({self.time_limit / 60:g} min): run stopped.")

    def stop(self) -> None:
        """Stopped from the page."""
        if not self.done:
            self._finish("STOPPED", "Stopped from the page.")

    # ------------------------------------------------------------------------------------------------------------
    def _control(self, t: float) -> tuple[Stick, float]:
        nav = self.nav
        if self.phase == "REPORTED":
            if t >= 1.0:
                self.phase = "TAKEOFF"
                self.event("dispatch", f"Drone dispatched from the base: climbing to {SEARCH_ALT:.0f} m.")
            return Stick(), -90.0
        if self.phase == "TAKEOFF":
            if nav.alt >= 20.0:  # clear of everything round the base: head out and finish the climb on the way
                self.phase = "SEARCH"
                self._plan_t = -10.0
                self.event("search", f"Climbing through {nav.alt:.0f} m to {SEARCH_ALT:.0f} m and heading out. "
                                     f"Searching where the suspect could have run since the call: the streets out "
                                     f"from {self.crime_label}.")
            yaw = float(np.clip(2.2 * ((heading_from_vector(*(self.crime_xy - self.nav_xy())) - nav.heading + 540) % 360
                                       - 180), -90, 90))
            return Stick(0.0, 0.0, 4.0, yaw), (SEARCH_PITCH if nav.alt > 6.0 else -90.0)
        if self.phase == "SCAN" and self.scan is not None:
            hits = self.confirm.hits
            face = hits[-1][1] if hits and t - hits[-1][0] < 1.5 else None
            self.scan["facing"] = face is not None
            return scan_command(nav, PAD, self.scan["centre"], self.scan["turn"], face)
        if self.phase == "SEARCH":
            if self.pick is None:
                return Stick(0.0, 0.0, float(np.clip(0.8 * (SEARCH_ALT - nav.alt), -3, 3)), 25.0), SEARCH_PITCH
            return goto_command(nav, PAD, self.pick.viewpoint, self.pick.target, pitch=self.pick.pitch)
        if self.phase == "FOLLOW" and self.filter is not None:
            pos, vel = self.filter.at(t)
            bv = self.box_v[1] if self.box_v is not None and t - self.box_v[0] < 0.6 else None
            return follow_command(nav, PAD, pos, vel, bv)
        return Stick(0.0, 0.0, 0.0, 0.0), self.drone.gimbal

    # ------------------------------------------------------------------------------------------------------------
    def scene(self, view: str = "drone") -> dict:
        """What a camera sees at this frame, as plain data (another process can draw it: ``render_scene``): the
        drone's camera, or a simulator view ("chase", "top") with the drone in it and its camera as an inset."""
        cam = true_camera(self.drone)
        people = [self.suspect.actor] + [b.actor for b in self.bystanders]
        own = (cam.pose.position.tolist(), cam.pose.heading_deg, cam.pose.pitch_deg)
        sc = {"view": "drone", "cam": own, "people": [actor_state(a) for a in people], "police": self.police.pose()}
        if view in ("chase", "top"):
            d = self.drone
            sc.update(view=view, cam=self.views.pose(view, d), inset=own,  # drawn standing on the 10 cm pad and its marks
                      drone=(float(d.pos[0]), float(d.pos[1]), float(d.pos[2]) + 0.15, float(d.heading)))
        return sc

    def render(self) -> np.ndarray:
        res = render_scene(self.S["renderer"], self.scene(), with_gt=True)
        g = res.gt.get("suspect")
        self.truth_visible = float(g.visibility) if g is not None else 0.0
        self.frame = (self.frame_no, res.image)
        return res.image

    def _capture(self, t: float) -> None:
        img = self.render()
        cam_nav = self.nav.camera(PAD)
        crop = None
        if self.phase == "FOLLOW" and self.filter is not None and self.detect_n % 4 != 0:
            pos, _ = self.filter.at(t)
            uv, z = cam_nav.project(np.array([[pos[0], pos[1], 0.9]]))
            if z[0] > 0 and 0 <= uv[0, 0] < CAMERA.width and 0 <= uv[0, 1] < CAMERA.height:
                crop = (float(uv[0, 0]), float(uv[0, 1]))
        self.detect_n += 1
        res = self.detect(img, t, crop)
        bars = find_light_bars(img) if self.police.state != "PARKED" else []
        self.pending.append((self.frame_no + LATENCY_FRAMES, t, cam_nav, res, bars, self.suspect.xy.copy(),
                             self.truth_visible))

    def _apply_ready(self) -> None:
        while self.pending and self.pending[0][0] <= self.frame_no:
            _, tc, cam, res, bars, truth_xy, truth_vis = self.pending.pop(0)
            fd = res.result() if isinstance(res, Future) else res
            self._apply(tc, cam, fd, bars, truth_xy, truth_vis)
            if self.done:
                return

    def _apply(self, tc: float, cam, fd: FrameDetections, bars: list, truth_xy: np.ndarray, truth_vis: float) -> None:
        reds = []
        for p in fd.people:
            x0, y0, x1, y1 = p.box
            p.xy = ground_xy(cam, ((x0 + x1) / 2, y1), 0.0)
            if p.is_red and p.xy is not None:
                reds.append(p)
        for b in bars:
            b.xy = ground_xy(cam, ((b.box[0] + b.box[2]) / 2, (b.box[1] + b.box[3]) / 2), 1.55)
        self.last_det, self.last_bars = fd, bars
        if bars:
            self.m["light_bar_frames"] += 1
        if self.m["first_true_red_t"] is None and any(math.dist(p.xy, truth_xy) < 4.0 for p in reds):
            self.m["first_true_red_t"] = tc
        seen, recent = self.survey.update(tc, cam)
        if self.phase in ("TAKEOFF", "SEARCH", "SCAN"):
            self.m["survey_seen"] += seen
            self.m["survey_recent"] += recent
        if self.phase != "FOLLOW":
            self.belief.observe(cam, [p.xy for p in reds], tc)
        if self.phase in ("TAKEOFF", "SEARCH", "SCAN"):
            for p in sorted(reds, key=lambda q: -q.conf):
                got = self.confirm.add(tc, p.xy)
                if got is not None:
                    self._start_follow(tc, got, truth_xy)
                    break
        elif self.phase == "FOLLOW" and self.filter is not None:
            pos_c, _ = self.filter.at(tc)
            gate = 6.0 + 2.0 * max(0.0, tc - self.filter.t_meas)
            best = min(reds, key=lambda q: math.dist(q.xy, pos_c), default=None)
            if best is not None and math.dist(best.xy, pos_c) <= gate:
                self.filter.update(best.xy, tc)
                self.box_v = (tc, (best.box[1] + best.box[3]) / 2)
            elif tc - self.filter.t_meas > LOST_AFTER_S:
                self._lose(tc)
        bar = next((b for b in bars if b.xy is not None), None)
        if bar is not None and not self._bar_seen and self.police.state != "PARKED":
            self._bar_seen = True  # once: the camera's own check on the car, against what the car says
            ref = self.filter.at(tc)[0] if self.phase == "FOLLOW" and self.filter is not None else \
                np.array(self.last_seen["xy"]) if self.last_seen else None
            bits = [f"{math.dist(bar.xy, ref):.0f} m from the suspect"] if ref is not None else []
            if self.ping is not None:
                car = np.array(self.ping["xy"]) + np.array(self.ping["vel"]) * (tc - self.ping["t"])
                bits.append(f"where the camera puts it agrees with the car's own ping to {math.dist(bar.xy, car):.1f} m")
            self.event("police", "Police car in the drone's view (its red and blue light bar)" +
                       (": " + "; ".join(bits) if bits else "") + ".")

    def _start_follow(self, tc: float, got, truth_xy: np.ndarray) -> None:
        pos, vel = got
        self.filter = TargetFilter(pos, vel, tc)
        first = self.m["match_t"] is None
        err = math.dist(pos, truth_xy)
        if err > 6.0:
            self.m["wrong_matches"] += 1
        if first:
            self.m["match_t"] = tc
            self.m["match_error_m"] = round(err, 2)
            self.event("match", f"YOLO found a person whose shirt is red (3 sightings in "
                                f"{tc - self.confirm.hits[0][0]:.1f} s) near {self.st.place_label(pos)}: following "
                                f"from {FOLLOW_BACK:.0f} m behind with the camera on them.")
        else:
            self.m["reacquired"] += 1
            gone = tc - self.lost_seen_t if self.lost_seen_t is not None else 0.0
            self.m["refind"].append(round(gone, 1))
            self.event("match", f"Back in view near {self.st.place_label(pos)} after {gone:.0f} s out of sight (found "
                                f"by the 360° scan): following again.")
            self._refound = True
        self.confirm.reset()
        self.scan = None
        self.phase = "FOLLOW"
        self.next_report = self.t

    def _lose(self, tc: float) -> None:
        """Out of sight for LOST_AFTER_S: hover over where they were last seen and turn full circles (the way they
        were heading first) until they are in view again."""
        f = self.filter
        assert f is not None
        pos, vel = f.at(f.t_meas)
        label = self.st.place_label(pos)
        self.last_seen = {"t": f.t_meas, "xy": (float(pos[0]), float(pos[1])), "base": to_base(pos), "label": label}
        self.lost_seen_t = f.t_meas
        self.belief.seed_sighting(pos, vel, f.t_meas)
        self.planner.pick = None
        self.pick = None
        self._plan_t = -10.0
        self.confirm.reset()
        self.phase = "SCAN"
        sp = float(np.linalg.norm(vel))
        turn = 1.0 if sp <= 0.5 or wrap_deg(heading_from_vector(*vel) - self.nav.heading) >= 0 else -1.0
        self.scan = {"t0": tc, "turned": 0.0, "turn": turn, "centre": np.asarray(pos, float), "facing": False}
        self.m["losses"] += 1
        self.next_report = self.t
        self.event("lost", f"Lost from view near {label} ({tc - f.t_meas:.1f} s without a sighting). Hovering over "
                           f"where they were last seen and turning full circles, camera 50° down (it sees 10 to 46 m "
                           f"out all round every 6 s), until they are in view again.")

    def _from_police(self, t: float, pos) -> dict | None:
        """Where ``pos`` is from the police car, by the car's latest ping moved on at its speed since."""
        p = self.ping
        if p is None:
            return None
        car = np.array(p["xy"]) + np.array(p["vel"]) * (t - p["t"])
        rel = np.asarray(pos, float) - car
        dist = float(np.linalg.norm(rel))
        brg = heading_from_vector(*rel) % 360.0
        clock = int(round(wrap_deg(brg - p["heading"]) / 30.0)) % 12 or 12
        return {"rel": [round(float(rel[0]), 1), round(float(rel[1]), 1)], "distance_m": round(dist, 1),
                "bearing_deg": round(brg), "compass": compass(brg), "clock": clock,
                "car_base": [round(v, 1) for v in to_base(car)], "ping_age_s": round(t - p["t"], 1)}

    @staticmethod
    def _rel_words(rel: dict) -> tuple[str, str]:
        """('46 m NE of you (your 2 o'clock)', the same as said aloud)."""
        if rel["distance_m"] < 12.0:
            return f"right beside you ({rel['distance_m']:.0f} m, your {rel['clock']} o'clock)", \
                f"right beside you, your {rel['clock']} o'clock"
        return f"{rel['distance_m']:.0f} m {rel['compass']} of you (your {rel['clock']} o'clock)", \
            f"{spoken_m(rel['distance_m'])} {COMPASS_WORDS[rel['compass']]} of you, your {rel['clock']} o'clock"

    def _callout(self, t: float, kind: str, text: str, speech: str, urgent: bool = False) -> None:
        """A message to the police: written (``text``) and as it is spoken (``speech``, read out on the page)."""
        self.callouts.append({"n": len(self.callouts) + 1, "t": round(t, 1), "kind": kind, "text": text,
                              "speech": speech, "urgent": urgent})

    def _report_lost(self, t: float) -> None:
        ls = self.last_seen
        assert ls is not None
        first = t - ls["t"] < LOST_AFTER_S + 1.0  # the first callout after losing them, said straight away
        self.next_report = t + 6.0
        rel = self._from_police(t, ls["xy"])
        where = at_place(ls["label"])
        circles = int(self.scan["turned"] // 360.0) if self.scan is not None else 0
        doing = f"Turning full circles above where they were last seen ({circles} so far)"
        if rel is not None:
            words, said = self._rel_words(rel)
            where += f", {words}"
        text = (f"To police: suspect out of sight for {t - ls['t']:.0f} s; last seen {where} "
                f"({fmt_base(ls['base'])}). {doing}.")
        spoken_where = spoken_place(at_place(ls["label"])) + (f", {said}" if rel is not None else "")
        speech = (f"Lost sight of the suspect {spoken_where}. Scanning." if first else
                  f"Suspect still out of sight. Last seen {spoken_where}.")
        self.event("report", text)
        self._callout(t, "lost", text, speech, urgent=first)

    def _report(self, t: float) -> None:
        f = self.filter
        assert f is not None
        if t - f.t_meas > 0.45:  # only from a fresh sighting (one detector pass plus its latency): try again shortly
            self.next_report = t + 0.2
            return
        self.next_report = t + REPORT_EVERY_S
        pos, vel = f.at(t)
        base = np.array(to_base(pos))
        drone = self.nav.xy.copy()
        off = base - drone
        dist = float(np.linalg.norm(base))
        brg = heading_from_vector(*base) % 360.0
        sp = float(np.linalg.norm(vel))
        err = math.dist(pos, self.suspect.xy)  # scoring only: the true position is never used by the drone
        rep = {"t": round(t, 1), "base": [round(float(base[0]), 1), round(float(base[1]), 1)],
               "xy": [round(float(pos[0]), 1), round(float(pos[1]), 1)],
               "drone_base": [round(float(drone[0]), 1), round(float(drone[1]), 1)],
               "offset": [round(float(off[0]), 1), round(float(off[1]), 1)],
               "distance_m": round(dist, 1), "bearing_deg": round(brg), "compass": compass(brg),
               "speed_mps": round(sp, 1), "moving": compass(heading_from_vector(*vel)) if sp > 0.6 else None,
               "near": self.st.place_label(pos), "error_m": round(err, 2)}
        rel = self._from_police(t, pos)
        rep["police"] = rel
        self.reports.append(rep)
        moving = f", running {rep['moving']} at {sp:.1f} m/s" if rep["moving"] else ", not moving"
        said_moving = f", running {COMPASS_WORDS[rep['moving']]}" if rep["moving"] else ", not moving"
        how = (f"Drone {fmt_base(drone)} by dead reckoning + camera offset {off[0]:+.1f} E, {off[1]:+.1f} N.")
        back = self._refound
        self._refound = False
        if rel is None:  # the first report: the police car is still at the station
            text = (f"To police: suspect at {fmt_base(base)} ({dist:.0f} m, bearing {brg:03.0f} {compass(brg)}) "
                    f"{at_place(rep['near'])}{moving}. {how}")
            speech = f"Suspect located {spoken_place(at_place(rep['near']))}{said_moving}."
        else:
            truth = np.asarray(self.suspect.xy) - np.array(self.police.pose()[:2])  # scoring only
            self.m["callout_err"].append(round(float(np.linalg.norm(np.array(rel["rel"]) - truth)), 2))
            words, said = self._rel_words(rel)
            text = (f"To police: suspect {words}, {at_place(rep['near'])}{moving}. At {fmt_base(base)}; your ping "
                    f"puts you at {fmt_base(rel['car_base'])}. {how}")
            speech = f"Suspect {said}, {spoken_place(at_place(rep['near']))}{said_moving}."
        urgent = back or rel is None
        if back:
            speech = "Back in view. " + speech
        if rel is not None and not self.on_scene and rel["distance_m"] < ON_SCENE_M:
            self.on_scene = True
            self.m["on_scene_t"] = t
            speech, urgent = "Police on scene. " + speech, True
            self.event("police", f"Police car on scene, {rel['distance_m']:.0f} m from the suspect. The drone keeps "
                                 f"following and calling out where they are from the car.")
        elif rel is not None and rel["distance_m"] < 10.0 and t - self._alongside_t > 20.0:
            self._alongside_t = t
            self.m["alongside"] += 1
            self.event("police", f"Police car alongside the suspect ({rel['distance_m']:.0f} m).")
        self.event("report", text)
        if self.police.state == "PARKED":
            self.police.dispatch(t, pos)
            speech += " Police car dispatched."
            self.event("police", "Police car dispatched from the station to the reported position (catching up in "
                                 "about 20 s). It pings its position to the drone every second; from now on each "
                                 "report says where the suspect is from the car.")
        else:  # where they will be by the time the car gets there, up to 3 s ahead
            lead = float(np.clip((rel["distance_m"] if rel else 40.0) / 14.0, 0.0, 3.0))
            self.police.replan(np.asarray(pos, float) + vel * lead)
        self._callout(t, "position", text, speech, urgent)

    def _finish(self, outcome: str, text: str) -> None:
        self.end_phase = self.phase
        self.phase = outcome
        self.outcome = outcome
        self.done = True
        self.event("end", text)

    # ------------------------------------------------------------------------------------------------------------
    def hud_items(self, view: str = "drone") -> dict:
        """The overlay for the current frame as plain data (``hud.draw_hud`` draws it, in any process). The drone's
        camera: the latest detections pinned to their ground spots and drawn where those are now, the tracker's
        bracket on the followed person, the police light bar, and two lines of status. A simulator view: what the
        drone's camera covers on the ground, the drone, the tracker's target, the police car, and the drone camera's
        overlay scaled for the inset."""
        own = self._hud_drone()
        if view not in ("chase", "top"):
            return own
        d = self.drone
        cam = self.views.camera(view, d)

        def px(p3) -> tuple[float, float] | None:
            uv, z = cam.project(np.asarray(p3, float)[None, :])
            return (float(uv[0, 0]), float(uv[0, 1])) if z[0] > 0.3 else None

        polys, tags, brackets = [], [], []
        if d.pos[2] > 5.0:
            fp = true_camera(d).footprint(55.0)
            pts = [px((x, y, 0.0)) for x, y in fp] if fp else []
            if pts and all(p is not None for p in pts):
                polys.append((pts, (240, 200, 76), 2, "drone camera's view (to 55 m)"))
        dp = px(d.pos)
        if dp is not None:
            if view == "top":
                tags.append((dp[0], dp[1], (240, 200, 76), f"DRONE  {self.nav.alt:.0f} m", "drone", float(d.heading)))
            else:
                tags.append((dp[0], dp[1], (240, 200, 76), "DJI Mini 2 (to scale)", "right", 0.0))
        if self.phase == "FOLLOW" and self.filter is not None:
            pos, _ = self.filter.at(self.t)
            a, b = px((pos[0], pos[1], 0.0)), px((pos[0], pos[1], 1.8))
            if a is not None and b is not None:
                h = max(abs(a[1] - b[1]), 22.0)
                w = max(h * 0.62, 22.0)
                cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                brackets.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, (60, 220, 90), "TARGET (tracker)"))
        if self.police.state != "PARKED":
            cx_, cy_, _ = self.police.pose()
            q = px((cx_, cy_, 1.6))
            if q is not None:
                tags.append((q[0], q[1] - 22.0, (255, 170, 90), "POLICE", "label", 0.0))
        if view == "top":
            q = px((self.suspect.xy[0], self.suspect.xy[1], 0.0))
            if q is not None:
                tags.append((q[0], q[1], (90, 90, 255), "suspect (sim truth)", "truth", 0.0))
        k = INSET.width / CAMERA.width
        inset = {"boxes": [(x0 * k, y0 * k, x1 * k, y1 * k, col, 1, "") for x0, y0, x1, y1, col, _, _ in own["boxes"]],
                 "brackets": [(x0 * k, y0 * k, x1 * k, y1 * k, col, "") for x0, y0, x1, y1, col, _ in own["brackets"]],
                 "texts": []}
        top = (f"SIMULATED  {VIEW_NAMES[view]} (a simulator camera, not the drone's)  t {self.t:5.1f}s  "
               f"{PHASE_LABEL.get(self.phase, self.phase)}")
        bot = f"drone alt {self.nav.alt:4.1f} m  heading {self.nav.heading:03.0f}  gimbal {d.gimbal:5.1f} deg"
        last = self.reports[-1] if self.reports else None
        if last is not None and last.get("police") and self.t - last["t"] < 6.0:
            rel = last["police"]
            bot += f"   suspect {rel['distance_m']:.0f} m {rel['compass']} of the police car"
        return {"boxes": [], "brackets": brackets, "polys": polys, "tags": tags, "texts": [top, bot], "inset": inset}

    def _hud_drone(self) -> dict:
        cam = self.nav.camera(PAD)

        def pinned(xy, z_top: float, aspect: float):
            uv, z = cam.project(np.array([[xy[0], xy[1], 0.0], [xy[0], xy[1], z_top]]))
            if not (z > 0.5).all():
                return None
            h = abs(uv[0, 1] - uv[1, 1])
            cx = (uv[0, 0] + uv[1, 0]) / 2
            w = max(h * aspect, 6.0)
            return cx - w / 2, min(uv[0, 1], uv[1, 1]), cx + w / 2, max(uv[0, 1], uv[1, 1])

        boxes, brackets = [], []
        fd = self.last_det
        if fd is not None and self.t - fd.t < 0.45:
            for p in fd.people:
                if p.xy is None:
                    continue
                x0, y0, x1, y1 = p.box
                b = pinned(p.xy, 1.75, (x1 - x0) / max(y1 - y0, 1.0))
                if b is not None:
                    col = (40, 40, 235) if p.is_red else (205, 205, 205)
                    boxes.append((*b, col, 2 if p.is_red else 1, f"{'red shirt' if p.is_red else 'person'} {p.conf:.2f}"))
            for bar in self.last_bars:
                if bar.xy is not None:
                    b = pinned(bar.xy, 1.75, 2.2)
                    if b is not None:
                        boxes.append((b[0] - 10, b[1] - 6, b[2] + 10, b[3] + 6, (235, 140, 40), 2, "police light bar"))
        if self.phase == "FOLLOW" and self.filter is not None:
            pos, _ = self.filter.at(self.t)
            b = pinned(pos, 1.8, 0.62)
            if b is not None:
                pad = 6.0
                brackets.append((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad, (60, 220, 90), "TARGET (tracker)"))
        mode = {"full": "full frame", "crop": "crop on target"}.get(fd.mode if fd else "", "")
        top = (f"SIMULATED  DJI Mini 2  720p {FPS} fps  YOLO26n {FPS // DETECT_FRAMES} Hz{' ' + mode if mode else ''}"
               f"  t {self.t:5.1f}s  {PHASE_LABEL.get(self.phase, self.phase)}")
        bot = (f"alt {self.nav.alt:4.1f} m  gimbal {self.drone.gimbal:5.1f} deg  heading {self.nav.heading:03.0f}  "
               f"dead reckoning {fmt_base(self.nav.xy)}")
        return {"boxes": boxes, "brackets": brackets, "texts": [top, bot]}

    def state(self) -> dict:
        d = self.drone
        cam = self.nav.camera(PAD)
        fp = cam.footprint(55.0) if self.nav.alt > 5 else None
        f = self.filter
        target = None
        if f is not None:
            pos, vel = f.at(self.t)
            target = {"xy": [round(float(pos[0]), 1), round(float(pos[1]), 1)], "base": [round(v, 1) for v in to_base(pos)],
                      "fresh": self.phase == "FOLLOW" and self.t - f.t_meas < 1.5}
        pol = self.police
        px, py, ph = pol.pose()
        sus = self.suspect
        return {
            "seed": self.seed, "t": round(self.t, 2), "phase": self.phase, "phase_label": PHASE_LABEL.get(self.phase, self.phase),
            "done": self.done, "outcome": self.outcome,
            "crime": {"xy": [round(float(v), 1) for v in self.crime_xy], "label": self.crime_label,
                      "head_start_s": round(self.head_start, 1)},
            "drone": {"xy": [round(float(v), 1) for v in self.nav_xy()], "base": [round(float(v), 1) for v in self.nav.xy],
                      "alt": round(self.nav.alt, 1), "heading": round(self.nav.heading), "gimbal": round(d.gimbal, 1),
                      "speed": round(float(np.linalg.norm(d.vel[:2])), 1),
                      "dr_error_m": round(float(np.linalg.norm(self.nav_xy() - d.pos[:2])), 3),
                      "footprint": [[round(x, 1), round(y, 1)] for x, y in fp] if fp else None, "trail": self.trail[-600:]},
            "pick": {"target": [round(float(v), 1) for v in self.pick.target], "viewpoint": [round(float(v), 1) for v in self.pick.viewpoint],
                     "label": self.pick.label} if self.pick is not None and self.phase == "SEARCH" else None,
            "target": target,
            "last_seen": self.last_seen if self.phase == "SCAN" else None,
            "scan": {"centre": [round(float(v), 1) for v in self.scan["centre"]], "turned": round(self.scan["turned"] % 360.0),
                     "circle": int(self.scan["turned"] // 360.0) + 1, "facing": self.scan["facing"]}
            if self.scan is not None else None,
            "reports": self.reports[-40:],
            "police": {"xy": [round(px, 1), round(py, 1)], "heading": round(ph), "state": pol.state,
                       "eta_s": None if pol.eta_s(self.t) is None else round(pol.eta_s(self.t), 1),
                       "route": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in pol.route],
                       "dispatched_t": pol.dispatched_t, "on_scene": self.on_scene,
                       "ping": None if self.ping is None else {
                           "xy": [round(float(v), 1) for v in self.ping["xy"]],
                           "base": [round(v, 1) for v in to_base(self.ping["xy"])], "heading": round(self.ping["heading"]),
                           "speed": round(self.ping["speed"], 1), "age_s": round(self.t - self.ping["t"], 1)}},
            "callouts": [{k: v for k, v in c.items() if k != "text"} for c in self.callouts[-12:]],
            "spawn_picked": self.spawn_picked,
            "time_limit_s": None if math.isinf(self.time_limit) else self.time_limit,
            "belief": self.belief.view() if self.phase in ("REPORTED", "TAKEOFF", "SEARCH", "SCAN") else [],
            "survey": self.survey.encode(self.t),
            "truth": {"suspect": [round(float(sus.xy[0]), 1), round(float(sus.xy[1]), 1)], "suspect_state": sus.state,
                      "suspect_visible": round(self.truth_visible, 2),
                      "bystanders": [[round(float(b.actor.pos[0]), 1), round(float(b.actor.pos[1]), 1)] for b in self.bystanders]},
            "detector": {"model": "YOLO26n (COCO, pretrained) + torso colour; police car: light-bar colour detector",
                         "people": len(self.last_det.people) if self.last_det else 0,
                         "mode": self.last_det.mode if self.last_det else None},
            "events": self.events[-60:],
            "summary": self.summary() if self.done else None,
        }

    def summary(self, detector_stats: dict | None = None) -> dict:
        m = self.m
        errs = [r["error_m"] for r in self.reports]
        walk_seen = self.survey.seen_walk_m2()
        since = self.t - m["match_t"] if m["match_t"] is not None else 0.0

        def q(xs, p):
            return round(float(np.percentile(xs, p)), 1) if xs else None
        pol = self.police
        return {
            "seed": self.seed, "outcome": self.outcome, "duration_s": round(self.t, 1),
            "crime_near": self.crime_label, "spawn_picked": self.spawn_picked, "head_start_s": round(self.head_start, 1),
            "first_true_sighting_s": None if m["first_true_red_t"] is None else round(m["first_true_red_t"], 1),
            "matched_s": None if m["match_t"] is None else round(m["match_t"], 1),
            "match_error_m": m["match_error_m"], "wrong_matches": m["wrong_matches"],
            "following_pct": round(100 * m["follow_frames"] / FPS / since, 1) if since > 0 else None,
            "follow_dist_median_m": q(m["follow_dist"], 50), "follow_dist_p90_m": q(m["follow_dist"], 90),
            "follow_dist_max_m": q(m["follow_dist"], 100),
            "reports": len(self.reports), "report_error_mean_m": round(float(np.mean(errs)), 2) if errs else None,
            "report_error_max_m": round(float(np.max(errs)), 2) if errs else None,
            "callouts": len(self.callouts),
            "callout_error_mean_m": round(float(np.mean(m["callout_err"])), 2) if m["callout_err"] else None,
            "callout_error_max_m": round(float(np.max(m["callout_err"])), 2) if m["callout_err"] else None,
            "police_dispatched_s": None if pol.dispatched_t is None else round(pol.dispatched_t, 1),
            "police_on_scene_s": None if m["on_scene_t"] is None else round(m["on_scene_t"], 1),
            "police_dispatch_to_scene_s": round(m["on_scene_t"] - pol.dispatched_t, 1)
            if m["on_scene_t"] is not None and pol.dispatched_t is not None else None,
            "police_gap_median_m": q(m["police_gap"], 50), "police_alongside": m["alongside"],
            "police_distance_m": round(pol.distance_m),
            "losses": m["losses"], "reacquired": m["reacquired"],
            "refind_median_s": q(m["refind"], 50), "refind_max_s": q(m["refind"], 100),
            "lost_at_end": getattr(self, "end_phase", self.phase) == "SCAN",
            "suspect_hides": self.suspect.hides,
            "drone_distance_m": round(self.drone.distance_m, 0),
            "dead_reckoning_error_m": round(float(np.linalg.norm(self.nav_xy() - self.drone.pos[:2])), 3),
            "sidewalk_surveyed_m2": round(walk_seen), "sidewalk_surveyed_pct": round(100 * walk_seen / self.survey.walk_area_m2, 1),
            "search_revisit_pct": round(100 * m["survey_recent"] / m["survey_seen"], 1) if m["survey_seen"] else None,
            "planner_picks": self.planner.picks, "detector_passes": self.detect_n,
            "light_bar_frames": m["light_bar_frames"], **(detector_stats or {}),
        }
