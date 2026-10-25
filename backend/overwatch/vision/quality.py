"""Conservative quality gates from observed tracker measurements, without claiming semantic occlusion detection."""
from __future__ import annotations

import math
from statistics import median

from .tracker import Track
from .detectors import Detection


def measurement_problems(target: Track | None, tracks: list[Track], image_size: tuple[int, int],
                         high_confidence: float, detections: list[Detection] | None = None) -> tuple[str, ...]:
    if target is None or not target.matched_now or target.measured_box is None or target.state != "CONFIRMED":
        return ("no current confirmed measurement",)
    reasons = []
    box = target.measured_box
    if not all(math.isfinite(x) for x in box):
        return ("nonfinite bounding box",)
    x0, y0, x1, y1 = box
    width, height = image_size
    w, h = x1-x0, y1-y0
    if w <= 0 or h <= 0:
        return ("invalid bounding box",)
    if x0 <= .02*width or x1 >= .98*width or y0 <= .02*height or y1 >= .98*height:
        reasons.append("clipped measurement")
    measured_conf = next((d.confidence for d in detections or [] if d.box == box), target.conf)
    if not math.isfinite(measured_conf) or measured_conf < high_confidence:
        reasons.append("low detector score")
    if target.reacquired:
        reasons.append("first measurement after a tracking gap")
    # History contains this frame's measurement last. Reject abrupt partial boxes/posture/association changes.
    previous = [b for _, b in list(target.history)[-4:-1] if b[2] > b[0] and b[3] > b[1]]
    if previous:
        pw = median(b[2]-b[0] for b in previous)
        ph = median(b[3]-b[1] for b in previous)
        if not (.65 <= w/pw <= 1.5 and .65 <= h/ph <= 1.5 and .7 <= (w/h)/(pw/ph) <= 1.4):
            reasons.append("abrupt box size or aspect change")
    # Use detections before tracker fragment filtering too: a suppressed overlapping track still makes
    # a person's measured box ambiguous. Detector scores here are raw, not the track's smoothed score.
    boxes = ([d.box for d in detections if d.class_name == "person"] if detections is not None else
             [t.measured_box or t.box for t in tracks if t.matched_now and t.class_name == "person"])
    skipped_target = False
    for other_box in boxes:
        if not skipped_target and other_box == box:
            skipped_target = True
            continue
        a0, b0, a1, b1 = other_box
        intersection = max(0, min(x1, a1)-max(x0, a0)) * max(0, min(y1, b1)-max(y0, b0))
        if intersection / (w*h) > .2:
            reasons.append("overlapping person measurements")
            break
    return tuple(reasons)
