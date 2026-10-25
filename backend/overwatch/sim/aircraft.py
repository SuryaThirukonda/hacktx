"""Aircraft profiles for the simulated drones, with where every number comes from.

Each value carries a source: "spec sheet" (the manufacturer's published specification), "Overwatch cap" (a limit
this system imposes below the spec), "assumed" (a reasonable value not yet measured) or "measured" (fitted from
recordings; none exist yet). Spec-sheet values are replaced by measured ones
as recordings arrive (see ``sim/step_response.py`` and ``tests/unit/test_aircraft_fit.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .drone_dynamics import DroneParams


@dataclass(frozen=True)
class Value:
    value: float
    unit: str
    source: str  # spec sheet | Overwatch cap | model file | assumed | measured
    note: str = ""


@dataclass
class AircraftProfile:
    id: str
    name: str
    values: dict[str, Value] = field(default_factory=dict)

    def v(self, key: str) -> float:
        return self.values[key].value

    def params(self) -> DroneParams:
        """The kinematic model parameters this profile implies (the defaults are the DJI Mini 2 profile)."""
        return DroneParams(tau_v=self.v("tau_v"), tau_vz=self.v("tau_vz"), tau_yaw=self.v("tau_yaw"),
                           sdk_max_h=self.v("max_h_cmd"), sdk_max_yaw=self.v("max_yaw_cmd"),
                           sdk_max_vz=self.v("max_vz_cmd"), gimbal_rate_dps=self.v("gimbal_rate"),
                           battery_minutes=self.v("flight_time"), gps_sigma_m=self.v("gps_sigma"))

    def table(self) -> list[dict[str, Any]]:
        return [{"key": k, "value": v.value, "unit": v.unit, "source": v.source, "note": v.note}
                for k, v in self.values.items()]


DJI_MINI_2 = AircraftProfile("dji_mini_2", "DJI Mini 2", {
    "mass": Value(0.249, "kg", "spec sheet", "takeoff weight under 249 g"),
    "max_h_speed": Value(16.0, "m/s", "spec sheet", "S mode"),
    "max_ascent": Value(5.0, "m/s", "spec sheet"),
    "max_descent": Value(3.5, "m/s", "spec sheet"),
    "flight_time": Value(31.0, "min", "spec sheet", "manufacturer's test conditions, calm air"),
    "battery_energy": Value(17.32, "Wh", "spec sheet", "2250 mAh at 7.7 V"),
    "hfov": Value(83.0, "deg", "spec sheet", "camera field of view"),
    "max_h_cmd": Value(15.0, "m/s", "Overwatch cap", "virtual-stick horizontal cap, below the spec"),
    "max_vz_cmd": Value(4.0, "m/s", "Overwatch cap"),
    "max_yaw_cmd": Value(100.0, "deg/s", "Overwatch cap"),
    "tau_v": Value(0.55, "s", "assumed", "first-order velocity response; replace with a fit to DJI simulator steps"),
    "tau_vz": Value(0.6, "s", "assumed"),
    "tau_yaw": Value(0.3, "s", "assumed"),
    "gimbal_rate": Value(60.0, "deg/s", "assumed"),
    "gps_sigma": Value(0.25, "m", "assumed", "random-walk step; bounded at the hover accuracy spec"),
    "hover_power": Value(17.32 / (31.0 / 60.0) * 0.9, "W", "assumed",
                         "derived: spec energy over spec flight time, 90% usable; the speed dependence is assumed"),
})


PROFILES = {"dji_mini_2": lambda: DJI_MINI_2}
