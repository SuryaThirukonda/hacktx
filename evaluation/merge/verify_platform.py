"""Paid-service-free scenario regression of the repaired combined source."""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def run(sid):
    import cv2
    import yaml
    cv2.setNumThreads(1)
    from overwatch.config import Config
    from overwatch.evaluation.scenario_runner import HeadlessRunner
    from overwatch.evaluation.gt_eval import evaluate
    cfg = Config.model_validate(yaml.safe_load((ROOT / "config/default.yaml").read_text()))
    cfg.decisions.primary, cfg.decisions.shadow = "rules", []
    runner = HeadlessRunner(cfg, sid, engine="rules")
    result = runner.run()
    report = evaluate(result, runner.sim.scenario)
    out = ROOT / "evaluation/merge/platform"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{sid}.json").write_text(json.dumps(report, indent=2))
    return {k: report[k] for k in ["scenario", "expected_pass", "wall_s", "subject_lock", "autonomy"]}


if __name__ == "__main__":
    for sid in sys.argv[1:] or ["PP0", "PP8", "PP5", "PP6", "PP7", "SR4"]:
        print(json.dumps(run(sid)), flush=True)
