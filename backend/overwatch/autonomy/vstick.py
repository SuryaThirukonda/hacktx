"""Virtual-stick command helpers.

DJI Mobile SDK v4, ``rollPitchControlMode = Velocity``, ``rollPitchCoordinateSystem = Body``: **positive pitch moves
the aircraft right and positive roll moves it forward** (Overwatch §5.1 axis gotcha). Everything in this repo speaks
(forward, right, up, yaw_rate); the bridge app maps through ``to_dji`` exactly once. Unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DJIStick:
    roll: float  # forward m/s (Body frame velocity mode)
    pitch: float  # right m/s
    yaw: float  # deg/s
    throttle: float  # up m/s


SDK_LIMITS = {"horizontal": 15.0, "yaw": 100.0, "vertical": 4.0}


def clamp(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


def to_dji(forward: float, right: float, up: float, yaw_rate: float) -> DJIStick:
    return DJIStick(roll=clamp(forward, SDK_LIMITS["horizontal"]), pitch=clamp(right, SDK_LIMITS["horizontal"]),
                    yaw=clamp(yaw_rate, SDK_LIMITS["yaw"]), throttle=clamp(up, SDK_LIMITS["vertical"]))
