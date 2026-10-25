"""Entry point: ``uvicorn overwatch.main:app`` or ``python -m overwatch``."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .api.app import create_app

logging.basicConfig(level=os.environ.get("OVERWATCH_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

# test12 (a self-contained police drone dispatch scenario in the repo root): its page and API at /test12
sys.path.append(str(Path(__file__).resolve().parents[2]))
try:
    from test12.server import mount as _mount_test12

    _mount_test12(app)
except Exception:  # noqa: BLE001 - the rest of the app runs without it
    logging.getLogger(__name__).exception("test12 not mounted")
