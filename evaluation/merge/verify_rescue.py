"""Headless rescue probe. All truth comparison is confined to evaluation."""
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import yaml
from overwatch.config import Config
from overwatch.evaluation.scenario_runner import HeadlessRunner


def run(sid):
    cfg = Config.model_validate(yaml.safe_load((ROOT / "config/default.yaml").read_text()))
    cfg.decisions.primary, cfg.decisions.shadow = "rules", []
    runner = HeadlessRunner(cfg, sid, engine="rules")
    beacon_error = None
    approved = False
    def step(r, t):
        nonlocal beacon_error, approved
        mission = r.pipeline.rescue
        if mission.beacon and beacon_error is None:
            who = next(a for a in r.sim.actors if a.role == "subject")
            beacon_error = math.dist(mission.beacon["xy"], who.pos)
        if mission.phase == "REVIEW" and not approved:
            mission.confirm("scripted simulated operator")
            approved = True
    res = runner.run(float(os.environ.get("VERIFY_DURATION", "115")), record_every_ms=1000, on_step=step)
    result = dict(scenario=sid, rescue=runner.pipeline.rescue.view(), beacon_error_m=beacon_error,
                  wall_s=res.wall_s, detector=runner.pipeline.detector.model_id)
    out = ROOT / "evaluation/merge/rescue"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{sid}.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    for sid in sys.argv[1:] or ["FR3", "FF3", "SR5"]:
        print(json.dumps(run(sid)), flush=True)
