"""Velocity step-response harness: fly the same command steps through any drone model (or read a recording) and
compare rise time, steady-state speed and RMS velocity error.

Pass criteria (plan section 9): rise time and steady-state speed within 15%, RMS velocity error under 0.3 m/s.
Recording format (CSV, one per step test, in ``data/recordings/dji_steps/``): ``t_s,cmd_forward_mps,vel_forward_mps``,
recorded from DJI's built-in simulator through the bridge with the propellers off. None exist yet.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Callable

import numpy as np

from ..schemas import VStickCommand

RECORDINGS = Path(__file__).resolve().parents[3] / "data" / "recordings" / "dji_steps"


def step_commands(levels: list[tuple[float, float]]) -> Callable[[float], float]:
    """levels: [(t_start_s, forward_mps)] -> command at time t."""
    def cmd(t: float) -> float:
        v = 0.0
        for t0, lv in levels:
            if t >= t0:
                v = lv
        return v
    return cmd


def run_model(drone, cmd: Callable[[float], float], duration_s: float, dt: float = 0.02) -> np.ndarray:
    """Fly ``drone`` (SimDrone or MujocoDrone) with virtual-stick forward commands; returns rows (t, cmd, vel_fwd)."""
    drone.vstick_enabled = True
    rows = []
    t = 0.0
    while t < duration_s:
        c = cmd(t)
        drone.step(dt, VStickCommand(forward_mps=c, ttl_ms=1000.0))
        h = math.radians(drone.heading)
        fwd = float(drone.vel[0] * math.sin(h) + drone.vel[1] * math.cos(h))
        t += dt
        rows.append((t, c, fwd))
    return np.array(rows)


def load_recording(path: Path) -> np.ndarray:
    with path.open() as f:
        r = csv.DictReader(f)
        return np.array([(float(x["t_s"]), float(x["cmd_forward_mps"]), float(x["vel_forward_mps"])) for x in r])


def metrics(series: np.ndarray) -> dict[str, float | None]:
    """Response to the first non-zero step: delay (to 10%), rise time (10 to 90%), steady-state speed."""
    t, c, v = series[:, 0], series[:, 1], series[:, 2]
    on = np.nonzero(np.abs(c) > 1e-6)[0]
    if not len(on):
        return {"rise_s": None, "steady_mps": None, "delay_s": None}
    i0 = on[0]
    level = c[i0]
    seg_end = next((j for j in range(i0, len(c)) if abs(c[j] - level) > 1e-6), len(c))
    tt, vv = t[i0:seg_end] - t[i0], v[i0:seg_end]
    steady = float(np.mean(vv[-max(3, len(vv) // 10):]))
    def first(frac: float) -> float | None:
        k = np.nonzero(np.abs(vv) >= abs(steady) * frac)[0]
        return float(tt[k[0]]) if len(k) else None
    t10, t90 = first(0.1), first(0.9)
    return {"rise_s": (t90 - t10) if t10 is not None and t90 is not None else None, "steady_mps": steady,
            "delay_s": t10}


def compare(sim: np.ndarray, ref: np.ndarray, tol: float = 0.15, rms_max: float = 0.3) -> dict:
    ms, mr = metrics(sim), metrics(ref)
    t = ref[:, 0]
    v_sim = np.interp(t, sim[:, 0], sim[:, 2])
    rms = float(np.sqrt(np.mean((v_sim - ref[:, 2]) ** 2)))
    def rel(a, b):
        return None if a is None or b is None or abs(b) < 1e-9 else abs(a - b) / abs(b)
    r_rise, r_ss = rel(ms["rise_s"], mr["rise_s"]), rel(ms["steady_mps"], mr["steady_mps"])
    ok = r_rise is not None and r_ss is not None and r_rise <= tol and r_ss <= tol and rms <= rms_max
    return {"sim": ms, "ref": mr, "rise_rel_err": r_rise, "steady_rel_err": r_ss, "rms_mps": round(rms, 3), "pass": ok}
