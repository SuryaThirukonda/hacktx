"""Local simulation workspace; safe to spawn City's existing render/detector workers."""
import argparse
import os
import sys

import uvicorn

from overwatch.api.app import create_app
from overwatch.config import REPO_ROOT, Secrets, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    os.environ.setdefault("OVERWATCH_DB", str(REPO_ROOT / "data/merged-demo.db"))
    cfg = load_config()
    cfg.session.mode, cfg.session.scenario = "SIMULATED", "FR3"
    cfg.decisions.primary, cfg.decisions.shadow, cfg.decisions.allow_mock = "rules", [], False
    cfg.secrets = Secrets()
    cfg.autonomy.allow_real_flight = False
    app = create_app(cfg)
    sys.path.insert(0, str(REPO_ROOT))
    from test12.server import mount
    mount(app)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
