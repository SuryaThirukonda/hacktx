"""Following the suspect: the position filter, the match rule and the two flight modes.

* Filter: constant velocity on the ground (x, y, vx, vy). Detections arrive 0.2 s after their frame (the detector's
  latency), so each one is applied at the time its frame was taken and the filter is predicted forward for control.
* Match: three red-shirt detections within 2.5 s, all within 6 m of each other, before the drone commits.
* Follow: 18 m behind the person (along their direction of travel; when they stand still, on the side the drone is
  already on) at 28 m, flying their velocity plus 1.1 times the gap to that spot each second, up to the aircraft's
  15 m/s, so it closes a gap quickly instead of trailing; the aircraft yaws to face them and the gimbal pitches to
  keep them in the middle of the frame (the geometry, corrected by where the person actually is in the image).
* Scan: after losing them, hover over where they were last seen and turn full circles at 60 deg/s with the camera
  50 deg down (from 30 m that sees 10 to 46 m out, all round, every 6 s) until they are back in view; it stops
  turning to face anything red it just saw.
* Search flight: to a viewpoint at up to 13 m/s (15 m/s in transit), 30 m up, the camera 50 deg down (straight down
  above a truck or kiosk someone may be crouching behind) and yawed at what it is looking for.
"""

from __future__ import annotations

import math

import numpy as np

from overwatch.geometry.camera import heading_from_vector, wrap_deg

from .drone import CAMERA, MAX_H, DeadReckoning, Stick

FOLLOW_ALT, FOLLOW_BACK = 28.0, 18.0
FOLLOW_GAIN = 1.1  # per second, on the gap to the spot behind them, on top of flying their velocity
SEARCH_ALT, SEARCH_PITCH = 30.0, -50.0
SCAN_RATE = 60.0  # deg/s: a full circle in 6 s, every direction in the frame for over a second (6 detector passes)


