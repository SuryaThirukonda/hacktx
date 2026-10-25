"""Vercel entrypoint: the Overwatch FastAPI app (backend/overwatch/main.py), found by Vercel as ``app`` in app.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from overwatch.main import app  # noqa: E402,F401
