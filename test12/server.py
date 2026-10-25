"""The /test12 page and its API, mounted on the Overwatch backend by ``overwatch/main.py``.

    POST /api/test12/start        {"seed": optional int, "spawn": optional [x, y]}: a new run (a random seed unless one
                                  is given; the suspect starts at the sidewalk nearest ``spawn``, else somewhere random)
    POST /api/test12/stop
    GET|POST /api/test12/view     {"view": "drone" | "chase" | "top"}: which camera the video shows (one for everyone)
    GET  /api/test12/state        the run as the page shows it (five times a second of simulation time)
    GET  /api/test12/world        the map for the page: streets, buildings, cover, trees, the base
    GET  /api/test12/stream.mjpg  the video at 30 fps: the drone camera with what the pipeline found drawn over it, or a
                                  simulator view (third-person, bird's-eye) with the drone in it
    GET  /api/test12/frame.jpg    the latest frame
    GET  /api/test12/voice        whether callouts can be spoken with ElevenLabs
    GET  /api/test12/voice/{run}/{n}.mp3  callout ``n`` of the current run, spoken (ElevenLabs, cached)
    GET  /test12                  the page

Voice: the drone's callouts to the police are read out on the page with ElevenLabs text-to-speech, through the
backend's own client (``overwatch/voice/tts.py``: the key from the repo's .env stays on the server, accounts fall back
automatically, audio is cached on disk). Only the current run's callouts can be spoken, one synthesis at a time, so
the public tunnel cannot be used to speak anything else; the page asks for the newest callout only when it has
finished saying the last one.

Runs happen in a child process (``live.py``): the simulation, YOLO on its own thread, and the worker processes that
draw the 30 fps frames. A run is paced to the wall clock, one physics step per video frame; the run waits for each
YOLO answer at its fixed 0.2 s latency, so a seed replays exactly as it does headless. This process only relays the
child's frames and page state, so the event loop that streams the video is never starved, and plays the frames out
on a steady 30 fps clock behind a ~170 ms buffer (they are made in bursts when the run catches up after a busy
moment, such as the search planner's once-a-second look ahead). While a run is going, the
main simulation (the demo at /) is paused so the two do not compete for the CPU, and resumed afterwards if it was
running.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import multiprocessing as mp
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .drone import CAMERA
from .sim import shared
from .world import CAR_HOME, PAD

log = logging.getLogger("test12")
WEB = Path(__file__).resolve().parent / "web"
FRAME_RING = 12
VIDEO_FPS = 30
VIEWS = ("drone", "chase", "top")
CALLOUTS_KEPT = 60
JITTER_FRAMES = 5  # frames held back (~170 ms) so the video plays out evenly although frames arrive in bursts
MAX_BACKLOG = 15  # past half a second behind, the oldest frames are dropped to catch up


class Manager:
    def __init__(self) -> None:
        self.rt = None
        self.lock = threading.Lock()
        self.proc = None
        self.conn = None
        self.run_no = 0
        self.ring: deque[tuple[int, bytes]] = deque(maxlen=FRAME_RING)  # (frame id, jpeg), newest last
        self.inbox: deque[bytes] = deque()  # frames from the child, waiting for their turn in the playout
        self.frame_id = 0
        self.state_json = json.dumps({"running": False, "phase": "IDLE"}).encode()
        self.view = "drone"
        self.callouts: dict[int, str] = {}  # the current run's callouts as they are said, by number
        self.voice_lock: asyncio.Lock | None = None
        self.started = False
        self.stop_ack = threading.Event()
        self.frame3d = None
        self._resume_main = False
        self._idle_jpeg: bytes | None = None
        self._world_json: bytes | None = None

    # -- the child process ---------------------------------------------------------------------------------------
    def _ensure_child(self) -> None:
        if self.proc is not None and self.proc.is_alive():
            return
        from . import live

        ctx = mp.get_context("spawn")  # not fork: this process runs the web server's threads
        here, there = ctx.Pipe()
        self.proc = ctx.Process(target=live.serve, args=(there,), name="test12-live")
        self.proc.start()
        self.conn = here
        if not here.poll(180) or here.recv()[0] != "ready":
            raise RuntimeError("the test12 run process did not start")
        threading.Thread(target=self._receive, args=(here,), daemon=True, name="test12-receive").start()
        threading.Thread(target=self._play_out, daemon=True, name="test12-playout").start()
        atexit.register(self._quit)

    def _receive(self, conn) -> None:
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                return
            kind, run_no = msg[0], msg[1] if len(msg) > 1 else None
            if run_no != self.run_no:
                continue  # a run that was replaced
            if kind == "frame":
                with self.lock:
                    self.inbox.append(msg[2])
            elif kind == "callout":
                with self.lock:
                    self.callouts[msg[2]] = msg[3]
                    for n in [n for n in self.callouts if n <= msg[2] - CALLOUTS_KEPT]:
                        del self.callouts[n]
            elif kind in ("state", "end"):
                with self.lock:
                    self.state_json = msg[2]
                    self.frame3d = json.loads(msg[2]).get("frame3d")
                if kind == "end":
                    self.started = False
                    self.stop_ack.set()
                    self._resume_main_if_paused()

    def _play_out(self) -> None:
        """Release the child's frames at an even 30 a second: they are made in bursts (the run catches up after a
        busy moment), so a few are held back and handed out on a steady clock."""
        period = 1.0 / VIDEO_FPS
        next_t = time.monotonic()
        buffering = True
        while True:
            now = time.monotonic()
            if now < next_t:
                time.sleep(next_t - now)
            elif now - next_t > 0.5:
                next_t = now  # after a long stall, start the clock again rather than rush
            next_t += period
            with self.lock:
                while len(self.inbox) > MAX_BACKLOG:
                    self.inbox.popleft()
                if buffering and len(self.inbox) < JITTER_FRAMES:
                    continue
                if not self.inbox:
                    buffering = True  # ran dry: build up a little again before playing on
                    continue
                buffering = False
                self.frame_id += 1
                self.ring.append((self.frame_id, self.inbox.popleft()))

    def _quit(self) -> None:
        try:
            if self.conn is not None:
                self.conn.send(("quit",))
            if self.proc is not None:
                self.proc.join(timeout=3)
                if self.proc.is_alive():
                    self.proc.terminate()
        except Exception:  # noqa: BLE001 - shutting down anyway
            pass

    # -- runs ----------------------------------------------------------------------------------------------------
    def start(self, seed: int | None, spawn: list[float] | None = None) -> int:
        self._ensure_child()
        seed = int(seed) if seed is not None else random.randrange(1, 100000)
        with self.lock:
            self.stop_ack.clear()
            self.frame3d = None
            self.run_no += 1
            self.started = True
            self.inbox.clear()
            self.callouts.clear()
            self.state_json = json.dumps({"running": True, "phase": "REPORTED", "seed": seed, "t": 0.0,
                                          "run_no": self.run_no, "view": self.view}).encode()
        self._pause_main()
        self.conn.send(("start", self.run_no, seed, spawn, self.view))
        return seed

    def set_view(self, view: str) -> None:
        self.view = view
        if self.conn is not None:
            self.conn.send(("view", view))

    def tts(self):
        """The backend's ElevenLabs client (built by the main runtime from the repo's .env), if there is one."""
        tts = getattr(self.rt, "tts", None)
        return tts if tts is not None and tts.available else None

    def stop(self) -> None:
        if self.conn is not None and self.started:
            self.stop_ack.clear()
            self.conn.send(("stop", self.run_no))
            if not self.stop_ack.wait(8):
                raise RuntimeError("City process did not acknowledge stop; main simulation remains paused")

    def latest_jpeg(self) -> bytes | None:
        with self.lock:
            return self.ring[-1][1] if self.ring else None

    def _pause_main(self) -> None:
        rt = self.rt
        if rt is None or getattr(rt, "sim", None) is None:
            return
        try:
            if not rt.paused:
                rt.control("pause")
                self._resume_main = True
        except Exception:  # noqa: BLE001 - the main simulation is optional here
            log.warning("could not pause the main simulation", exc_info=True)

    def _resume_main_if_paused(self) -> None:
        if self._resume_main and self.rt is not None:
            try:
                self.rt.control("resume")
            except Exception:  # noqa: BLE001
                log.warning("could not resume the main simulation", exc_info=True)
            self._resume_main = False

    # -- before any run ------------------------------------------------------------------------------------------
    def idle_jpeg(self) -> bytes:
        """Before any run: the drone's camera on the pad, looking along the apron."""
        if self._idle_jpeg is None:
            S = shared()
            from overwatch.geometry.camera import CameraPose, PinholeCamera

            from .render import police_car

            cam = PinholeCamera(CAMERA, CameraPose(np.array([PAD[0] - 18.0, PAD[1] - 18.0, 14.0]), 45.0, -32.0))
            img = S["renderer"].render(cam, [], vehicles=[police_car(CAR_HOME[0], CAR_HOME[1], 90.0)]).image
            text = "SIMULATED  police station: drone pad and patrol car. Press Start simulation."
            cv2.putText(img, text, (14, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, text, (14, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
            self._idle_jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])[1].tobytes()
        return self._idle_jpeg

    def world_json(self) -> bytes:
        if self._world_json is None:
            S = shared()
            w = S["world"]
            boxes = []
            for b in w.boxes:
                if b.kind in ("pad_mark",):
                    continue
                cs = b.corners()
                kind = "station" if b.oid == "police-station" else b.kind
                boxes.append([kind, round(float(cs[:, 0].min()), 2), round(float(cs[:, 0].max()), 2),
                              round(float(cs[:, 1].min()), 2), round(float(cs[:, 1].max()), 2),
                              round(b.z0 + b.size_lwh[2], 1)])
            g = w.streets
            streets = [[e["name"], e["axis"], e["kind"], list(g["nodes"][e["a"]]), list(g["nodes"][e["b"]])] for e in g["edges"]]
            self._world_json = json.dumps({
                "bounds": w.bounds, "pad": PAD, "car_home": CAR_HOME, "streets": streets,
                "road_width": {"ew": 8.0, "ns": 7.0}, "lane_offset": {"ew": 6.0, "ns": 5.5},
                "boxes": boxes, "trees": [[round(t.xy[0], 1), round(t.xy[1], 1), round(t.canopy_r, 1)] for t in w.trees],
                "spots": [s.as_dict() for s in S["spots"]], "layout": S["layout"],
                "labels": {n: lab for n, lab in g["labels"].items() if "&" in lab and n in g["nodes"]},
                "nodes": g["nodes"],
            }, separators=(",", ":")).encode()
        return self._world_json


MGR = Manager()


class StartBody(BaseModel):
    seed: int | None = None
    spawn: list[float] | None = None


class ViewBody(BaseModel):
    view: str


def mount(app: FastAPI) -> None:
    """Adds the routes ahead of the dashboard's catch-all route, so /test12 is not served the dashboard."""
    MGR.rt = getattr(app.state, "rt", None)
    if MGR.rt is not None:
        MGR.rt.external_simulator_stop = MGR.stop
    def authorized(connection):
        token = MGR.rt.cfg.secrets.overwatch_token if MGR.rt is not None else None
        got = connection.query_params.get("token") or connection.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return not token or got == token

    def auth(request: Request):
        if not authorized(request):
            raise HTTPException(401, "access token required")

    r = APIRouter(dependencies=[Depends(auth)])
    pages = APIRouter()

    @r.post("/api/test12/start")
    def start(body: StartBody | None = None) -> dict[str, Any]:
        if MGR.rt is None or MGR.rt.mode != "SIMULATED":
            raise HTTPException(409, "City chase runs only in the labelled simulation mode")
        spawn = body.spawn if body else None
        if spawn is not None and (len(spawn) != 2 or not all(np.isfinite(spawn))):
            raise HTTPException(422, "spawn is [x, y] in metres")
        seed = MGR.start(body.seed if body else None, spawn)
        return {"ok": True, "seed": seed}

    @r.get("/api/test12/view")
    def get_view() -> dict[str, Any]:
        return {"view": MGR.view}

    @r.post("/api/test12/view")
    def set_view(body: ViewBody) -> dict[str, Any]:
        if body.view not in VIEWS:
            raise HTTPException(422, f"view is one of {', '.join(VIEWS)}")
        MGR.set_view(body.view)
        return {"ok": True, "view": MGR.view}

    @r.get("/api/test12/voice")
    def voice_status() -> dict[str, Any]:
        tts = MGR.tts()
        return {"elevenlabs": tts is not None, **({"status": tts.status()} if tts is not None else {})}

    @r.get("/api/test12/voice/{run_no}/{n}.mp3")
    async def voice(run_no: int, n: int) -> Response:
        with MGR.lock:
            text = MGR.callouts.get(n) if run_no == MGR.run_no else None
        if text is None:
            raise HTTPException(404, "no such callout in the current run")
        tts = MGR.tts()
        if tts is None:
            raise HTTPException(503, "ElevenLabs is not available")
        if MGR.voice_lock is None:
            MGR.voice_lock = asyncio.Lock()
        async with MGR.voice_lock:  # one synthesis at a time
            audio = await tts.speak(text)
        if not audio:
            raise HTTPException(503, "ElevenLabs did not answer")
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=600"})

    @r.post("/api/test12/stop")
    def stop() -> dict[str, Any]:
        MGR.stop()
        return {"ok": True}

    @r.get("/api/test12/state")
    def state() -> Response:
        with MGR.lock:
            data = MGR.state_json
        return Response(data, media_type="application/json", headers={"Cache-Control": "no-store"})

    @r.get("/api/test12/world")
    def world() -> Response:
        return Response(MGR.world_json(), media_type="application/json")

    @r.get("/api/test12/frame.jpg")
    def frame() -> Response:
        return Response(MGR.latest_jpeg() or MGR.idle_jpeg(), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    def part(data: bytes) -> bytes:
        return b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + \
            data + b"\r\n"

    @r.get("/api/test12/stream.mjpg")
    async def stream() -> StreamingResponse:
        async def gen():
            last_id, idle_at = 0, 0.0
            while True:
                if not MGR.started:  # before the first run: the pad, again every second (a browser shows a part of the
                    if time.monotonic() - idle_at > 1.0:  # stream only once the next one begins)
                        idle_at = time.monotonic()
                        yield part(await asyncio.to_thread(MGR.idle_jpeg))
                    await asyncio.sleep(0.1)
                    continue
                with MGR.lock:
                    new = [(i, d) for i, d in MGR.ring if i > last_id]
                if len(new) > 8:  # this viewer fell well behind (a slow network): skip ahead rather than lag
                    new = new[-3:]
                for i, d in new:  # every frame, in order: the full 30 a second
                    last_id = i
                    yield part(d)
                await asyncio.sleep(0.008)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @pages.get("/test12")
    def page() -> FileResponse:
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    @pages.get("/test12/{name}")
    def asset(name: str) -> FileResponse:
        f = (WEB / name).resolve()
        if f.parent != WEB or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-store"})

    @r.get("/api/test12/ortho.jpg")
    def ortho():
        image = shared()["renderer"].levels[0].image
        return Response(cv2.imencode(".jpg", image)[1].tobytes(), media_type="image/jpeg")

    @pages.websocket("/ws/test12/sim3d")
    async def sim3d_socket(ws: WebSocket):
        if not authorized(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            from .sim3d import world as world3d
            payload = await asyncio.to_thread(lambda: world3d(shared()))
            await ws.send_json(payload)
            while True:
                with MGR.lock:
                    current = MGR.frame3d
                if current:
                    await ws.send_json(current)
                await asyncio.sleep(.2)
        except (WebSocketDisconnect, RuntimeError):
            return

    # the route objects go straight in, just before the dashboard's catch-all (include_router may wrap them in one
    # entry appended after it, where GET /test12 would never be reached); every other route keeps its place
    routes = app.router.routes
    at = next((i for i, rt in enumerate(routes) if getattr(rt, "path", None) == "/{path:path}"), len(routes))
    routes[at:at] = [*r.routes, *pages.routes]
