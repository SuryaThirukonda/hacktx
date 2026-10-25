"""test12: a police drone dispatch, simulated end to end.

A crime is reported (a person in a red shirt, running, last seen at a street corner). The DJI Mini 2 stand-in climbs
from the drone pad at the police station, searches where the person could be by now, finds them with YOLO (a person
box whose torso is red), follows them from behind with the camera tilted and reports their position relative to the
base by dead reckoning. A police car leaves at the first report and catches up about 20 s later; the run ends when the
drone's YOLO sees the police car next to the person.

Everything here is simulated and labelled so. The map copies the committed pursuit world's street layout and adds
places to hide; the backend only mounts ``test12.server`` at ``/test12``.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = str(REPO_ROOT / "backend")
if _BACKEND not in sys.path:  # the backend runs from backend/; scripts here may not
    sys.path.append(_BACKEND)
