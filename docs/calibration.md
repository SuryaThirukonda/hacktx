# Calibration and registration

Metric claims (positions, speeds, ranges, clock bearings, time-to-contact) are only made while a calibration source
is valid. Otherwise tracks are shown in image space and the UI says why.

## 1. Ground markers (best)

1. Print markers: `make markers` (field, 1.0 m) or `python backend/scripts/make_markers.py --size-m 0.18` (A4,
   tabletop). Print `evaluation/markers/print.html` at 100 % scale and check the black square with a ruler.
2. Lay them flat, **top edge toward local +y**, at surveyed positions. Edit `calibration.markers.layout` (meters)
   and `size_m` in your config to match what you actually measured (tape measure, right angles via 3-4-5).
3. Keep at least one **holdout** marker (`holdout_ids`). Holdout markers are never used to fit the homography:
   their reprojection error is the honest accuracy estimate shown in the Calibration tab. If no holdout marker is
   visible, leave-one-out cross-validation over the fit markers is used (labelled `leave_one_out`).
4. Gates (config `calibration`): ≥ `min_markers` fit markers, fit-marker spread ≥ 12 m², holdout RMSE ≤
   `max_holdout_rmse_m`. Tune the threshold to the accuracy your demo needs; the defaults are targets, not
   measurements.

Between marker detections the homography is carried by camera-motion estimates (`chained`), with an error bound that
grows with the number of chained frames and age (`chained_max_age_s`). A chained mapping that disagrees with
telemetry by more than 3 m + its bound is dropped.

## 2. Telemetry projection (drone moving away from the markers)

With fresh DJI telemetry (GPS, barometric altitude, yaw, gimbal pitch) each pixel ray is intersected with the ground
plane (flat-ground assumption). While markers are visible the system learns the GPS bias (median difference between
the two projections) and applies it for up to 2 minutes; the error bound is
`telemetry_error_m_per_m_alt × altitude + GPS error (1.5 m raw, ~0.35 m bias-corrected)`.

The Mini 2 live-view intrinsics in `sources.drone.hfov_deg` (71.6° at 1280×720) are derived from the spec sheet and
**unverified**: calibrate with a checkerboard (`cv2.calibrateCamera`) before trusting telemetry projection.

## 3. Manual ground points (no markers)

`POST /api/calibration/ground-points` with ≥ 4 pixel↔ground correspondences (`{"u","v","x","y","holdout"}`).
Mark at least one as `holdout: true` (or give ≥ 5 points for leave-one-out); otherwise the calibration is
rejected because its accuracy cannot be verified.

## 4. Phone ↔ map registration

ARKit's frame has an arbitrary origin and yaw and drifts. Methods, best first:

* **auto_trajectory** — mark the responder in the drone view (alt-click). While the responder walks ≥ 2 m, the
  ARKit trajectory is aligned to the drone-observed ground track with a 2D rigid fit (Kabsch, worst 20 % of pairs
  dropped, residual ≤ `max_residual_m`). This is what the simulator demo uses (residual ≈ 0.1–0.2 m).
* **compass_point** — a good compass reading + one co-located point (low confidence).
* **manual_two_point** — `POST /api/registration/manual` with two ARKit and two map points (low confidence).

Until registration is valid, LiDAR obstacles stay in the phone-local view and the map says
`DRONE-LOCAL METRIC · phone not registered`.

## 5. Map modes

| Mode | Meaning |
|---|---|
| `REGISTERED_METRIC` | drone→map and phone→map both valid |
| `DRONE_LOCAL` | drone→map valid, phone not registered: no fused LiDAR on the map |
| `PHONE_ONLY` | drone video not current: aerial tracks are stale |
| `UNCALIBRATED` | no validated ground transform: nothing is metric |
