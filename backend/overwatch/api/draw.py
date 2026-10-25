"""Server-side overlay rendering for MJPEG/snapshot endpoints (the dashboard draws its own interactive overlay)."""

from __future__ import annotations

import cv2
import numpy as np

from ..schemas import FrameOverlay

ROLE_COLORS = {"subject": (11, 158, 245), "responder": (246, 130, 59), None: (238, 211, 34)}


def annotate(img: np.ndarray, ov: FrameOverlay) -> np.ndarray:
    out = img.copy()
    for z in ov.zones_px:
        pts = np.array(z["polygon"], np.int32)
        cv2.polylines(out, [pts], True, (200, 200, 255), 1, cv2.LINE_AA)
    for m in ov.markers:
        if m.corners_px:
            col = (255, 0, 255) if m.holdout else (255, 255, 0)
            cv2.polylines(out, [np.array(m.corners_px, np.int32)], True, col, 2, cv2.LINE_AA)
            cv2.putText(out, f"M{m.id}{' H' if m.holdout else ''}", (int(m.center_px[0]) + 6, int(m.center_px[1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
    for t in ov.tracks:
        x0, y0, x1, y1 = (int(v) for v in t.box)
        col = ROLE_COLORS.get(t.role, ROLE_COLORS[None])
        dashed = t.state in ("LOST", "OCCLUDED")
        thick = 2 if t.role else 1
        if dashed:
            for i in range(x0, x1, 6):
                cv2.line(out, (i, y0), (min(i + 3, x1), y0), col, 1)
                cv2.line(out, (i, y1), (min(i + 3, x1), y1), col, 1)
            for j in range(y0, y1, 6):
                cv2.line(out, (x0, j), (x0, min(j + 3, y1)), col, 1)
                cv2.line(out, (x1, j), (x1, min(j + 3, y1)), col, 1)
        else:
            cv2.rectangle(out, (x0, y0), (x1, y1), col, thick, cv2.LINE_AA)
        label = (t.role.upper() + " " if t.role else "") + t.label
        cv2.putText(out, label, (x0, max(10, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
        if t.velocity_px and not dashed:
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            ex, ey = int(cx + t.velocity_px[0]), int(cy + t.velocity_px[1])
            if abs(ex - cx) + abs(ey - cy) > 3:
                cv2.arrowedLine(out, (cx, cy), (ex, ey), col, 1, cv2.LINE_AA, tipLength=0.3)
    tag = f"{ov.provenance}  #{ov.frame_id}  {ov.model_id or ''}  calib:{'OK' if ov.calibration_valid else 'NO'}"
    cv2.rectangle(out, (0, 0), (len(tag) * 8 + 10, 20), (0, 0, 0), -1)
    cv2.putText(out, tag, (5, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return out
