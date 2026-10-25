#!/usr/bin/env bash
# Step 3 of 4: train a Gaussian splat with nerfstudio's splatfacto on a CUDA GPU (about 10-30 minutes).
# Needs nerfstudio 1.1+ and an NVIDIA GPU. Not run on the Overwatch development machine; see README.md.
# Usage: ./train_splatfacto.sh work/
set -euo pipefail
w="$1"
ns-process-data images --data "$w/images" --output-dir "$w/ns" --skip-colmap --colmap-model-path "$w/sparse/0"
ns-train splatfacto --data "$w/ns" --output-dir "$w/runs" --viewer.quit-on-train-completion True
cfg=$(ls -t "$w"/runs/*/splatfacto/*/config.yml | head -1)
ns-export gaussian-splat --load-config "$cfg" --output-dir "$w/export"
echo "splat PLY in $w/export/splat.ply"
