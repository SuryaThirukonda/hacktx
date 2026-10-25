"""Kinematic DJI Mini 2 stand-in: first-order velocity response to virtual-stick commands, wind gusts (OU process),
GPS random walk bounded at the Mini 2 hover spec, barometric drift, battery drain, gimbal rate limits."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..geometry.camera import CameraPose, wrap_deg
from ..geometry.geo import local_to_latlon
from ..schemas import DroneTelemetry, VStickCommand


@dataclass
class DroneParams:
    tau_v: float = 0.55
    tau_vz: float = 0.6
    tau_yaw: float = 0.3
    sdk_max_h: float = 15.0
    sdk_max_yaw: float = 100.0
    sdk_max_vz: float = 4.0
    gimbal_rate_dps: float = 60.0
    battery_minutes: float = 31.0
    gps_sigma_m: float = 0.25
    gps_bound_m: float = 1.5
    baro_drift_m: float = 0.5
    rtl_speed: float = 5.0
    rtl_alt: float = 30.0


class SimDrone:
    def __init__(self, x: float, y: float, alt: float, heading: float, gimbal_pitch: float = -45.0,
                 seed: int = 5, params: DroneParams | None = None) -> None:
        self.p = params or DroneParams()
        self.pos = np.array([x, y, alt], float)
        self.home = np.array([x, y, 0.0])
        self.vel = np.zeros(3)
        self.heading = heading
        self.yaw_rate = 0.0
        self.gimbal_pitch = gimbal_pitch
        self.gimbal_target = gimbal_pitch
        self.battery = 92.0
        self.flying = alt > 0.5
        self.vstick_enabled = False
        self.mode = "P-GPS"
        self.rng = np.random.default_rng(seed)
        self.wind = np.zeros(2)
        self.wind_offset = np.zeros(2)
        self.gps_err = np.zeros(2)
        self.baro_err = 0.0
        self.wobble_phase = self.rng.uniform(0, 2 * math.pi)
        self.pitch = 0.0
        self.roll = 0.0
        self.t = 0.0

    # ---------------------------------------------------------------------------------------------------------
    def _setpoints(self, cmd: VStickCommand | None) -> tuple[np.ndarray, float, float]:
        """World-frame horizontal velocity, vertical speed and yaw rate the flight controller is asked for
        (RTL, virtual stick, or hover). Shared by the kinematic and the MuJoCo models."""
        p = self.p
        h = math.radians(self.heading)
        fwd = np.array([math.sin(h), math.cos(h)])
        right = np.array([math.cos(h), -math.sin(h)])
        target_v = np.zeros(2)
        target_vz = 0.0
        target_yaw = 0.0
        if self.mode == "RTL":
            to_home = self.home[:2] - self.pos[:2]
            d = float(np.linalg.norm(to_home))
            if self.pos[2] < p.rtl_alt - 0.5 and d > 2:
                target_vz = 2.0
            elif d > 0.5:
                target_v = to_home / d * min(p.rtl_speed, d)
            else:
                target_vz = -1.5 if self.pos[2] > 0.3 else 0.0
        elif self.vstick_enabled and cmd is not None:
            fwd_c = float(np.clip(cmd.forward_mps, -p.sdk_max_h, p.sdk_max_h))
            right_c = float(np.clip(cmd.right_mps, -p.sdk_max_h, p.sdk_max_h))
            target_v = fwd * fwd_c + right * right_c
            target_vz = float(np.clip(cmd.up_mps, -p.sdk_max_vz, p.sdk_max_vz))
            target_yaw = float(np.clip(cmd.yaw_rate_dps, -p.sdk_max_yaw, p.sdk_max_yaw))
        return target_v, target_vz, target_yaw

    def step(self, dt: float, cmd: VStickCommand | None, wind_gusts: bool = False) -> None:
        if dt <= 0:
            return
        self.t += dt
        p = self.p
        h = math.radians(self.heading)
        fwd = np.array([math.sin(h), math.cos(h)])
        right = np.array([math.cos(h), -math.sin(h)])
        target_v, target_vz, target_yaw = self._setpoints(cmd)
        prev_v = self.vel[:2].copy()
        self.vel[:2] += (target_v - self.vel[:2]) * min(1.0, dt / p.tau_v)
        self.vel[2] += (target_vz - self.vel[2]) * min(1.0, dt / p.tau_vz)
        self.yaw_rate += (target_yaw - self.yaw_rate) * min(1.0, dt / p.tau_yaw)

        # wind: OU process; the position controller rejects most of it, leaving a slow drift offset
        sigma = 3.0 if wind_gusts else 0.8
        self.wind += (-self.wind / 1.0) * dt + self.rng.normal(0, sigma * math.sqrt(dt), 2)
        self.wind_offset += (self.wind * 0.08 - self.wind_offset * 0.6) * dt

        self.pos[:2] += self.vel[:2] * dt
        self.pos[2] = max(0.0, self.pos[2] + self.vel[2] * dt)
        self.heading = (self.heading + self.yaw_rate * dt) % 360.0
        self.gimbal_pitch += float(np.clip(self.gimbal_target - self.gimbal_pitch, -p.gimbal_rate_dps * dt, p.gimbal_rate_dps * dt))

        acc = (self.vel[:2] - prev_v) / dt
        self.pitch = -float(np.dot(acc, fwd)) * 2.0 - float(np.dot(self.vel[:2], fwd)) * 1.2
        self.roll = float(np.dot(acc, right)) * 2.0 + float(np.dot(self.vel[:2], right)) * 1.2
        speed = float(np.linalg.norm(self.vel[:2]))
        self.battery = max(0.0, self.battery - dt * (100.0 / (p.battery_minutes * 60.0)) * (1.0 + 0.04 * speed))
        self.gps_err += self.rng.normal(0, p.gps_sigma_m * math.sqrt(dt), 2) - self.gps_err * 0.05 * dt
        self.gps_err = np.clip(self.gps_err, -p.gps_bound_m, p.gps_bound_m)
        self.baro_err = float(np.clip(self.baro_err + self.rng.normal(0, 0.03 * math.sqrt(dt)), -p.baro_drift_m, p.baro_drift_m))
        self.flying = self.pos[2] > 0.3

    # ---------------------------------------------------------------------------------------------------------
    def camera_pose(self, jitter_deg: float = 0.25) -> CameraPose:
        """True camera pose (3-axis gimbal: pitch from gimbal, yaw from aircraft, small residual jitter)."""
        t = self.t
        jp = jitter_deg * (math.sin(2 * math.pi * 1.3 * t + self.wobble_phase) + 0.5 * math.sin(2 * math.pi * 3.1 * t))
        jr = jitter_deg * 0.6 * math.sin(2 * math.pi * 1.7 * t + 1.1)
        jy = jitter_deg * 0.5 * math.sin(2 * math.pi * 0.9 * t + 2.3)
        pos = self.pos.copy()
        pos[:2] += self.wind_offset
        return CameraPose(pos, (self.heading + jy) % 360.0, float(np.clip(self.gimbal_pitch + jp, -90, 0)), jr)

    def true_xy(self) -> np.ndarray:
        return self.pos[:2] + self.wind_offset

    def telemetry(self, t_ms: float, geo_ref, seq: int, north_offset_deg: float = 0.0, gps_scale: float = 1.0) -> DroneTelemetry:
        xy = self.true_xy() + self.gps_err * gps_scale
        lat = lon = None
        if geo_ref is not None:
            lat, lon = local_to_latlon(float(xy[0]), float(xy[1]), geo_ref, north_offset_deg)
        # NED velocity from local ENU (assuming local +y = north when north_offset = 0)
        ve, vn = float(self.vel[0]), float(self.vel[1])
        return DroneTelemetry(
            source_id="sim-mini2",
            seq=seq,
            capture_ts_ms=t_ms,
            lat=lat,
            lon=lon,
            alt_rel_m=round(float(self.pos[2] + self.baro_err), 2),
            yaw_deg=round(wrap_deg(self.heading + north_offset_deg), 2),
            pitch_deg=round(self.pitch, 2),
            roll_deg=round(self.roll, 2),
            vel_ned=(round(vn, 3), round(ve, 3), round(-float(self.vel[2]), 3)),
            gimbal_pitch_deg=round(self.gimbal_pitch, 2),
            battery_pct=round(self.battery, 1),
            gps_level=4,
            sats=14,
            flying=self.flying,
            vstick_enabled=self.vstick_enabled,
            sim=True,
            flight_mode="VIRTUAL_STICK" if self.vstick_enabled else self.mode,
        )
