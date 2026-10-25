"""Calibration manager: chooses the best *valid* image->ground mapping each frame and reports its error bound.

Priority (Rescue Copilot §9 + Overwatch §5.4):
1. ``markers``   fresh ArUco homography with held-out error within the gate;
2. ``chained``   last marker/manual homography carried through camera-motion estimates (age/drift limited);
3. ``telemetry`` pose-based ray projection from DJI telemetry (GPS, baro altitude, yaw, gimbal pitch), corrected
                 by a GPS bias learned while markers were visible;
4. ``none``      no metric claims: tracks stay in image space ("unlocalized").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import Config
from ..ids import IdGen
from ..schemas import CalibrationStatus, DroneTelemetry, MarkerObservation
from ..vision.cmc import CMCResult
from ..vision.markers import MarkerDetection, MarkerDetector
from .camera import CameraPose, Intrinsics, PinholeCamera
from .geo import latlon_to_local, true_to_local_heading
from .homography import HomographyFit, apply_h, fit_marker_homography, marker_world_corners


@dataclass
class _Calib:
    calib_id: str
    H: np.ndarray  # image px -> ground (x, y)
    source: str
    created_ms: float
    base_error_m: float
    fit: HomographyFit | None = None
    chained_frames: int = 0
    lineage: str | None = None


@dataclass
class ProjectionResult:
    xy: np.ndarray  # (N, 2)
    ok: np.ndarray  # (N,) bool
    error_m: float


def camera_from_telemetry(tel: DroneTelemetry, intr: Intrinsics, geo_ref, north_offset_deg: float,
                          bias_xy: np.ndarray | None = None) -> PinholeCamera | None:
    if (geo_ref is None or tel.lat is None or tel.lon is None or tel.alt_rel_m is None or tel.yaw_deg is None
            or tel.gimbal_pitch_deg is None):
        return None
    x, y = latlon_to_local(tel.lat, tel.lon, geo_ref, north_offset_deg)
    if bias_xy is not None:
        x, y = x + float(bias_xy[0]), y + float(bias_xy[1])
    pose = CameraPose(np.array([x, y, max(0.5, tel.alt_rel_m)]), true_to_local_heading(tel.yaw_deg, north_offset_deg),
                      float(np.clip(tel.gimbal_pitch_deg, -90.0, -1.0)), 0.0)
    return PinholeCamera(intr, pose)


class CalibrationManager:
    def __init__(self, cfg: Config, intr: Intrinsics) -> None:
        self.cfg = cfg
        self.intr = intr
        mk = cfg.calibration.markers
        self.layout = {int(k): (float(v[0]), float(v[1])) for k, v in mk.layout.items()}
        self.holdout = set(int(i) for i in mk.holdout_ids)
        self.detector = MarkerDetector(mk.dictionary, known_ids=set(self.layout)) if self.layout else None
        self.ids = IdGen("calib")
        self.reset()

    def reset(self) -> None:
        self.frame_count = 0
        self.current: _Calib | None = None
        self.last_marker: _Calib | None = None
        self.last_marker_ms: float | None = None
        self.last_markers: list[MarkerObservation] = []
        self.markers_detected = 0
        self.tel_bias = np.zeros(2)
        self.tel_bias_ms: float | None = None
        self.manual: _Calib | None = None
        self.status = CalibrationStatus()
        self.telemetry_camera: PinholeCamera | None = None

    # ---------------------------------------------------------------------------------------------------------
    def update(self, image: np.ndarray, t_ms: float, cmc: CMCResult | None, telemetry: DroneTelemetry | None,
               telemetry_fresh: bool) -> CalibrationStatus:
        cc = self.cfg.calibration
        self.frame_count += 1
        geo = self.cfg.world.geo_reference
        north = self.cfg.world.north_offset_deg
        tel_cam_raw = camera_from_telemetry(telemetry, self.intr, geo, north) if (telemetry and telemetry_fresh) else None

        fresh: _Calib | None = None
        fit: HomographyFit | None = None
        if self.detector is not None and (self.frame_count % max(1, cc.markers.detect_every_n_frames) == 1
                                          or cc.markers.detect_every_n_frames <= 1):
            dets = self.detector.detect(image)
            self.markers_detected = len(dets)
            fit = fit_marker_homography(dets, self.layout, cc.markers.size_m, self.holdout, cc.min_markers,
                                        cc.max_holdout_rmse_m)
            self.last_markers = self._marker_obs(dets, fit)
            if fit.valid and fit.H is not None:
                err = max(0.05, fit.holdout_rmse_m or 0.0)
                fresh = _Calib(self.ids(), fit.H, "markers", t_ms, err, fit)
                self.last_marker = fresh
                self.last_marker_ms = t_ms
                if tel_cam_raw is not None:
                    self._learn_bias(fresh.H, tel_cam_raw, t_ms)

        candidates: list[_Calib] = []
        if fresh is not None:
            candidates.append(fresh)
        else:
            prev = self.current
            if prev is not None and prev.source in ("markers", "chained", "manual") and cmc is not None and cmc.ok:
                origin_ms = self.last_marker_ms if prev.source != "manual" else prev.created_ms
                age_s = (t_ms - (origin_ms or t_ms)) / 1000.0
                if prev.source == "manual" or age_s <= cc.chained_max_age_s:
                    H = prev.H @ np.linalg.inv(cmc.H)
                    H /= H[2, 2]
                    n = prev.chained_frames + 1
                    base = (prev.fit.holdout_rmse_m if prev.fit and prev.fit.holdout_rmse_m else prev.base_error_m)
                    err = base + 0.035 * math.sqrt(n) + 0.06 * age_s
                    candidates.append(_Calib(prev.calib_id if prev.source != "chained" else prev.calib_id, H,
                                             "manual" if prev.source == "manual" else "chained", prev.created_ms, err,
                                             prev.fit, n, prev.lineage or prev.calib_id))
        if tel_cam_raw is not None and cc.telemetry_projection and telemetry is not None:
            bias_age = None if self.tel_bias_ms is None else (t_ms - self.tel_bias_ms) / 1000.0
            bias = self.tel_bias if (bias_age is not None and bias_age < 120.0) else None
            tel_cam = camera_from_telemetry(telemetry, self.intr, geo, north, bias)
            self.telemetry_camera = tel_cam
            if tel_cam is not None:
                alt = float(telemetry.alt_rel_m or 0.0)
                gps_err = 1.5 if bias is None else 0.35 + 0.015 * float(bias_age or 0.0)
                err = cc.telemetry_error_m_per_m_alt * alt + gps_err
                Ht = tel_cam.image_to_ground_homography()
                candidates.append(_Calib(f"tel-{int(t_ms)}", Ht, "telemetry", t_ms, err, None,
                                         lineage="bias-corrected" if bias is not None else "raw-gps"))
        else:
            self.telemetry_camera = None
        # chained homographies drift: cross-check against telemetry when both exist
        chosen: _Calib | None = None
        if candidates:
            chosen = min(candidates, key=lambda c: c.base_error_m)
            tel = next((c for c in candidates if c.source == "telemetry"), None)
            if chosen.source == "chained" and tel is not None:
                probe = np.array([[self.intr.cx, self.intr.cy]])
                gap = float(np.linalg.norm(apply_h(chosen.H, probe) - apply_h(tel.H, probe)))
                if gap > 3.0 + tel.base_error_m:
                    chosen = tel
        self.current = chosen
        self.status = self._status(chosen, fit, t_ms)
        return self.status

    # ---------------------------------------------------------------------------------------------------------
    def _learn_bias(self, H_marker: np.ndarray, tel_cam: PinholeCamera, t_ms: float) -> None:
        W, Hh = self.intr.width, self.intr.height
        grid = np.array([[W * fx, Hh * fy] for fx in (0.25, 0.5, 0.75) for fy in (0.4, 0.6, 0.8)])
        gm = apply_h(H_marker, grid)
        gt, hit = tel_cam.pixel_to_ground(grid)
        if hit.sum() < 4:
            return
        delta = np.median(gm[hit] - gt[hit], axis=0)
        if self.tel_bias_ms is None:
            self.tel_bias = delta
        else:
            self.tel_bias = 0.7 * self.tel_bias + 0.3 * delta
        self.tel_bias_ms = t_ms

    def _marker_obs(self, dets: list[MarkerDetection], fit: HomographyFit) -> list[MarkerObservation]:
        out = []
        for d in dets:
            wxy = self.layout.get(d.marker_id)
            out.append(MarkerObservation(
                id=d.marker_id, center_px=d.center_px, corners_px=[(float(x), float(y)) for x, y in d.corners_px],
                world_xy=wxy, used_for_fit=d.marker_id in fit.fit_ids, holdout=d.marker_id in self.holdout,
                error_m=round(fit.per_marker_error_m[d.marker_id], 3) if d.marker_id in fit.per_marker_error_m else None))
        return out

    def _status(self, c: _Calib | None, fit: HomographyFit | None, t_ms: float) -> CalibrationStatus:
        lm = self.last_marker
        if c is None:
            reason = fit.reason if fit is not None else "no calibration source"
            if self.last_marker_ms is not None and fit is not None and not fit.valid:
                reason = f"CALIBRATION LOST: {fit.reason}"
            return CalibrationStatus(valid=False, source="none", reason=reason, markers_detected=self.markers_detected,
                                     markers=self.last_markers, last_marker_calibration_id=lm.calib_id if lm else None)
        f = c.fit
        age = (t_ms - (self.last_marker_ms if c.source == "chained" and self.last_marker_ms else c.created_ms)) / 1000.0
        reason = {"markers": "marker homography; holdout error within gate",
                  "chained": f"marker homography carried by camera-motion estimates ({c.chained_frames} frames)",
                  "telemetry": f"pose projection from drone telemetry ({c.lineage})",
                  "manual": "operator ground points"}[c.source]
        return CalibrationStatus(
            calibration_id=c.calib_id, source=c.source, valid=True, reason=reason,
            H_img_to_ground=np.round(c.H, 8).tolist(), markers_detected=self.markers_detected,
            markers_used=len(f.fit_ids) if f else 0, holdout_count=len(f.holdout_ids) if f else 0,
            holdout_rmse_m=round(f.holdout_rmse_m, 3) if f and f.holdout_rmse_m is not None else None,
            fit_rmse_m=round(f.fit_rmse_m, 3) if f and f.fit_rmse_m is not None else None,
            age_s=round(max(0.0, age), 2), error_bound_m=round(c.base_error_m, 2), markers=self.last_markers,
            last_marker_calibration_id=lm.calib_id if lm else None)

    # ---------------------------------------------------------------------------------------------------------
    @property
    def valid(self) -> bool:
        return self.current is not None

    @property
    def calibration_id(self) -> str | None:
        return self.current.calib_id if self.current else None

    @property
    def error_m(self) -> float | None:
        return self.current.base_error_m if self.current else None

    def project(self, px: np.ndarray) -> ProjectionResult | None:
        if self.current is None:
            return None
        pts = np.asarray(px, float).reshape(-1, 2)
        H = self.current.H
        p = np.column_stack([pts, np.ones(len(pts))]) @ H.T
        ok = p[:, 2] > 1e-9 if self.current.source == "telemetry" else np.abs(p[:, 2]) > 1e-12
        xy = p[:, :2] / np.where(np.abs(p[:, 2:3]) < 1e-12, 1e-12, p[:, 2:3])
        # sanity: points near/above the horizon project absurdly far away
        ok &= np.isfinite(xy).all(axis=1) & (np.abs(xy) < 5000).all(axis=1)
        return ProjectionResult(xy, ok, self.current.base_error_m)

    def ground_to_image(self, xy: np.ndarray) -> np.ndarray | None:
        if self.current is None:
            return None
        Hi = np.linalg.inv(self.current.H)
        return apply_h(Hi, xy)

    def gsd_at(self, u: float, v: float) -> float | None:
        r = self.project(np.array([[u, v], [u + 1, v]]))
        if r is None or not r.ok.all():
            return None
        return float(np.linalg.norm(r.xy[1] - r.xy[0]))

    def footprint(self) -> list[tuple[float, float]] | None:
        if self.telemetry_camera is not None:
            return self.telemetry_camera.footprint(max_range_m=80.0)
        if self.current is None:
            return None
        W, H = self.intr.width, self.intr.height
        pts = []
        for (u0, v0), (u1, v1) in [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))]:
            for s in np.linspace(0, 1, 5, endpoint=False):
                pts.append((u0 + (u1 - u0) * s, v0 + (v1 - v0) * s))
        r = self.project(np.array(pts))
        if r is None or not r.ok.all():
            return None
        return [(float(x), float(y)) for x, y in r.xy]

    # ---------------------------------------------------------------------------------------------------------
    def set_manual(self, px: list[tuple[float, float]], world: list[tuple[float, float]], holdout: list[bool],
                   t_ms: float) -> CalibrationStatus:
        P = np.array(px, float)
        Wd = np.array(world, float)
        hmask = np.array(holdout, bool) if holdout else np.zeros(len(P), bool)
        fitP, fitW = P[~hmask], Wd[~hmask]
        if len(fitP) < 4:
            raise ValueError("need at least 4 non-holdout correspondences")
        H, _ = cv2.findHomography(fitP, fitW, 0)
        if H is None:
            raise ValueError("degenerate correspondences")
        errs: list[float] = []
        if hmask.any():
            errs = list(np.linalg.norm(apply_h(H, P[hmask]) - Wd[hmask], axis=1))
        elif len(fitP) >= 5:
            for k in range(len(fitP)):
                idx = [i for i in range(len(fitP)) if i != k]
                Hk, _ = cv2.findHomography(fitP[idx], fitW[idx], 0)
                if Hk is not None:
                    errs.append(float(np.linalg.norm(apply_h(Hk, fitP[k:k + 1]) - fitW[k:k + 1])))
        rmse = float(np.sqrt(np.mean(np.square(errs)))) if errs else None
        fit = HomographyFit(H, rmse is not None and rmse <= self.cfg.calibration.max_holdout_rmse_m,
                            "manual ground points", holdout_rmse_m=rmse,
                            holdout_method="dedicated" if hmask.any() else ("leave_one_out" if errs else "none"))
        if not fit.valid:
            raise ValueError(f"manual calibration rejected: holdout error {rmse} (need <= "
                             f"{self.cfg.calibration.max_holdout_rmse_m} m and >= 1 holdout or 5 points)")
        self.manual = _Calib(self.ids(), H, "manual", t_ms, max(0.1, rmse or 0.1), fit)
        self.current = self.manual
        self.status = self._status(self.current, fit, t_ms)
        return self.status


__all__ = ["CalibrationManager", "camera_from_telemetry", "marker_world_corners", "ProjectionResult"]
