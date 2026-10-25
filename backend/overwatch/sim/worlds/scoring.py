"""Ground-truth scoring helpers for the simulation worlds (evaluation only; the pipeline never sees ground truth)."""

from __future__ import annotations

from typing import Any

from ...evaluation.gt_eval import _iou

IOU_HIT = 0.2
VIS_MIN = 0.5


def actor_found(res: Any, actor_id: str) -> dict[str, Any]:
    """First time a pipeline track overlaps the actor (IoU >= 0.2 while visibility >= 0.5) and how often it does."""
    first = None
    visible = hits = 0
    for st in res.steps:
        g = (st.gt.get("actors") or {}).get(actor_id)
        if not g or not g.get("box_xyxy") or g.get("visibility", 0) < VIS_MIN:
            continue
        visible += 1
        if any(t["box"] and _iou(t["box"], g["box_xyxy"]) >= IOU_HIT for t in st.tracks if t["state"] != "LOST"):
            hits += 1
            if first is None:
                first = round(st.t_ms / 1000.0, 1)
    return {"found": int(first is not None), "first_detected_s": first, "visible_steps": visible,
            "detected_fraction_when_visible": round(hits / visible, 3) if visible else None}


def false_tracks(res: Any, min_steps: int = 5) -> int:
    """Confirmed tracks that never overlap any actor: candidates a reviewer would have to reject."""
    seen: dict[str, int] = {}
    matched: set[str] = set()
    for st in res.steps:
        gts = [g["box_xyxy"] for g in (st.gt.get("actors") or {}).values() if g.get("box_xyxy")]
        for t in st.tracks:
            if t["state"] != "CONFIRMED" or not t["box"]:
                continue
            seen[t["id"]] = seen.get(t["id"], 0) + 1
            if any(_iou(t["box"], b) >= IOU_HIT for b in gts):
                matched.add(t["id"])
    return sum(1 for k, n in seen.items() if n >= min_steps and k not in matched)


def false_objects(res: Any, min_steps: int = 5, merge_m: float = 4.0) -> int:
    """False candidate tracks merged by ground position: one object seen on two passes counts once."""
    seen: dict[str, int] = {}
    matched: set[str] = set()
    last_xy: dict[str, tuple[float, float]] = {}
    for st in res.steps:
        gts = [g["box_xyxy"] for g in (st.gt.get("actors") or {}).values() if g.get("box_xyxy")]
        for t in st.tracks:
            if t["state"] != "CONFIRMED" or not t["box"]:
                continue
            seen[t["id"]] = seen.get(t["id"], 0) + 1
            if t.get("xy") is not None:
                last_xy[t["id"]] = (float(t["xy"][0]), float(t["xy"][1]))
            if any(_iou(t["box"], b) >= IOU_HIT for b in gts):
                matched.add(t["id"])
    objs: list[tuple[float, float]] = []
    unplaced = 0
    for k, n in seen.items():
        if n < min_steps or k in matched:
            continue
        xy = last_xy.get(k)
        if xy is None:
            unplaced += 1
        elif all((xy[0] - o[0]) ** 2 + (xy[1] - o[1]) ** 2 > merge_m ** 2 for o in objs):
            objs.append(xy)
    return len(objs) + unplaced
