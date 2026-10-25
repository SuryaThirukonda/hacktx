#!/usr/bin/env python
"""Detector benchmark (Rescue Copilot §15.3 / Overwatch §7.2): same frames, same thresholds, every model.

    # real footage (accuracy only if you pass labels; otherwise latency + detection counts, marked NOT EVALUATED)
    python scripts/benchmark_models.py --source sessions/demo/drone.mp4 --models yolo26n,yolo26s,rfdetr --imgsz 640,960
    python scripts/benchmark_models.py --source frames_dir/ --labels labels_coco.json
    # synthetic frames with ground truth (labelled synthetic; validates nothing about real video)
    python scripts/benchmark_models.py --synthetic demo --models sim_blob,yolo26s
    # small overhead targets (scenario S09, 20 -> 95 m climb): plain vs SAHI-style tiled inference
    python scripts/benchmark_models.py --synthetic S09 --stride 5 --iou 0.3 --models yolo26n,yolo26n+tiles

A ``+tiles`` suffix wraps the model in ``TiledDetector`` (640 px tiles, 20 % overlap, plus one full-frame pass).
Recall is also reported by ground-truth box height, the number that matters for high-altitude footage.

Writes evaluation/reports/models-<stamp>/{results.json, per_frame.csv, summary.md}. Models that fail to load are
reported as NOT RUN, never as zero accuracy.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import REPO_ROOT, load_config  # noqa: E402
from overwatch.evaluation.gt_eval import SIZE_BINS  # noqa: E402
from overwatch.vision.detectors import RFDETRDetector, SimBlobDetector, TiledDetector, UltralyticsDetector  # noqa: E402

WEIGHTS = REPO_ROOT / "backend" / "weights"


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def frames_from_source(src: str, limit: int):
    p = Path(src)
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png"))[:limit]
        for f in files:
            img = cv2.imread(str(f))
            if img is not None:
                yield f.name, img, None
        return
    cap = cv2.VideoCapture(src)
    i = 0
    while i < limit:
        ok, img = cap.read()
        if not ok:
            break
        yield f"frame_{i:06d}", img, None
        i += 1


def frames_from_sim(scenario: str, limit: int, stride: int = 3):
    from overwatch.faults import FaultInjector
    from overwatch.sim.driver import SimulationDriver

    drv = SimulationDriver(load_config(), FaultInjector(), scenario)
    n = 0
    k = 0
    while n < limit and drv.t_s < drv.scenario.duration_s:
        out = drv.step(1 / 30)
        f = out.drone_frame
        if f is None:
            continue
        k += 1
        if k % stride:
            continue
        gts = [v["box_xyxy"] for v in f.gt["actors"].values() if v["in_frame"] and v["visibility"] >= 0.5 and v["box_xyxy"]]
        yield f"{scenario}_{f.frame_id:05d}", f.image, gts
        n += 1


def frames_from_composite(limit: int, seed: int = 0):
    """Real person pixels at small scale: the two photos bundled with Ultralytics are shrunk so their people are
    8-64 px tall and pasted onto a 1920x1080 canvas. Labels are YOLO26s detections on the full-size originals
    (pseudo-labels). This isolates target size only - not overhead viewpoint, motion blur or real backgrounds."""
    import ultralytics

    assets = Path(ultralytics.__file__).parent / "assets"
    labeler = UltralyticsDetector(str(WEIGHTS / "yolo26s.pt") if (WEIGHTS / "yolo26s.pt").exists() else "yolo26s.pt",
                                  imgsz=1280, conf=0.5, classes=["person"], device="auto")
    photos = []
    for name in ("bus.jpg", "zidane.jpg"):
        img = cv2.imread(str(assets / name))
        if img is None:
            continue
        boxes = [d.box for d in labeler.detect(img)]
        if boxes:
            photos.append((img, boxes, max(b[3] - b[1] for b in boxes)))
    if not photos:
        raise RuntimeError("no labelled source photos")
    rng = np.random.default_rng(seed)
    for i in range(limit):
        canvas = np.full((1080, 1920, 3), (92, 118, 104), np.uint8)
        canvas = cv2.add(canvas, rng.integers(0, 18, canvas.shape, dtype=np.uint8))
        gts, occupied = [], []
        for _ in range(40):
            if len(occupied) >= 7:
                break
            img, boxes, tallest = photos[int(rng.integers(len(photos)))]
            target = float(np.exp(rng.uniform(np.log(8.0), np.log(64.0))))  # tallest person height in px
            sc = target / tallest
            h, w = max(1, int(img.shape[0] * sc)), max(1, int(img.shape[1] * sc))
            x, y = int(rng.integers(0, 1920 - w)), int(rng.integers(0, 1080 - h))
            if any(x < ox1 and ox0 < x + w and y < oy1 and oy0 < y + h for ox0, oy0, ox1, oy1 in occupied):
                continue
            canvas[y:y + h, x:x + w] = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            occupied.append((x, y, x + w, y + h))
            gts += [[x + b[0] * sc, y + b[1] * sc, x + b[2] * sc, y + b[3] * sc] for b in boxes]
        yield f"composite_{i:04d}", canvas, gts


def load_coco(path: str) -> dict[str, list]:
    d = json.loads(Path(path).read_text())
    cats = {c["id"]: c["name"] for c in d.get("categories", [])}
    by_img = {im["id"]: im["file_name"] for im in d["images"]}
    out: dict[str, list] = {}
    for a in d["annotations"]:
        if cats.get(a["category_id"], "person") != "person":
            continue
        x, y, w, h = a["bbox"]
        out.setdefault(Path(by_img[a["image_id"]]).stem, []).append([x, y, x + w, y + h])
    return out


def build(name: str, imgsz: int, conf: float):
    if name.endswith("+tiles"):
        return TiledDetector(build(name[: -len("+tiles")], imgsz, conf), tile_px=640, overlap=0.2, full_frame=True)
    if name == "sim_blob":
        return SimBlobDetector()
    if name.startswith("rfdetr"):
        return RFDETRDetector(size=name.split("_")[1] if "_" in name else "small", conf=conf)
    w = name if name.endswith(".pt") else f"{name}.pt"
    path = WEIGHTS / w
    return UltralyticsDetector(str(path) if path.exists() else w, imgsz=imgsz, conf=conf, classes=["person"], device="auto")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="video file or directory of images")
    ap.add_argument("--synthetic", help="simulator scenario id (frames + ground truth)")
    ap.add_argument("--composite", action="store_true",
                    help="small real-photo people pasted on a canvas (pseudo-labels; isolates target size)")
    ap.add_argument("--labels", help="COCO JSON with person boxes; file_name stems must match frame names")
    ap.add_argument("--models", default="yolo26n,yolo26s,sim_blob")
    ap.add_argument("--imgsz", default="640,960")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--stride", type=int, default=3, help="use every Nth simulator frame (covers longer scenarios)")
    ap.add_argument("--output-dir", help="write reports here instead of inside the repository")
    a = ap.parse_args()
    if not (a.source or a.synthetic or a.composite):
        ap.error("pass --source, --synthetic or --composite")
    labels = load_coco(a.labels) if a.labels else None
    if a.composite:
        frames = list(frames_from_composite(a.frames))
    elif a.synthetic:
        frames = list(frames_from_sim(a.synthetic, a.frames, a.stride))
    else:
        frames = list(frames_from_source(a.source, a.frames))
    if labels is not None:
        frames = [(n, img, labels.get(n, [])) for n, img, _ in frames]
    has_gt = any(g is not None for _, _, g in frames)
    if a.composite:
        label = "composite (real person pixels, scaled; pseudo-labels)"
    else:
        label = "synthetic" if a.synthetic else ("replayed real (labelled)" if has_gt else "replayed real (unlabelled)")
    out_dir = (Path(a.output_dir) if a.output_dir else
               REPO_ROOT / "evaluation" / "reports" / f"models-{time.strftime('%Y%m%d-%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {"python": platform.python_version(), "machine": platform.machine(), "processor": platform.processor(),
           "opencv": cv2.__version__}
    try:
        import torch

        env["torch"] = torch.__version__
        env["mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        env["cuda"] = torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        env["torch"] = "not installed"
    results = []
    rows = []
    for model in a.models.split(","):
        sizes = [int(s) for s in a.imgsz.split(",")] if not model.startswith("sim_blob") else [0]
        for sz in sizes:
            name = model if sz == 0 else f"{model}@{sz}"
            try:
                det = build(model, sz or 640, a.conf)
            except Exception as e:  # noqa: BLE001
                results.append({"model": name, "status": "NOT RUN", "reason": f"{type(e).__name__}: {e}"})
                print(f"{name:18s} NOT RUN ({e})")
                continue
            for _, img, _ in frames[:3]:
                det.detect(img)  # warm-up excluded from timing
            lat, tp, fp, fn, ndet = [], 0, 0, 0, 0
            size_hits = {k: [0, 0] for k, _, _ in SIZE_BINS}
            for fname, img, gts in frames:
                t0 = time.perf_counter()
                dets = det.detect(img)
                ms = (time.perf_counter() - t0) * 1000.0
                lat.append(ms)
                ndet += len(dets)
                ftp = ffp = ffn = None
                if gts is not None:
                    used = set()
                    ftp = 0
                    for d in sorted(dets, key=lambda x: -x.confidence):
                        best, bi = None, a.iou
                        for gi, g in enumerate(gts):
                            v = iou(d.box, g)
                            if gi not in used and v >= bi:
                                best, bi = gi, v
                        if best is None:
                            fp += 1
                        else:
                            used.add(best)
                            tp += 1
                            ftp += 1
                    ffn = len(gts) - len(used)
                    for gi, g in enumerate(gts):
                        k = next(k for k, lo, hi in SIZE_BINS if lo <= g[3] - g[1] < hi)
                        size_hits[k][1] += 1
                        size_hits[k][0] += int(gi in used)
                    ffp = len(dets) - ftp
                    fn += ffn
                rows.append([name, fname, round(ms, 2), len(dets), ftp, ffp, ffn])
            arr = np.array(lat)
            r = {"model": name, "model_id": det.model_id, "status": "OK", "frames": len(frames),
                 "latency_ms": {"p50": round(float(np.percentile(arr, 50)), 2), "p95": round(float(np.percentile(arr, 95)), 2),
                                "mean": round(float(arr.mean()), 2)},
                 "fps": round(1000.0 / float(arr.mean()), 1), "detections_per_frame": round(ndet / max(1, len(frames)), 2)}
            if has_gt:
                r["precision"] = round(tp / (tp + fp), 3) if tp + fp else None
                r["recall"] = round(tp / (tp + fn), 3) if tp + fn else None
                r["iou_threshold"] = a.iou
                r["recall_by_size"] = {k: {"hits": h, "total": n, "recall": round(h / n, 3) if n else None}
                                       for k, (h, n) in size_hits.items() if n}
            else:
                r["accuracy"] = "NOT EVALUATED (no labels)"
            results.append(r)
            print(f"{name:18s} p50 {r['latency_ms']['p50']:7.1f} ms  fps {r['fps']:6.1f}  "
                  f"P {r.get('precision', '-')}  R {r.get('recall', '-')}  det/frame {r['detections_per_frame']}")
    src_name = "composite" if a.composite else (a.source or f"sim:{a.synthetic}")
    (out_dir / "results.json").write_text(json.dumps({"label": label, "env": env, "source": src_name,
                                                      "conf": a.conf, "results": results}, indent=2), encoding="utf-8")
    with (out_dir / "per_frame.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model", "frame", "ms", "detections", "tp", "fp", "fn"])
        w.writerows(rows)
    lines = [f"# Detector benchmark ({label})", "", f"Source: `{src_name}` · frames: {len(frames)} · "
             f"conf ≥ {a.conf} · env: {env}", "", "| Model | p50 ms | p95 ms | FPS | Precision | Recall |", "|---|---|---|---|---|---|"]
    for r in results:
        if r["status"] != "OK":
            lines.append(f"| {r['model']} | NOT RUN | | | | |")
            continue
        lines.append(f"| {r['model']} | {r['latency_ms']['p50']} | {r['latency_ms']['p95']} | {r['fps']} | "
                     f"{r.get('precision', 'not evaluated')} | {r.get('recall', 'not evaluated')} |")
    sized = [r for r in results if r.get("recall_by_size")]
    if sized:
        bins = [k for k, _, _ in SIZE_BINS if any(k in r["recall_by_size"] for r in sized)]
        lines += ["", f"Recall by ground-truth box height (IoU ≥ {a.iou}):", "",
                  "| Model | " + " | ".join(bins) + " |", "|---|" + "---|" * len(bins)]
        for r in sized:
            cells = []
            for k in bins:
                v = r["recall_by_size"].get(k)
                cells.append(f"{v['recall']} ({v['hits']}/{v['total']})" if v else "")
            lines.append(f"| {r['model']} | " + " | ".join(cells) + " |")
    if a.composite:
        lines += ["", "Composite frames: the two photos bundled with Ultralytics, shrunk and pasted. Labels are YOLO26s "
                  "detections on the full-size originals. Only target size varies; this is not overhead footage."]
    if label == "synthetic":
        lines += ["", "Synthetic frames validate the pipeline, not real-video detector accuracy. YOLO was trained on "
                  "photographs, so its numbers on cartoon renders say little about real footage either way."]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("report:", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
