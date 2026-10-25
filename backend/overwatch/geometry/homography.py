"""Marker-based image->ground homography with *held-out* reprojection error (Rescue Copilot §9.2).

Training-point residuals alone are not an accuracy measure. Error is measured on dedicated holdout markers that
are never used for fitting; if none is visible, leave-one-marker-out cross-validation over the fit markers is
used and labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..vision.markers import MarkerDetection


def marker_world_corners(xy: tuple[float, float], size_m: float) -> np.ndarray:
    cx, cy = xy
    s = size_m / 2.0
    return np.array([[cx - s, cy + s], [cx + s, cy + s], [cx + s, cy - s], [cx - s, cy - s]], dtype=float)


def apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, float).reshape(-1, 2)
    p = np.column_stack([pts, np.ones(len(pts))]) @ H.T
    return p[:, :2] / p[:, 2:3]


@dataclass
class HomographyFit:
    H: np.ndarray | None
    valid: bool
    reason: str
    fit_ids: list[int] = field(default_factory=list)
    holdout_ids: list[int] = field(default_factory=list)
    fit_rmse_m: float | None = None
    holdout_rmse_m: float | None = None
    holdout_method: str = "none"  # dedicated | leave_one_out | none
    per_marker_error_m: dict[int, float] = field(default_factory=dict)
    inlier_ratio: float | None = None
    spread_m2: float = 0.0


def _fit(px: np.ndarray, world: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    if len(px) < 4:
        return None, None
    H, inl = cv2.findHomography(px, world, cv2.RANSAC, 0.2)
    return H, inl


def fit_marker_homography(dets: list[MarkerDetection], layout: dict[int, tuple[float, float]], size_m: float,
                          holdout_ids: set[int], min_markers: int = 3, max_holdout_rmse_m: float = 0.5,
                          min_spread_m2: float = 12.0) -> HomographyFit:
    known = [d for d in dets if d.marker_id in layout]
    fit = [d for d in known if d.marker_id not in holdout_ids]
    hold = [d for d in known if d.marker_id in holdout_ids]
    if len(fit) < min_markers:
        return HomographyFit(None, False, f"{len(fit)} fit markers visible (need {min_markers})",
                             [d.marker_id for d in fit], [d.marker_id for d in hold])
    centers = np.array([layout[d.marker_id] for d in fit], float)
    spread = float(cv2.contourArea(cv2.convexHull(centers.astype(np.float32)))) if len(centers) >= 3 else 0.0
    px = np.vstack([d.corners_px for d in fit])
    world = np.vstack([marker_world_corners(layout[d.marker_id], size_m) for d in fit])
    H, inl = _fit(px, world)
    if H is None:
        return HomographyFit(None, False, "homography fit failed", [d.marker_id for d in fit], [d.marker_id for d in hold])
    res = np.linalg.norm(apply_h(H, px) - world, axis=1)
    fit_rmse = float(np.sqrt(np.mean(res ** 2)))
    inlier_ratio = float(inl.mean()) if inl is not None else None
    per: dict[int, float] = {}
    method = "none"
    hold_errs: list[float] = []
    if hold:
        method = "dedicated"
        for d in hold:
            e = np.linalg.norm(apply_h(H, d.corners_px) - marker_world_corners(layout[d.marker_id], size_m), axis=1)
            per[d.marker_id] = float(np.sqrt(np.mean(e ** 2)))
            hold_errs.extend(e.tolist())
    elif len(fit) >= min_markers + 1:
        method = "leave_one_out"
        for k, d in enumerate(fit):
            others = [o for i, o in enumerate(fit) if i != k]
            Hk, _ = _fit(np.vstack([o.corners_px for o in others]),
                         np.vstack([marker_world_corners(layout[o.marker_id], size_m) for o in others]))
            if Hk is None:
                continue
            e = np.linalg.norm(apply_h(Hk, d.corners_px) - marker_world_corners(layout[d.marker_id], size_m), axis=1)
            per[d.marker_id] = float(np.sqrt(np.mean(e ** 2)))
            hold_errs.extend(e.tolist())
    for d in fit:
        if d.marker_id not in per:
            e = np.linalg.norm(apply_h(H, d.corners_px) - marker_world_corners(layout[d.marker_id], size_m), axis=1)
            per[d.marker_id] = float(np.sqrt(np.mean(e ** 2)))
    hold_rmse = float(np.sqrt(np.mean(np.square(hold_errs)))) if hold_errs else None
    ok = True
    reason = "ok"
    if spread < min_spread_m2:
        ok, reason = False, f"fit markers too close together ({spread:.1f} m^2 < {min_spread_m2})"
    elif hold_rmse is None:
        ok, reason = False, "no holdout marker visible: accuracy cannot be verified"
    elif hold_rmse > max_holdout_rmse_m:
        ok, reason = False, f"holdout error {hold_rmse:.2f} m > {max_holdout_rmse_m} m"
    return HomographyFit(H, ok, reason, [d.marker_id for d in fit], [d.marker_id for d in hold], fit_rmse, hold_rmse,
                         method, per, inlier_ratio, spread)
