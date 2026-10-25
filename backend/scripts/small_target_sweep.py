#!/usr/bin/env python
"""Compare aerial person detector settings on a frozen VisDrone image list.

This is a person-only exploratory scorer, not the official VisDrone evaluator.
It follows the source taxonomy and masks ignored regions before scoring.
Outputs must be written outside the Git checkout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from overwatch.vision.detectors import TiledDetector, UltralyticsDetector

Box = tuple[float, float, float, float]


def intersection(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def intersection_over_detection(a: Box, b: Box) -> float:
    area = (a[2] - a[0]) * (a[3] - a[1])
    return intersection(a, b) / area if area > 0 else 0.0


def clipped_box(x: int, y: int, w: int, h: int, width: int, height: int) -> Box | None:
    box = (float(max(0, x)), float(max(0, y)), float(min(width, x + w)), float(min(height, y + h)))
    return box if box[2] > box[0] and box[3] > box[1] else None


@dataclass
class Annotations:
    people: list[Box]
    others: list[Box]
    ignore_integral: np.ndarray | None
    removed_people: int
    categories: Counter

    def ignored_coverage(self, box: Box) -> float:
        if self.ignore_integral is None:
            return 0.0
        height, width = self.ignore_integral.shape[0] - 1, self.ignore_integral.shape[1] - 1
        x0 = max(0, min(width, math.floor(box[0])))
        y0 = max(0, min(height, math.floor(box[1])))
        x1 = max(0, min(width, math.ceil(box[2])))
        y1 = max(0, min(height, math.ceil(box[3])))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        integral = self.ignore_integral
        covered = int(integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0])
        return covered / ((x1 - x0) * (y1 - y0))


def read_annotations(path: Path, width: int, height: int) -> Annotations:
    people, others, ignored_regions = [], [], []
    categories = Counter()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split(",")
        if len(fields) != 8:
            raise ValueError(f"{path}:{line_number}: expected 8 VisDrone fields, got {len(fields)}")
        try:
            x, y, w, h, score, category, _truncation, _occlusion = map(int, fields)
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: non-integer field") from error
        categories[(score, category)] += 1
        box = clipped_box(x, y, w, h, width, height)
        if box is None:
            continue
        if category == 0:
            ignored_regions.append(box)
        elif category == 11 or score == 0:
            others.append(box)
        elif category in (1, 2):
            people.append(box)
    integral = None
    if ignored_regions:
        mask = np.zeros((height, width), dtype=np.uint8)
        for x0, y0, x1, y1 in ignored_regions:
            mask[int(y0):int(y1), int(x0):int(x1)] = 1
        integral = cv2.integral(mask)
    annotations = Annotations(people, others, integral, 0, categories)
    kept = [box for box in people if annotations.ignored_coverage(box) < 0.5]
    annotations.removed_people = len(people) - len(kept)
    annotations.people = kept
    return annotations


def score_frame(predictions: list[tuple[Box, float]], annotations: Annotations,
                threshold: float, match_iou: float = 0.5) -> dict[str, int]:
    candidates = [(box, confidence) for box, confidence in predictions if confidence >= threshold]
    candidates.sort(key=lambda item: -item[1])
    used: set[int] = set()
    tp = fp = ignored = small_hits = 0
    for box, _confidence in candidates:
        # VisDrone excludes detections mostly inside category-0 ignored regions.
        if annotations.ignored_coverage(box) >= 0.5:
            ignored += 1
            continue
        best_index = None
        best_iou = match_iou
        for index, truth in enumerate(annotations.people):
            overlap = iou(box, truth)
            if index not in used and overlap >= best_iou:
                best_index, best_iou = index, overlap
        if best_index is not None:
            used.add(best_index)
            tp += 1
            small_hits += int(annotations.people[best_index][3] - annotations.people[best_index][1] < 32)
        elif any(intersection_over_detection(box, other) >= 0.5 for other in annotations.others):
            ignored += 1
        else:
            fp += 1
    small_total = sum(box[3] - box[1] < 32 for box in annotations.people)
    return {"tp": tp, "fp": fp, "fn": len(annotations.people) - tp, "small_hits": small_hits,
            "small_total": small_total, "ignored_predictions": ignored, "predictions": len(candidates)}


@dataclass
class Frame:
    name: str
    image: np.ndarray
    annotations: Annotations


def load_frames(source_root: Path, manifest: Path) -> tuple[list[Frame], dict]:
    definition = json.loads(manifest.read_text(encoding="utf-8"))
    names = definition["images"]
    if len(names) != len(set(names)) or len(names) != definition["image_count"]:
        raise ValueError("manifest image list contains duplicates or an incorrect count")
    digest = hashlib.sha256("\n".join(names).encode()).hexdigest()
    if digest != definition["filename_list_sha256"]:
        raise ValueError("manifest image list hash changed")
    frames = []
    for name in names:
        image = cv2.imread(str(source_root / "images" / name))
        if image is None:
            raise ValueError(f"missing or unreadable image: {name}")
        height, width = image.shape[:2]
        annotation_path = source_root / "annotations" / (Path(name).stem + ".txt")
        frames.append(Frame(name, image, read_annotations(annotation_path, width, height)))
    return frames, definition


def parse_config(name: str) -> dict:
    direct = re.fullmatch(r"([ns])(\d+)", name)
    if direct:
        size = int(direct.group(2))
        if size not in (640, 960, 1280, 1536):
            raise ValueError(f"unsupported image size: {size}")
        return {"name": name, "checkpoint": f"yolo26{direct.group(1)}.pt", "imgsz": size,
                "tile_px": None, "overlap": None, "full_frame": None}
    tiled = re.fullmatch(r"t(\d+)o(\d+)(nofull)?", name)
    if tiled:
        tile_px, overlap = int(tiled.group(1)), int(tiled.group(2)) / 100
        if tile_px not in (512, 640, 768) or not 0 <= overlap <= 0.4:
            raise ValueError(f"unsupported tiling setting: {name}")
        return {"name": name, "checkpoint": "yolo26n.pt", "imgsz": 640,
                "tile_px": tile_px, "overlap": overlap, "full_frame": tiled.group(3) is None}
    raise ValueError(f"unknown configuration: {name}")


def predict(model, image: np.ndarray, config: dict, confidence: float) -> list[tuple[Box, float]]:
    if config["tile_px"] is not None:
        return [(d.box, d.confidence) for d in model.detect(image)]
    result = model.predict(image, imgsz=config["imgsz"], conf=confidence, classes=[0],
                           device=0, verbose=False)[0]
    if result.boxes is None:
        return []
    return [(tuple(map(float, box)), float(score))
            for box, score in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy())]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--weights-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--configs", required=True, help="comma-separated names, e.g. n640,n960,t640o20")
    parser.add_argument("--thresholds", default="0.10,0.15,0.20,0.25")
    args = parser.parse_args()
    thresholds = [float(value) for value in args.thresholds.split(",")]
    if not thresholds or min(thresholds) <= 0 or max(thresholds) >= 1:
        parser.error("thresholds must lie strictly between 0 and 1")
    import torch
    from ultralytics import YOLO
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this sweep")
    configs = [parse_config(value) for value in args.configs.split(",")]
    args.output_dir.mkdir(parents=True, exist_ok=False)
    frames, definition = load_frames(args.source_root, args.manifest)
    if not frames:
        raise ValueError("empty manifest")
    rows, per_frame = [], []
    predictions_path = args.output_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as prediction_file:
        for config in configs:
            checkpoint = args.weights_root / config["checkpoint"]
            if not checkpoint.is_file():
                raise FileNotFoundError(checkpoint)
            print(f"Starting {config['name']}: {len(frames)} frames", flush=True)
            if config["tile_px"] is None:
                model = YOLO(str(checkpoint))
                if model.names[0] != "person":
                    raise ValueError(f"{checkpoint} class 0 is {model.names[0]!r}, expected 'person'")
            else:
                base = UltralyticsDetector(checkpoint.as_posix(), config["imgsz"], min(thresholds),
                                           ["person"], "cuda")
                model = TiledDetector(base, tile_px=config["tile_px"], overlap=config["overlap"],
                                      full_frame=config["full_frame"])
            for frame in frames[:3]:
                predict(model, frame.image, config, min(thresholds))
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            latencies = []
            scores = {threshold: Counter() for threshold in thresholds}
            for index, frame in enumerate(frames):
                torch.cuda.synchronize()
                start = time.perf_counter()
                detections = predict(model, frame.image, config, min(thresholds))
                torch.cuda.synchronize()
                latency_ms = (time.perf_counter() - start) * 1000
                latencies.append(latency_ms)
                prediction_file.write(json.dumps({
                    "config": config["name"], "image": frame.name,
                    "detections": [[*box, confidence] for box, confidence in detections],
                }) + "\n")
                for threshold in thresholds:
                    outcome = score_frame(detections, frame.annotations, threshold)
                    scores[threshold].update(outcome)
                    per_frame.append({"config": config["name"], "threshold": threshold, "image": frame.name,
                                      "latency_ms": round(latency_ms, 3), **outcome})
                if (index + 1) % 24 == 0:
                    print(f"  {index + 1}/{len(frames)}", flush=True)
            latency = np.asarray(latencies)
            memory = round(torch.cuda.max_memory_allocated() / 2**20, 2)
            for threshold in thresholds:
                totals = scores[threshold]
                tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
                small_hits, small_total = totals["small_hits"], totals["small_total"]
                rows.append({
                    **config, "confidence": threshold, "frames": len(frames),
                    "people": tp + fn, "small_people": small_total,
                    "tp": tp, "fp": fp, "fn": fn, "small_hits": small_hits,
                    "ignored_predictions": totals["ignored_predictions"],
                    "precision": round(tp / (tp + fp), 5) if tp + fp else None,
                    "recall": round(tp / (tp + fn), 5) if tp + fn else None,
                    "small_recall": round(small_hits / small_total, 5) if small_total else None,
                    "fp_per_image": round(fp / len(frames), 5),
                    "mean_ms": round(float(latency.mean()), 3),
                    "p50_ms": round(float(np.median(latency)), 3),
                    "p95_ms": round(float(np.percentile(latency, 95)), 3),
                    "fps_serial": round(1000 / float(latency.mean()), 2),
                    "peak_allocated_mb": memory,
                    "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                })
            print(f"  done: p50 {np.median(latency):.2f} ms, p95 {np.percentile(latency, 95):.2f} ms",
                  flush=True)
            del model
            torch.cuda.empty_cache()
    audit = {
        "source_root": str(args.source_root), "manifest": str(args.manifest),
        "filename_list_sha256": definition["filename_list_sha256"],
        "image_count": len(frames), "dataset": definition["dataset"],
        "protocol": "VisDrone categories 1+2 -> person; category 0 union mask excludes >=50% covered "
                    "GT/detections; category 11 unmatched detections ignored at IoA>=0.5; "
                    "greedy confidence-ordered one-to-one IoU>=0.5 matching; small box height <32 pixels",
        "inference_confidence": min(thresholds),
        "latency_note": "one CUDA-synchronized detector call at minimum threshold; higher thresholds are "
                        "offline filters on the same returned boxes",
        "removed_people_in_ignored_regions": sum(frame.annotations.removed_people for frame in frames),
        "source_category_counts": {
            f"score{score}_class{category}": count
            for (score, category), count in sum((frame.annotations.categories for frame in frames), Counter()).items()
        },
    }
    (args.output_dir / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (args.output_dir / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with (args.output_dir / "results.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (args.output_dir / "per_frame.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(per_frame[0]))
        writer.writeheader()
        writer.writerows(per_frame)
    print(f"Saved {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
