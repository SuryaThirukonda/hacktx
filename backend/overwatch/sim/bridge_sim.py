"""In-process emulation of the iPhone A "Drone Bridge" app, with the same command semantics as the real one
(ios/OverwatchDroneBridge): 20 Hz virtual stick, 300 ms command TTL -> hover, 2 s link loss -> virtual stick
disabled (pilot regains control), command latency, pilot stick override."""

from __future__ import annotations

from collections import deque

import numpy as np

from ..schemas import BridgeCommand, DroneTelemetry, VStickCommand
from .drone_dynamics import SimDrone


class SimBridge:
    is_sim = True

    def __init__(self, drone: SimDrone, ttl_ms: float = 300.0, link_timeout_ms: float = 2000.0,
                 latency_ms: tuple[float, float] = (50.0, 150.0), seed: int = 9) -> None:
        self.drone = drone
        self.ttl_ms = ttl_ms
        self.link_timeout_ms = link_timeout_ms
        self.latency_ms = latency_ms
        self.rng = np.random.default_rng(seed)
        self.pending: deque[tuple[float, BridgeCommand]] = deque()
        self.active_cmd: VStickCommand | None = None
        self.active_cmd_rx_ms = -1e12
        self.last_link_ms = -1e12
        self.now_ms = 0.0
        self.cmd_count = 0
        self.audit: deque[dict] = deque(maxlen=200)
        self._watchdog_hover = False
        self._tel_seq = 0
        self.link_up = True  # fault injection: simulated Mac<->bridge link loss

    # -- interface used by the autonomy controller ---------------------------------------------------------
    def connected(self, now_ms: float) -> bool:
        return self.link_up

    def send(self, cmd: BridgeCommand) -> None:
        if not self.link_up:
            return
        lat = float(self.rng.uniform(*self.latency_ms))
        self.pending.append((self.now_ms + lat, cmd))

    # -- simulation step -------------------------------------------------------------------------------------
    def step(self, dt: float, now_ms: float, wind_gusts: bool = False) -> None:
        self.now_ms = now_ms
        while self.pending and self.pending[0][0] <= now_ms:
            _, cmd = self.pending.popleft()
            self._apply(cmd, now_ms)
        cmd: VStickCommand | None = None
        if self.drone.vstick_enabled:
            age = now_ms - self.active_cmd_rx_ms
            if self.active_cmd is not None and age <= min(self.ttl_ms, self.active_cmd.ttl_ms):
                cmd = self.active_cmd
                self._watchdog_hover = False
            elif not self._watchdog_hover:
                self._watchdog_hover = True
                self._log(now_ms, "watchdog_hover", f"no virtual-stick command for {age:.0f} ms -> hover")
            if now_ms - self.last_link_ms > self.link_timeout_ms:
                self.drone.vstick_enabled = False
                self._log(now_ms, "link_lost", "Mac link lost > 2 s: virtual stick disabled, pilot in control")
        self.drone.step(dt, cmd, wind_gusts=wind_gusts)

    def _apply(self, cmd: BridgeCommand, now_ms: float) -> None:
        self.last_link_ms = now_ms
        d = self.drone
        if cmd.type == "vstick":
            self.active_cmd = VStickCommand(**cmd.payload)
            self.active_cmd_rx_ms = now_ms
            self.cmd_count += 1
        elif cmd.type == "gimbal":
            d.gimbal_target = float(np.clip(cmd.payload.get("pitch_deg", d.gimbal_target), -90.0, 0.0))
        elif cmd.type == "mode":
            enable = bool(cmd.payload.get("vstick", False))
            if enable and d.mode == "RTL":
                self._log(now_ms, "mode_rejected", "virtual stick refused during RTL")
                return
            if enable != d.vstick_enabled:
                d.vstick_enabled = enable
                self._log(now_ms, "mode", f"virtual stick {'enabled' if enable else 'disabled'}")
        elif cmd.type in ("hover", "estop"):
            self.active_cmd = VStickCommand(ts_ms=now_ms)
            self.active_cmd_rx_ms = now_ms
            if cmd.type == "estop":
                d.vstick_enabled = False
                d.mode = "P-GPS"
            self._log(now_ms, cmd.type, "hover" if cmd.type == "hover" else "E-STOP: hover + virtual stick disabled")
        elif cmd.type == "rtl":
            d.vstick_enabled = False
            d.mode = "RTL"
            self._log(now_ms, "rtl", "return to launch")
        elif cmd.type == "ping":
            pass

    def pilot_override(self) -> None:
        """Pilot moved the sticks: DJI exits virtual stick mode (this is our override, and it's a feature)."""
        if self.drone.vstick_enabled:
            self.drone.vstick_enabled = False
            self._log(self.now_ms, "pilot_override", "pilot stick input: virtual stick exited")
        if self.drone.mode == "RTL":
            self.drone.mode = "P-GPS"

    def telemetry(self, now_ms: float, geo_ref, north_offset_deg: float = 0.0, gps_scale: float = 1.0) -> DroneTelemetry:
        self._tel_seq += 1
        return self.drone.telemetry(now_ms, geo_ref, self._tel_seq, north_offset_deg, gps_scale)

    def _log(self, now_ms: float, kind: str, text: str) -> None:
        self.audit.append({"ts_ms": now_ms, "kind": kind, "text": text})
