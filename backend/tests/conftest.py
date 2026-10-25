import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ.setdefault("OVERWATCH_DB", ":memory:")  # tests never touch the real incident database
