"""Loopback SAR demo with synthetic sensors, rules, no cloud keys and physical permission off."""
import argparse
import os

import uvicorn

from overwatch.api.app import create_app
from overwatch.config import REPO_ROOT, Secrets, load_config


def build_app():
    os.environ.setdefault("OVERWATCH_DB", str(REPO_ROOT / "data" / "sar-demo.db"))
    cfg = load_config()
    cfg.session.mode, cfg.session.scenario = "SIMULATED", "SAR1"
    cfg.decisions.primary, cfg.decisions.shadow, cfg.decisions.allow_mock = "rules", [], False
    cfg.secrets = Secrets()
    cfg.autonomy.allow_real_flight = False
    cfg.perception.detector = "sim_blob"
    app = create_app(cfg)
    app.state.rt.incidents.create("Simulated SAR assistance", "search", "SAR1")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port)
