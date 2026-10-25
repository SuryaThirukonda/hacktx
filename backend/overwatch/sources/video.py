"""One interface over every drone-video source, with honest connection states.

Sources:
* ``simulated``: the built-in simulator (SIMULATED mode); always labelled simulated.
* ``file``: an MP4/MOV played at its own frame rate with media timestamps (LIVE mode backend, provenance replay).
* ``webcam``: a local camera index (LIVE mode, provenance live).
* ``rtsp``: an RTSP/RTMP/HTTP stream with reconnect and backoff (LIVE mode, provenance live).
* ``dji_mini2``: the iPhone bridge app over DJI Mobile SDK V4 (4.16.2). Connected only when a real bridge has said
  hello and is sending; a test harness that declares synthetic imagery is reported as simulated, never live.
* ``near_ir``: a near-infrared-capable camera, file or stream (phase 16); images, never temperatures.

Switching between the simulator and real inputs needs a backend restart in the other mode; the API says so instead
of pretending.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..incident.models import SensorSource

if TYPE_CHECKING:
    from ..runtime import Runtime

SourceChoice = Literal["simulated", "file", "webcam", "rtsp", "dji_mini2", "near_ir"]
CHOICES: list[str] = ["simulated", "file", "webcam", "rtsp", "dji_mini2", "near_ir"]


class SourceError(ValueError):
    pass


def _iso(ms_ago: float | None) -> datetime | None:
    if ms_ago is None:
        return None
    return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - ms_ago / 1000.0, timezone.utc)


def classify_url(url: str) -> str:
    if url.isdigit():
        return "webcam"
    if "://" in url:
        return "rtsp"
    return "file"


class VideoSourceManager:
    def __init__(self, rt: "Runtime") -> None:
        self.rt = rt
        self.active: str = "simulated" if rt.mode == "SIMULATED" else ("replay" if rt.mode == "REPLAY" else "dji_mini2")
        self.value: str | None = None
        if rt.mode == "LIVE" and rt.cfg.sources.drone.url:
            self.value = str(rt.cfg.sources.drone.url)
            self.active = classify_url(self.value)

    # -- status ----------------------------------------------------------------------------------------------
    def _age(self) -> float | None:
        p = self.rt.pipeline
        c = p.counters.get("drone")
        if c is None or c.last_rx_ms is None:
            return None
        return max(0.0, self.rt.clock.now_ms() - c.last_rx_ms)

    def status(self, choice: str) -> SensorSource:
        rt = self.rt
        age = self._age()
        fresh = age is not None and age < 2000
        if choice == "simulated":
            on = rt.mode == "SIMULATED"
            return SensorSource(id="simulated", kind="simulation", name="Simulator (SIMULATED)", source_mode="simulated",
                                connection_status="connected" if on else "not_configured",
                                last_seen_at=_iso(age) if on else None,
                                detail=f"scenario {rt.sim.scenario.id}" if on and rt.sim else
                                "restart the backend in SIMULATED mode to use the simulator")
        if choice == "dji_mini2":
            br = getattr(rt.live, "bridge", None) if rt.live else None
            if br is None:
                return SensorSource(id="dji_mini2", kind="drone_rgb", name="DJI Mini 2 bridge", source_mode="live",
                                    connection_status="not_configured",
                                    detail="needs the LIVE backend and the iOS bridge app (DJI MSDK V4); not verified "
                                           "on hardware yet")
            now = rt.clock.now_ms()
            up = br.connected(now)
            synthetic = bool(getattr(br, "synthetic", False))
            return SensorSource(id="dji_mini2", kind="drone_rgb", name="DJI Mini 2 bridge",
                                source_mode="simulated" if synthetic else "live",
                                connection_status="connected" if up else "disconnected",
                                last_seen_at=_iso(now - br.last_rx_ms) if up else None,
                                detail="test harness (synthetic imagery)" if synthetic else
                                ("JPEG relay connected: video/ML only; updated native bridge required for control"
                                 if up and getattr(br, "legacy", False) else
                                 "bridge connected: pilot in control (re-arm on phone to allow autonomy)"
                                 if up and getattr(br, "control_inhibited", False) else
                                 "bridge connected" if up else "waiting for the bridge app to connect"))
        if choice in ("file", "webcam", "rtsp", "near_ir"):
            url_src = getattr(rt.live, "url_source", None) if rt.live else None
            mine = url_src is not None and self.active == choice
            mode = "replay" if choice == "file" else "live"
            if not mine:
                return SensorSource(id=choice, kind="near_ir" if choice == "near_ir" else "drone_rgb",
                                    name={"file": "Video file", "webcam": "Webcam", "rtsp": "RTSP stream",
                                          "near_ir": "Near-IR camera"}[choice], source_mode=mode,  # type: ignore[arg-type]
                                    connection_status="not_configured" if rt.mode != "LIVE" else "disconnected",
                                    detail="select it with a path/URL (LIVE backend)" if rt.mode == "LIVE" else
                                    "restart the backend in LIVE mode (make backend-live) to use real video")
            extra = ""
            if choice == "near_ir" and hasattr(url_src, "enhancement"):
                steps = url_src.enhancement.steps()
                extra = " · near-IR, not thermal · " + (f"image processing: {', '.join(steps)}" if steps else "raw")
            return SensorSource(id=choice, kind="near_ir" if choice == "near_ir" else "drone_rgb",
                                name=self.value or choice, source_mode=mode,  # type: ignore[arg-type]
                                connection_status="connected" if fresh else "disconnected",
                                last_seen_at=_iso(age) if fresh else None,
                                calibration={"reconnects": url_src.reconnects},
                                detail=f"{url_src.url} · reconnects {url_src.reconnects}{extra}")
        raise SourceError(f"unknown source {choice!r}; choose one of {', '.join(CHOICES)}")

    def list(self) -> dict[str, Any]:
        return {"active": self.active, "value": self.value, "mode": self.rt.mode,
                "items": [self.status(c).model_dump(mode="json") for c in CHOICES]}

    # -- switching -------------------------------------------------------------------------------------------
    def _disarm_control(self) -> None:
        rt = self.rt
        if rt.mode == "LIVE":
            with rt.pipeline.lock:
                rt.cfg.autonomy.allow_real_flight = False
                rt.pipeline.flight_confirmed_track_id = None
                rt.pipeline.autonomy.set_mode("MANUAL", rt.clock.now_ms(), "video source changed: explicit re-arm required")

    def select(self, choice: str, value: str | None = None) -> SensorSource:
        rt = self.rt
        if choice not in CHOICES:
            raise SourceError(f"unknown source {choice!r}; choose one of {', '.join(CHOICES)}")
        if choice == "simulated":
            if rt.mode != "SIMULATED":
                raise SourceError("the simulator needs the backend in SIMULATED mode (make backend)")
            if value:
                rt.control("restart", value)
            self.active, self.value = "simulated", value
            return self.status("simulated")
        if choice == "dji_mini2":
            if rt.mode != "LIVE":
                raise SourceError("the DJI bridge needs the backend in LIVE mode (make backend-live)")
            self._disarm_control()
            self._stop_url()
            self.active, self.value = "dji_mini2", None
            return self.status("dji_mini2")
        # file / webcam / rtsp / near_ir
        if rt.mode != "LIVE":
            raise SourceError("real video needs the backend in LIVE mode: make backend-live "
                              "OVERWATCH_DRONE_URL=<file|webcam index|rtsp://...>")
        if not value:
            raise SourceError(f"{choice} needs a value (path, webcam index or URL)")
        kind = classify_url(value)
        if choice in ("file", "webcam", "rtsp") and kind != choice:
            raise SourceError(f"{value!r} looks like a {kind}, not a {choice}")
        if kind == "file" and not Path(value).expanduser().exists():
            raise SourceError(f"file not found: {value}")
        from .live import URLFrameSource
        from .near_ir import NearIRVideoSource

        self._disarm_control()
        self._stop_url()
        cls = NearIRVideoSource if choice == "near_ir" else URLFrameSource
        src = cls(rt, str(Path(value).expanduser()) if kind == "file" else value)
        rt.live.url_source = src
        src.start()
        self.active, self.value = choice, value
        return self.status(choice)

    def _stop_url(self) -> None:
        cur = getattr(self.rt.live, "url_source", None) if self.rt.live else None
        if cur is not None:
            cur.stop()
            self.rt.live.url_source = None
