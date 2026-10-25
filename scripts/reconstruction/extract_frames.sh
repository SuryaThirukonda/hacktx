#!/usr/bin/env bash
# Step 1 of 4: extract sharp frames from a drone or phone video for reconstruction.
# Needs ffmpeg. Not run on the Overwatch development machine (no GPU); see README.md.
# Usage: ./extract_frames.sh input.mp4 work/ [fps]
set -euo pipefail
in="$1"; out="$2"; fps="${3:-2}"
mkdir -p "$out/images"
ffmpeg -hide_banner -loglevel error -i "$in" -vf "fps=${fps},scale='min(1600,iw)':-2" -q:v 2 "$out/images/frame_%05d.jpg"
echo "wrote $(ls "$out/images" | wc -l) frames to $out/images"
