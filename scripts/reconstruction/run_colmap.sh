#!/usr/bin/env bash
# Step 2 of 4: camera poses and a sparse point cloud with COLMAP (GPU SIFT recommended).
# Needs COLMAP 3.9+. Not run on the Overwatch development machine; see README.md.
# Usage: ./run_colmap.sh work/
set -euo pipefail
w="$1"
colmap feature_extractor --database_path "$w/db.db" --image_path "$w/images" --ImageReader.single_camera 1
colmap exhaustive_matcher --database_path "$w/db.db"
mkdir -p "$w/sparse"
colmap mapper --database_path "$w/db.db" --image_path "$w/images" --output_path "$w/sparse"
colmap model_converter --input_path "$w/sparse/0" --output_path "$w/sparse_points.ply" --output_type PLY
echo "sparse model in $w/sparse/0, points in $w/sparse_points.ply (frame: COLMAP's arbitrary frame, arbitrary scale)"
