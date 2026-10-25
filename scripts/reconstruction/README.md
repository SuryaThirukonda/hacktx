# Reconstruction scripts (GPU machine, not run here)

These scripts turn a drone or phone video into a mesh, point cloud or Gaussian splat that Overwatch can show in
the Scene tab. **None of them were run on the development machine:** it has no NVIDIA GPU. Nothing in the
dashboard is presented as reconstructed unless an asset was imported with `reconstructed=true`. The simulator
geometry export and the sample indoor scan are labelled as exports and synthetic data.

| Step | Script | Needs | Output |
|---|---|---|---|
| 1 | `extract_frames.sh video.mp4 work/ 2` | ffmpeg | `work/images/*.jpg` |
| 2 | `run_colmap.sh work/` | COLMAP 3.9+ (GPU SIFT recommended) | camera poses, `work/sparse_points.ply` |
| 3 | `train_splatfacto.sh work/` | nerfstudio 1.1+, CUDA GPU, roughly 10 to 30 min | `work/export/splat.ply` |
| 4 | `export_for_overwatch.sh <api> <incident> <file> <name> [transform.json]` | curl, python3 | a scene asset (and its alignment) |

## Import format

- **Files:** PLY (ASCII or binary; a point cloud, a mesh with faces, or a 3D Gaussian splat with `f_dc_*`,
  `opacity`, `scale_*`, `rot_*`), OBJ, `.splat` or `.ksplat`. Units are metres, z up (pass `up_axis=y` for y-up
  files). The upload is the raw file body:
  `POST /api/incidents/{id}/scene/assets?name=…&format=ply|obj|splat|ksplat&origin=…&reconstructed=true|false`.
- **Frame:** each asset gets its own frame, `scene:<asset id>` (or `arkit:<asset id>` with `frame_kind=arkit` for
  iPhone scans). Labels made on the asset are stored in that frame.
- **Alignment (`transform.json`):**

  ```json
  {"source_frame": "scene:SCN-…", "target_frame": "enu:INC-…",
   "matrix": [[r11, r12, r13, tx], [r21, r22, r23, ty], [r31, r32, r33, tz], [0, 0, 0, 1]],
   "method": "gcp similarity (5 points)", "residual_m": 0.18}
  ```

  `POST /api/scene-assets/{id}/transform` with the JSON as the body. `source_frame` defaults to the asset's frame
  and `target_frame` to the incident ENU frame. Without an alignment the scene is viewable, but incident
  observations are not drawn on it. The overlay names the missing link instead of guessing.
