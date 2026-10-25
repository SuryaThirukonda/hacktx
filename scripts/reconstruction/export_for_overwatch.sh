#!/usr/bin/env bash
# Step 4 of 4: import the result into an incident with its alignment.
# The reconstruction frame is arbitrary (COLMAP origin and scale). Overwatch draws incident observations on it only
# after an alignment exists: measure three or more ground control points in both frames, fit a similarity
# transform, and write transform.json {"source_frame": "scene:<asset id>", "target_frame": "enu:<incident>",
# "matrix": 4x4, "method": "gcp similarity (N points)", "residual_m": <RMS of the held-out points>}.
# Usage: ./export_for_overwatch.sh http://localhost:8000 INC-xxxx work/export/splat.ply "Drone splat, site A" [transform.json]
set -euo pipefail
api="$1"; iid="$2"; file="$3"; name="$4"; tf="${5:-}"
fmt="${file##*.}"
q="name=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$name")&format=$fmt&reconstructed=true&provenance=replay"
q="$q&origin=$(python3 -c 'import urllib.parse;print(urllib.parse.quote("COLMAP + splatfacto reconstruction"))')"
asset=$(curl -sf -X POST "$api/api/incidents/$iid/scene/assets?$q${OVERWATCH_TOKEN:+&token=$OVERWATCH_TOKEN}" \
  -H 'content-type: application/octet-stream' --data-binary @"$file")
id=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["id"])' "$asset")
echo "imported scene asset $id"
if [ -n "$tf" ]; then
  curl -sf -X POST "$api/api/scene-assets/$id/transform${OVERWATCH_TOKEN:+?token=$OVERWATCH_TOKEN}" --data-binary @"$tf" >/dev/null
  echo "alignment imported from $tf"
fi
