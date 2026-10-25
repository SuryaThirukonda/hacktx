#!/usr/bin/env python
"""Printable ArUco calibration markers + a placement diagram for the configured layout.

    python scripts/make_markers.py                  # sizes from config (field: 1.0 m markers)
    python scripts/make_markers.py --size-m 0.18    # tabletop demo: A4 sheets, scale the layout too

Outputs evaluation/markers/: marker_<id>.png (300 dpi), print.html (exact physical size when printed at 100%),
layout.png (where each marker goes; top edge faces local +y / "north"; holdout markers are measured, never fit).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import REPO_ROOT, load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-m", type=float, default=None, help="printed marker side (default: config)")
    ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()
    cfg = load_config()
    mk = cfg.calibration.markers
    size = a.size_m or mk.size_m
    out = REPO_ROOT / "evaluation" / "markers"
    out.mkdir(parents=True, exist_ok=True)
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, mk.dictionary))
    px = int(round(size / 0.0254 * a.dpi))
    quiet = int(px * 0.12)
    html = ["<!doctype html><meta charset=utf-8><title>Overwatch markers</title>",
            "<style>@page{margin:10mm} body{font-family:system-ui;margin:0} .m{page-break-after:always;text-align:center}"
            " img{display:block;margin:0 auto} .note{font-size:12px;color:#444}</style>"]
    for mid, (x, y) in sorted(mk.layout.items()):
        img = cv2.aruco.generateImageMarker(dictionary, int(mid), px)
        sheet = np.full((px + 2 * quiet, px + 2 * quiet), 255, np.uint8)
        sheet[quiet:quiet + px, quiet:quiet + px] = img
        cv2.arrowedLine(sheet, (sheet.shape[1] // 2, quiet - 4), (sheet.shape[1] // 2, 4), 0, max(2, px // 200), tipLength=0.4)
        name = f"marker_{mid}.png"
        cv2.imwrite(str(out / name), sheet)
        role = "HOLDOUT (accuracy check, never used for fitting)" if int(mid) in mk.holdout_ids else "fit"
        side_mm = size * 1000 * (px + 2 * quiet) / px
        html.append(f"<div class=m><h2>ArUco {mk.dictionary} id {mid}</h2><img src='{name}' style='width:{side_mm:.1f}mm;height:{side_mm:.1f}mm'>"
                    f"<p class=note>Black square = {size * 100:.1f} cm. Place centre at x={x} m, y={y} m. Arrow/top edge points to local +y. Role: {role}.</p></div>")
    (out / "print.html").write_text("\n".join(html))
    # placement diagram
    xs = [v[0] for v in mk.layout.values()]
    ys = [v[1] for v in mk.layout.values()]
    pad = 3.0
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    scale = 900 / max(x1 - x0, y1 - y0)
    W, H = int((x1 - x0) * scale), int((y1 - y0) * scale)
    canvas = np.full((H + 60, W + 170, 3), 250, np.uint8)
    to = lambda x, y: (int((x - x0) * scale), int((y1 - y) * scale))  # noqa: E731
    for gx in range(int(x0), int(x1) + 1):
        cv2.line(canvas, to(gx, y0), to(gx, y1), (230, 230, 230), 1)
    for gy in range(int(y0), int(y1) + 1):
        cv2.line(canvas, to(x0, gy), to(x1, gy), (230, 230, 230), 1)
    for mid, (x, y) in mk.layout.items():
        hold = int(mid) in mk.holdout_ids
        c = (180, 80, 140) if hold else (110, 150, 30)
        p = to(x, y)
        s = max(6, int(size * scale / 2))
        cv2.rectangle(canvas, (p[0] - s, p[1] - s), (p[0] + s, p[1] + s), c, 2)
        cv2.putText(canvas, f"id {mid}{' holdout' if hold else ''} ({x:g},{y:g})", (p[0] + s + 4, p[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
    cv2.arrowedLine(canvas, (W + 140, 60), (W + 140, 20), (40, 40, 40), 2, tipLength=0.3)
    cv2.putText(canvas, "+y", (W + 130, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1)
    cv2.putText(canvas, f"1 grid square = 1 m; marker size {size} m; origin = marker 0", (10, H + 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.imwrite(str(out / "layout.png"), canvas)
    print(f"wrote {len(mk.layout)} markers + print.html + layout.png to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
