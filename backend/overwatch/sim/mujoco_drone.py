"""Rigid-body quadrotor for the simulator: the Skydio X2 from MuJoCo Menagerie (the jev-drone setup), flown by a
cascaded controller so it accepts exactly the same virtual-stick setpoints as the kinematic Mini 2 stand-in.

    velocity setpoint (world) -> desired acceleration -> thrust vector + yaw -> desired attitude (geometric
    SO(3) controller, Lee et al. 2010) -> body torques -> per-rotor thrusts (exact allocation for the X2 layout)

Everything else the pipeline reads (telemetry, GPS / baro noise, battery, gimbal, camera pose) comes from the
parent class, so the perception and autonomy stacks cannot tell which model is flying. Wind gusts are applied as
a real force on the airframe instead of the kinematic model's drift offset.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..schemas import VStickCommand
from .drone_dynamics import DroneParams, SimDrone

SCENE = Path(__file__).resolve().parents[2] / "third_party" / "skydio_x2" / "overwatch_scene.xml"
G = 9.81


class MujocoDrone(SimDrone):
    model_name = "Skydio X2 (MuJoCo Menagerie)"
    PHYS_DT = 0.004  # 250 Hz physics + attitude loop
    MAX_TILT = math.radians(25.0)  # camera drones cruise gently (the Mini 2 tilts ~20-25 deg in normal mode)
    WIND_DRAG = 0.35  # N per m/s of gust speed
    BODY_DRAG = 0.6  # N per m/s of airspeed: makes cruise tilt like a real airframe (~10 deg at 4 m/s)

    def __init__(self, x: float, y: float, alt: float, heading: float, gimbal_pitch: float = -45.0,
                 seed: int = 5, params: DroneParams | None = None) -> None:
        super().__init__(x, y, alt, heading, gimbal_pitch, seed, params)
        import mujoco

        self.mj = mujoco
        self.model = mujoco.MjModel.from_xml_path(str(SCENE))
        self.data = mujoco.MjData(self.model)
        self.body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        self.gyro_adr = self.model.sensor_adr[mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SENSOR, "body_gyro")]
        psi = math.radians(90.0 - heading)  # our heading is clockwise from north (+y); MuJoCo yaw is CCW from +x
        self.data.qpos[0:3] = (x, y, max(alt, 0.12))
        self.data.qpos[3:7] = (math.cos(psi / 2), 0.0, 0.0, math.sin(psi / 2))
        mujoco.mj_forward(self.model, self.data)
        self.mass = float(self.model.body_subtreemass[self.body])
        self.J = np.array(self.model.body_inertia[self.body], float)
        # allocation: [F, tau_x, tau_y, tau_z] = A @ f from the actuator sites (thrust along body z) and yaw gears
        rows = []
        for i in range(self.model.nu):
            site = self.model.actuator_trnid[i, 0]
            px, py = self.model.site_pos[site][:2] - self.model.body_ipos[self.body][:2]
            yaw = self.model.actuator_gear[i, 5]
            rows.append([1.0, py, -px, yaw])
        self.A = np.array(rows, float).T
        self.A_inv = np.linalg.inv(self.A)
        self.f_max = float(self.model.actuator_ctrlrange[0, 1])
        self.thrusts = np.full(self.model.nu, self.mass * G / self.model.nu)
        self.psi_des = psi
        self.wind_offset = np.zeros(2)
        self._sync()

    # ---------------------------------------------------------------------------------------------------------
    def step(self, dt: float, cmd: VStickCommand | None, wind_gusts: bool = False) -> None:
        if dt <= 0:
            return
        self.t += dt
        p = self.p
        target_v, target_vz, target_yaw = self._setpoints(cmd)
        sigma = 3.0 if wind_gusts else 0.8
        self.wind += (-self.wind / 1.0) * dt + self.rng.normal(0, sigma * math.sqrt(dt), 2)
        n = max(1, math.ceil(dt / self.PHYS_DT - 1e-9))
        self.model.opt.timestep = dt / n
        for _ in range(n):
            self._control(target_v, target_vz, target_yaw, dt / n)
            self.mj.mj_step(self.model, self.data)
        self._sync()
        self.gimbal_pitch += float(np.clip(self.gimbal_target - self.gimbal_pitch, -p.gimbal_rate_dps * dt, p.gimbal_rate_dps * dt))
        speed = float(np.linalg.norm(self.vel[:2]))
        self.battery = max(0.0, self.battery - dt * (100.0 / (p.battery_minutes * 60.0)) * (1.0 + 0.04 * speed))
        self.gps_err += self.rng.normal(0, p.gps_sigma_m * math.sqrt(dt), 2) - self.gps_err * 0.05 * dt
        self.gps_err = np.clip(self.gps_err, -p.gps_bound_m, p.gps_bound_m)
        self.baro_err = float(np.clip(self.baro_err + self.rng.normal(0, 0.03 * math.sqrt(dt)), -p.baro_drift_m, p.baro_drift_m))
        self.flying = self.pos[2] > 0.3

    def _control(self, v_xy: np.ndarray, v_z: float, yaw_rate_dps: float, h: float) -> None:
        d = self.data
        R = d.xmat[self.body].reshape(3, 3)  # body -> world
        vel = d.qvel[0:3]
        omega = d.sensordata[self.gyro_adr:self.gyro_adr + 3]  # body frame
        # outer loop: velocity -> desired acceleration (bounded)
        a = np.zeros(3)
        a[:2] = 2.0 * (np.asarray(v_xy) - vel[:2])
        n = float(np.linalg.norm(a[:2]))
        if n > 3.5:
            a[:2] *= 3.5 / n
        a[2] = float(np.clip(3.0 * (v_z - vel[2]), -4.0, 4.0))
        # gusts and airframe drag act on the body; the controller feeds forward the drag of the commanded velocity
        # and sees gusts only through their effect on velocity
        d.xfrc_applied[self.body, 0:2] = self.WIND_DRAG * self.wind - self.BODY_DRAG * vel[:2]
        d.xfrc_applied[self.body, 2] = -self.BODY_DRAG * vel[2]
        a[:2] += self.BODY_DRAG / self.mass * np.asarray(v_xy)
        a[2] += self.BODY_DRAG / self.mass * v_z
        f_des = self.mass * (a + np.array([0.0, 0.0, G]))
        # tilt limit
        tilt = math.atan2(float(np.linalg.norm(f_des[:2])), float(f_des[2]))
        if tilt > self.MAX_TILT:
            horiz = f_des[:2] / max(1e-9, float(np.linalg.norm(f_des[:2])))
            f_des[:2] = horiz * f_des[2] * math.tan(self.MAX_TILT)
        # yaw setpoint integrates the commanded rate, kept near the actual yaw so it cannot wind up
        psi = math.atan2(R[1, 0], R[0, 0])
        yr = -math.radians(yaw_rate_dps)  # ours: clockwise positive; MuJoCo: counter-clockwise positive
        self.psi_des += yr * h
        err = math.atan2(math.sin(self.psi_des - psi), math.cos(self.psi_des - psi))
        if abs(err) > 0.5:
            self.psi_des = psi + math.copysign(0.5, err)
        zb = f_des / float(np.linalg.norm(f_des))
        xc = np.array([math.cos(self.psi_des), math.sin(self.psi_des), 0.0])
        yb = np.cross(zb, xc)
        yb /= float(np.linalg.norm(yb))
        xb = np.cross(yb, zb)
        Rd = np.column_stack([xb, yb, zb])
        eR_m = 0.5 * (Rd.T @ R - R.T @ Rd)
        eR = np.array([eR_m[2, 1], eR_m[0, 2], eR_m[1, 0]])
        e_om = omega - np.array([0.0, 0.0, yr])
        # roll/pitch stiff, yaw gentle: yaw torque per newton is tiny on a quad, so a stiff yaw loop saturates rotors
        wn = np.array([13.0, 13.0, 4.0])
        zeta = np.array([0.9, 0.9, 1.0])
        tau = -self.J * wn * wn * eR - self.J * 2 * zeta * wn * e_om + np.cross(omega, self.J * omega)
        F = float(f_des @ R[:, 2])
        # mixer with yaw last: thrust, roll and pitch first, then as much yaw torque as the remaining headroom allows
        # (yaw authority on a quad is small; saturating a rotor for yaw costs attitude control)
        f_base = self.A_inv @ np.array([F, tau[0], tau[1], 0.0])
        f_yaw = self.A_inv @ np.array([0.0, 0.0, 0.0, tau[2]])
        scale = 1.0
        for fb, fy in zip(f_base, f_yaw):  # yaw may use 70 % of each rotor's headroom, never all of it
            if fy > 1e-9:
                scale = min(scale, max(0.0, 0.7 * (self.f_max - fb) / fy))
            elif fy < -1e-9:
                scale = min(scale, max(0.0, 0.7 * fb / -fy))
        self.thrusts = np.clip(f_base + scale * f_yaw, 0.0, self.f_max)
        d.ctrl[:] = self.thrusts

    def _sync(self) -> None:
        d = self.data
        R = d.xmat[self.body].reshape(3, 3)
        self.pos = np.array(d.qpos[0:3], float)
        self.vel = np.array(d.qvel[0:3], float)
        psi = math.atan2(R[1, 0], R[0, 0])
        self.heading = (90.0 - math.degrees(psi)) % 360.0
        self.yaw_rate = -math.degrees(float(d.sensordata[self.gyro_adr + 2]))
        self.pitch = math.degrees(math.asin(float(np.clip(R[2, 0], -1, 1))))  # nose down -> negative
        self.roll = math.degrees(math.asin(float(np.clip(R[2, 1], -1, 1))))  # right side down -> positive

    # ---------------------------------------------------------------------------------------------------------
    def quat_wxyz(self) -> list[float]:
        return [round(float(v), 5) for v in self.data.qpos[3:7]]

    def rotor_thrusts(self) -> list[float]:
        return [round(float(v), 3) for v in self.thrusts]
