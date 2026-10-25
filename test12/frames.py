"""The 30 fps camera video: frames drawn in parallel by worker processes.

One frame takes about 25 to 40 ms to render on this CPU, too slow to draw 30 a second on the simulation's own thread,
so each frame's scene (camera pose, people, police car: ``Run.scene()``) and overlay (``Run.hud_items()``) go to a
small pool of processes that render, draw the overlay and encode a JPEG. Frames come back in order. The simulation
itself never waits for the video: if the pool falls behind, frames are dropped, not delayed.

Workers are started with ``spawn`` (not fork: the server process already runs YOLO's threads) and memory-map the
ground texture from its cache in ``data/cache``, so a worker is ready in well under a second.

A simulator view (third-person or bird's-eye) is two renders per frame: the view itself, with the drone drawn in it,
and the drone's camera at 384 x 216 for the inset in its corner.
"""

from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import Future, ProcessPoolExecutor

_renderer = None


def _init() -> None:
    import cv2

    import test12  # noqa: F401  (puts backend/ on the path)

    from .render import DepthRenderer
    from .world import world_bundle

    cv2.setNumThreads(1)
    w, _, _, tex = world_bundle()
    global _renderer
    _renderer = DepthRenderer(w, tex)


def _frame(scene: dict, hud: dict, quality: int) -> bytes:
    from .hud import draw_hud, encode_jpeg, paste_inset
    from .sim import render_scene

    res = render_scene(_renderer, scene, with_gt=False)
    img = draw_hud(res.image, hud)
    if scene.get("inset") is not None:
        small = render_scene(_renderer, scene, with_gt=False, view="inset").image
        img = paste_inset(img, draw_hud(small, hud.get("inset", {})))
    return encode_jpeg(img, quality)


def _ready() -> bool:
    import time

    time.sleep(0.3)  # long enough that the pool starts every worker for these, not one worker for all of them
    return _renderer is not None


class FramePool:
    def __init__(self, workers: int = 3, quality: int = 80) -> None:
        from .world import world_bundle

        world_bundle()  # paints and caches the ground texture once, before the workers memory-map it
        self.quality = quality
        self.workers = workers
        self.ex = ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn"), initializer=_init)
        for f in [self.ex.submit(_ready) for _ in range(workers)]:
            f.result(timeout=120)  # every worker up before the first run needs it

    def submit(self, scene: dict, hud: dict) -> Future:
        return self.ex.submit(_frame, scene, hud, self.quality)

    def shutdown(self) -> None:
        self.ex.shutdown(wait=False, cancel_futures=True)
