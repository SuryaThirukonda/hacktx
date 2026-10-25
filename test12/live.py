"""The live run in its own process.

The simulation's Python work (the belief, the planner, line of sight) holds the interpreter lock for long stretches;
inside the web server that starved the event loop that streams the video, so frames arrived at 10 to 20 a second
although 30 were made. Here a child process owns everything a run needs: the run itself, YOLO on its own thread, and
the pool of processes that draw the 30 fps frames. It sends finished JPEG frames and the page state to the server
through a pipe; the server only relays them.

Messages, child to server: ("ready",), ("frame", run_no, jpeg), ("state", run_no, json), ("end", run_no, json),
("callout", run_no, n, speech) (each callout to the police, for the page's voice).
Server to child: ("start", run_no, seed, spawn, view), ("view", view), ("stop", run_no), ("quit",).

The video shows one view at a time, for everyone watching: the drone's camera (its detection frames are reused, so
only five in six frames are drawn by the pool), or a simulator view, third-person or bird's-eye (every frame drawn by
the pool, with the drone's camera as an inset).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

FPS_STATE_EVERY = 6  # frames per page state: 5 a second
MAX_IN_FLIGHT = 18  # frames the render pool may be working on (room for a catch-up burst); past that a frame is dropped

log = logging.getLogger("test12.live")


def serve(conn, workers: int = 3, quality: int = 80, yolo_threads: int = 6) -> None:
    """The child process's main: load once, then run whatever the server asks for."""
    for k, v in (("OMP_NUM_THREADS", yolo_threads), ("MKL_NUM_THREADS", yolo_threads), ("OPENBLAS_NUM_THREADS", 1)):
        os.environ.setdefault(k, str(v))  # numpy's BLAS stays single-threaded; YOLO gets its threads
    import test12  # noqa: F401  (puts backend/ on the path)

    from .frames import FramePool
    from .sim import shared
    from .vision import PersonDetector

    detector = PersonDetector(threads=yolo_threads)
    shared()
    frames = FramePool(workers, quality)
    conn.send(("ready",))
    queued = None
    ctl = {"view": "drone"}
    while True:
        msg = queued or conn.recv()
        queued = None
        if msg[0] == "start":
            ctl["view"] = msg[4]
            queued = _run(conn, msg[1], msg[2], msg[3], ctl, detector, frames)
        elif msg[0] == "view":
            ctl["view"] = msg[1]
        elif msg[0] == "quit":
            frames.shutdown()
            return


def _run(conn, run_no: int, seed: int, spawn, ctl: dict, detector, frames):
    """One run, paced to the wall clock. Returns a message that arrived meanwhile and is for the idle loop."""
    from concurrent.futures import Future, ThreadPoolExecutor

    from .hud import draw_hud, encode_jpeg
    from .sim import FPS, Run

    yolo = ThreadPoolExecutor(1, thread_name_prefix="test12-yolo")
    detector.calls, detector.total_ms = 0, 0.0
    detector.log.clear()
    run = Run(seed, lambda img, t, crop: yolo.submit(detector.detect, img, t, crop), asynchronous=True, spawn=spawn)
    pending: deque = deque()
    emitted: deque = deque(maxlen=90)
    dropped = 0
    speed = 1.0
    leftover = None

    def video_fps() -> float:
        p = list(emitted)
        return (len(p) - 1) / (p[-1] - p[0]) if len(p) > 10 and p[-1] > p[0] else 0.0

    def state(extra: dict | None = None) -> bytes:
        st = run.state()
        st["run_no"] = run_no
        st["view"] = ctl["view"]
        st["speed"] = round(speed, 2)
        st["running"] = not run.done and not stopped
        st["video"] = {"fps": round(video_fps(), 1), "target_fps": FPS, "dropped": dropped}
        st["detector"]["yolo_ms_mean"] = round(detector.total_ms / max(detector.calls, 1), 1)
        st["detector"]["hz"] = FPS // 6
        if extra:
            st.update(extra)
        return json.dumps(st, separators=(",", ":")).encode()

    def next_frame() -> None:
        nonlocal dropped
        view = ctl["view"]
        hud = run.hud_items(view)
        if view == "drone" and run.frame is not None and run.frame[0] == run.frame_no:  # YOLO's frame, just drawn
            pending.append(encode_jpeg(draw_hud(run.frame[1], hud), frames.quality))
        elif sum(1 for p in pending if isinstance(p, Future)) < MAX_IN_FLIGHT:
            pending.append(frames.submit(run.scene(view), hud))
        else:
            dropped += 1

    said = 0

    def send_callouts() -> None:
        nonlocal said
        for c in run.callouts[said:]:
            conn.send(("callout", run_no, c["n"], c["speech"]))
        said = len(run.callouts)

    def drain(wait: bool = False) -> None:
        while pending:
            head = pending[0]
            if isinstance(head, Future):
                if not head.done() and not wait:
                    return
                try:
                    data = head.result(timeout=5)
                except Exception:  # noqa: BLE001 - one bad frame must not stop the video
                    log.warning("frame failed", exc_info=True)
                    data = b""
            else:
                data = head
            pending.popleft()
            if data:
                conn.send(("frame", run_no, data))
                emitted.append(time.monotonic())

    stopped = False
    slow: list = []  # steps over 60 ms: (t, phase before, phase after, ms), reported with the run's end
    try:
        run.render()
        next_frame()
        drain()
        conn.send(("state", run_no, state()))
        wall0, sim0 = time.monotonic(), run.t
        while not run.done:
            while conn.poll():  # a stop (or a new start) from the server, or another view
                msg = conn.recv()
                if msg[0] == "view":
                    ctl["view"] = msg[1]
                elif msg[0] in ("stop", "start", "quit"):
                    stopped = True
                    leftover = msg if msg[0] != "stop" else None
            if stopped:
                run.stop()
                break
            t_step = time.perf_counter()
            phase = run.phase
            run.step()
            send_callouts()
            next_frame()
            drain()
            ms = (time.perf_counter() - t_step) * 1000
            if ms > 60:
                slow.append((round(run.t, 2), phase, run.phase, round(ms)))
            if run.frame_no % FPS_STATE_EVERY == 0:
                conn.send(("state", run_no, state()))
            ahead = (run.t - sim0) - (time.monotonic() - wall0)
            if ahead > 0:
                time.sleep(ahead)
            elif ahead < -2.0:  # far behind: stop trying to catch up, just run as fast as the CPU allows
                wall0, sim0 = time.monotonic(), run.t
            if run.frame_no % FPS == 0:
                speed = (run.t - sim0) / max(time.monotonic() - wall0, 1e-3)
        drain(wait=True)
        summary = run.summary({"yolo_calls": detector.calls,
                               "yolo_ms_mean": round(detector.total_ms / max(detector.calls, 1), 1)})
        extra = {"summary": summary, "stopped_by_user": stopped}
        extra["slow_steps"] = sorted(slow, key=lambda s: -s[3])[:25]
        conn.send(("end", run_no, state(extra)))
    except Exception:  # noqa: BLE001 - keep the child alive for the next run; the page shows this one failed
        log.exception("test12 run failed")
        conn.send(("end", run_no, json.dumps({"running": False, "phase": "ERROR", "seed": seed,
                                              "error": "the run failed: see the server log"}).encode()))
    finally:
        yolo.shutdown(wait=False)
    return leftover
