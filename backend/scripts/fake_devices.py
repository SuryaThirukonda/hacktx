#!/usr/bin/env python
"""Live-path harness: pretend to be the DJI bridge iPhone and the responder iPhone.

Runs the simulator locally and speaks the real device protocols (docs/contracts.md) to a backend running in LIVE
mode: binary JPEG (or H.264 with --h264) frames + telemetry on /ws/bridge, observations + JPEG on /ws/phone,
answers clock-sync pings from a deliberately offset device clock, and applies the virtual-stick commands the Mac
sends back to its local simulated drone (closed loop over the network). Frames are declared synthetic, so the
backend labels them SYNTHETIC rather than LIVE.

    OVERWATCH_MODE=LIVE make backend          # terminal 1
    python scripts/fake_devices.py --operator # terminal 2 (emulates the scripted operator via REST)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import load_config  # noqa: E402
from overwatch.faults import FaultInjector  # noqa: E402
from overwatch.schemas import BridgeCommand  # noqa: E402
from overwatch.sim.driver import SimulationDriver  # noqa: E402

DEVICE_CLOCK_OFFSET_MS = 123_456.0  # device clocks are never the server clock


def device_ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000.0 + DEVICE_CLOCK_OFFSET_MS


def frame_msg(kind: int, ts: float, payload: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">d", ts) + payload


def post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=3).read())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1:8000")
    ap.add_argument("--scenario", default="demo")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--h264", action="store_true", help="send raw H.264 access units (needs PyAV)")
    ap.add_argument("--operator", action="store_true", help="start an incident and replay scripted operator clicks via REST")
    ap.add_argument("--token", default=None)
    a = ap.parse_args()
    cfg = load_config()
    sim = SimulationDriver(cfg, FaultInjector(), a.scenario)
    q = f"?token={a.token}" if a.token else ""
    base = f"http://{a.host}"
    t0 = time.monotonic()
    stats = {"frames": 0, "obs": 0, "tel": 0, "cmds": 0, "alerts": 0}
    encoder = None
    if a.h264:
        import av

        encoder = av.CodecContext.create("libx264", "w")
        encoder.width, encoder.height = cfg.sources.drone.width, cfg.sources.drone.height
        encoder.pix_fmt = "yuv420p"
        encoder.options = {"tune": "zerolatency", "preset": "ultrafast", "bf": "0"}

    async with websockets.connect(f"ws://{a.host}/ws/bridge{q}", max_size=2 ** 24) as bridge, \
            websockets.connect(f"ws://{a.host}/ws/phone{q}", max_size=2 ** 24) as phone:
        await bridge.send(json.dumps({"type": "hello", "device": "fake-bridge", "synthetic": True}))
        await phone.send(json.dumps({"type": "hello", "device": "fake-responder", "synthetic": True}))
        if a.operator:
            print("incident:", post(base, "/api/incident", {"consent": True}).get("incident_id"))

        async def bridge_rx() -> None:
            async for raw in bridge:
                m = json.loads(raw)
                if m.get("type") == "sync":
                    t1 = device_ms(t0)
                    await bridge.send(json.dumps({"type": "sync_reply", "t0": m["t0"], "t1": t1, "t2": device_ms(t0)}))
                elif m.get("type") in ("vstick", "gimbal", "mode", "hover", "rtl", "estop", "ping", "sim_start", "sim_stop"):
                    sim.bridge.send(BridgeCommand.model_validate(m))
                    stats["cmds"] += 1

        async def phone_rx() -> None:
            async for raw in phone:
                m = json.loads(raw)
                if m.get("type") == "sync":
                    t1 = device_ms(t0)
                    await phone.send(json.dumps({"type": "sync_reply", "t0": m["t0"], "t1": t1, "t2": device_ms(t0)}))
                elif m.get("type") == "alert":
                    stats["alerts"] += 1
                    print(f"  [phone] {m['severity']:>15s}  {m['spoken_text'] or m['text']}")
                    if m.get("ack_required"):
                        await phone.send(json.dumps({"type": "ack", "alert_id": m["alert_id"]}))

        async def operator_action(act) -> None:
            loop = asyncio.get_running_loop()
            for attempt in range(8):  # retry like a human would until the person has a confirmed track
                await asyncio.sleep(0.5)
                if act.action in ("lock_subject", "lock_responder"):
                    gt = sim.last_gt.get("actors", {}).get(act.arg)
                    if not (gt and gt.get("box_xyxy")):
                        continue
                    b = gt["box_xyxy"]
                    body = {"u": (b[0] + b[2]) / 2, "v": (b[1] + b[3]) / 2, "action": act.action}
                    r = await loop.run_in_executor(None, post, base, "/api/click", body)
                    if r.get("ok"):
                        print(f"  [operator] {act.action} {act.arg}: {r}")
                        return
                elif act.action == "autonomy":
                    r = await loop.run_in_executor(None, post, base, "/api/autonomy/mode", {"mode": act.arg})
                    print("  [operator] autonomy:", r.get("mode"))
                    return

        rx = [asyncio.create_task(bridge_rx()), asyncio.create_task(phone_rx())]
        dt = 1.0 / sim.WORLD_HZ
        nxt = time.monotonic()
        last_print = 0.0
        while sim.t_s < a.duration:
            out = sim.step(dt, True)
            dev_now = device_ms(t0)
            for act in out.actions if a.operator else []:
                asyncio.create_task(operator_action(act))  # never stall the device streams
            if out.telemetry:
                tel = out.telemetry.telemetry.model_copy(update={"capture_ts_ms": dev_now, "source_id": "fake-bridge"})
                await bridge.send(tel.model_dump_json())
                stats["tel"] += 1
            if out.drone_frame:
                img = out.drone_frame.image
                if encoder is not None:
                    import av

                    vf = av.VideoFrame.from_ndarray(img, format="bgr24")
                    for pkt in encoder.encode(vf):
                        await bridge.send(frame_msg(0x02, dev_now, bytes(pkt)))
                else:
                    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    await bridge.send(frame_msg(0x01, dev_now, buf.tobytes()))
                stats["frames"] += 1
            if out.phone:
                if out.phone.rgb is not None:
                    ok, buf = cv2.imencode(".jpg", out.phone.rgb, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    await phone.send(frame_msg(0x01, dev_now, buf.tobytes()))
                obs = out.phone.observation.model_copy(update={"capture_ts_ms": dev_now})
                await phone.send(obs.model_dump_json())
                stats["obs"] += 1
            if sim.t_s - last_print >= 5.0:
                last_print = sim.t_s
                print(f"t={sim.t_s:5.1f}s sent frames={stats['frames']} obs={stats['obs']} tel={stats['tel']} "
                      f"| received cmds={stats['cmds']} alerts={stats['alerts']} | drone vstick={sim.drone.vstick_enabled}")
            nxt += dt
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
        for t in rx:
            t.cancel()
    print("done", stats)


if __name__ == "__main__":
    asyncio.run(main())
