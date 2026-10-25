"""Probability-of-area maps for a land search.

* A prior from the initial planning point: a ring distribution over distance (a log-normal with a median distance
  and spread per behaviour profile) times terrain weights. The profile numbers below are illustrative simulation
  parameters for the simulated worlds, not published lost-person statistics.
* Bayesian update after a search: p' = p (1 - POD) inside the searched area, then renormalise.
* POD from coverage by the random-search formula POD = 1 - exp(-W/S) (sweep width W, track spacing S).
"""

from __future__ import annotations

import math

import numpy as np

# Illustrative parameters for the simulator only (median distance from the planning point, log-normal spread).
PROFILES = {
    "walk_away": {"median_m": 90.0, "sigma": 0.6},
    "stay_put": {"median_m": 35.0, "sigma": 0.5},
    "follow_trail": {"median_m": 120.0, "sigma": 0.45},
}


def prior(X: np.ndarray, Y: np.ndarray, ipp: tuple[float, float], profile: str,
          terrain_weight: np.ndarray | None = None) -> np.ndarray:
    pr = PROFILES[profile]
    d = np.hypot(X - ipp[0], Y - ipp[1]) + 1.0
    ring = np.exp(-0.5 * ((np.log(d) - math.log(pr["median_m"])) / pr["sigma"]) ** 2) / d  # per-area density
    p = ring * (terrain_weight if terrain_weight is not None else 1.0)
    return p / p.sum()


def pod(sweep_width_m: float, spacing_m: float) -> float:
    return 1.0 - math.exp(-sweep_width_m / max(spacing_m, 1e-6))


def update(p: np.ndarray, searched: np.ndarray, pod_value: float) -> np.ndarray:
    q = p * np.where(searched, 1.0 - pod_value, 1.0)
    return q / q.sum()


def sector_poa(p: np.ndarray, sector_masks: dict[str, np.ndarray]) -> dict[str, float]:
    return {k: float(p[m].sum()) for k, m in sector_masks.items()}