class TargetFilter:
    def __init__(self, xy, vel, t: float) -> None:
        self.x = np.array([xy[0], xy[1], vel[0], vel[1]], float)
        self.P = np.diag([1.0, 1.0, 9.0, 9.0])  # a velocity from three quick sightings is rough: let the next correct it
        self.t = t
        self.t_meas = t
        self.n = 1

    @staticmethod
    def _FQ(dt: float) -> tuple[np.ndarray, np.ndarray]:
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = 4.0  # (2 m/s^2)^2: a runner can stop or turn
        a, b, c = dt ** 4 / 4, dt ** 3 / 2, dt ** 2
        Q = q * np.array([[a, 0, b, 0], [0, a, 0, b], [b, 0, c, 0], [0, b, 0, c]])
        return F, Q

    def predict_to(self, t: float) -> None:
        dt = t - self.t
        if dt <= 0:
            return
        F, Q = self._FQ(dt)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        self.t = t

    def at(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        """Predicted position and velocity at ``t`` without changing the filter."""
        dt = max(0.0, t - self.t)
        return self.x[:2] + self.x[2:] * dt, self.x[2:].copy()

    def update(self, xy, t: float, sigma: float = 0.7) -> None:
        self.predict_to(t)
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1.0
        S = H @ self.P @ H.T + np.eye(2) * sigma ** 2
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (np.asarray(xy, float) - H @ self.x)
        self.P = (np.eye(4) - K @ H) @ self.P
        self.t_meas = t
        self.n += 1
        sp = float(np.linalg.norm(self.x[2:]))
        if sp > 7.0:  # nobody on foot runs faster than this
            self.x[2:] *= 7.0 / sp


class Confirmer:
    def __init__(self) -> None:
        self.hits: list[tuple[float, np.ndarray]] = []

    def add(self, t: float, xy) -> tuple[np.ndarray, np.ndarray] | None:
        """Returns (position, velocity) once three red-shirt sightings within 2.5 s agree to 6 m."""
        p = np.asarray(xy, float)
        self.hits = [(th, q) for th, q in self.hits if t - th <= 2.5 and float(np.linalg.norm(q - p)) < 6.0 + 7.0 * (t - th)]
        self.hits.append((t, p))
        if len(self.hits) >= 3:
            ts = np.array([h[0] for h in self.hits])
            ps = np.array([h[1] for h in self.hits])
            if ts[-1] - ts[0] < 0.2:
                return p, np.zeros(2)
            vel = np.polyfit(ts - ts[-1], ps, 1)[0]  # least-squares line through the sightings, not two endpoints
            sp = float(np.linalg.norm(vel))
            if sp > 7.0:
                vel *= 7.0 / sp
            return p, vel
        return None

    def reset(self) -> None:
        self.hits.clear()


def _to_body(v_world: np.ndarray, heading: float) -> tuple[float, float]:
    h = math.radians(heading)
    return float(v_world[0] * math.sin(h) + v_world[1] * math.cos(h)), \
        float(v_world[0] * math.cos(h) - v_world[1] * math.sin(h))


def _yaw_to(heading: float, frm, to) -> float:
    want = heading_from_vector(to[0] - frm[0], to[1] - frm[1])
    return float(np.clip(2.2 * wrap_deg(want - heading), -90.0, 90.0))


def follow_command(nav: DeadReckoning, pad, target_xy: np.ndarray, target_v: np.ndarray,
                   box_v: float | None) -> tuple[Stick, float]:
    me = np.asarray(pad, float) + nav.xy
    sp = float(np.linalg.norm(target_v))
    if sp > 0.8:
        back = -target_v / sp
    else:
        d = me - target_xy
        n = float(np.linalg.norm(d))
        back = d / n if n > 1e-3 else np.array([0.0, -1.0])
    goal = target_xy + back * FOLLOW_BACK
    v = target_v + FOLLOW_GAIN * (goal - me)
    n = float(np.linalg.norm(v))
    if n > MAX_H:
        v *= MAX_H / n
    fwd, right = _to_body(v, nav.heading)
    up = float(np.clip(0.8 * (FOLLOW_ALT - nav.alt), -3.0, 3.0))
    yaw = _yaw_to(nav.heading, me, target_xy)
    horiz = float(np.linalg.norm(target_xy - me))
    gimbal = -math.degrees(math.atan2(nav.alt - 1.0, max(horiz, 1.0)))
    if box_v is not None:  # where the person really is in the frame: nudge the gimbal to centre them
        err = math.degrees(math.atan((box_v - CAMERA.cy) / CAMERA.fy))
        gimbal -= 0.5 * err
    # no shallower than 40 deg down: a person up to ~85 m away still fits in the frame, YOLO is surer at steeper
    # angles, and a camera looking at the horizon would draw (and search) half the town for nothing
    return Stick(fwd, right, up, yaw), float(np.clip(gimbal, -90.0, -40.0))


def goto_command(nav: DeadReckoning, pad, viewpoint: np.ndarray, look_at: np.ndarray,
                 alt: float = SEARCH_ALT, pitch: float = SEARCH_PITCH) -> tuple[Stick, float]:
    me = np.asarray(pad, float) + nav.xy
    to = viewpoint - me
    d = float(np.linalg.norm(to))
    vmax = 15.0 if d > 80.0 else 13.0  # the Mini 2's virtual-stick cap in transit, a little less while looking
    v = to / d * min(vmax, 0.8 * d + 0.3) if d > 0.4 else np.zeros(2)
    fwd, right = _to_body(v, nav.heading)
    up = float(np.clip(0.8 * (alt - nav.alt), -3.0, 4.0))
    yaw = _yaw_to(nav.heading, me, look_at) if float(np.linalg.norm(look_at - me)) > 3.0 else 0.0
    return Stick(fwd, right, up, yaw), pitch


def scan_command(nav: DeadReckoning, pad, centre: np.ndarray, turn: float,
                 face: np.ndarray | None = None) -> tuple[Stick, float]:
    """The look round after losing them: hold over ``centre`` at search height and turn on the spot at SCAN_RATE
    (``turn`` +1 clockwise, -1 anticlockwise) with the camera 50 deg down; when a red shirt was just seen (``face``),
    stop turning and face it so the next detector passes can confirm it."""
    me = np.asarray(pad, float) + nav.xy
    to = np.asarray(centre, float) - me
    d = float(np.linalg.norm(to))
    v = to / d * min(10.0, 0.9 * d) if d > 0.5 else np.zeros(2)
    fwd, right = _to_body(v, nav.heading)
    up = float(np.clip(0.8 * (SEARCH_ALT - nav.alt), -3.0, 3.0))
    if face is not None and float(np.linalg.norm(np.asarray(face, float) - me)) > 3.0:
        yaw = _yaw_to(nav.heading, me, face)
    else:
        yaw = SCAN_RATE * turn
    return Stick(fwd, right, up, yaw), SEARCH_PITCH
