#!/usr/bin/env python3
"""One LIVE runtime on :8000, plus an optional :8765 TCP alias for the existing iPhone relay."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import uvicorn
import yaml

from overwatch.api.app import create_app
from overwatch.config import load_config


async def relay_port(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int) -> None:
    upstream = None
    try:
        remote_reader, upstream = await asyncio.open_connection("127.0.0.1", port)

        async def copy(src, dst):
            try:
                while data := await src.read(64 * 1024):
                    dst.write(data)
                    await dst.drain()
            finally:
                dst.close()

        tasks = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(remote_reader, writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def run(args) -> None:
    if args.config:
        os.environ["OVERWATCH_CONFIG"] = args.config
    cfg = load_config()
    cfg.session.mode = "LIVE"
    cfg.sources.drone.adapter = "bridge"
    cfg.sources.drone.url = None
    cfg.perception.detector = "ultralytics"
    cfg.perception.device = args.vision_device
    cfg.decisions.primary = args.engine
    cfg.decisions.shadow = []
    cfg.decisions.allow_mock = False
    cfg.autonomy.allow_real_flight = False  # Website enable is required after every start or reconnect.
    cfg.autonomy.motion_profile = args.motion_profile
    if args.real_flight and args.motion_profile == "full":
        cfg.autonomy.follow_max_speed_mps = min(1.0, cfg.autonomy.follow_max_speed_mps)
        cfg.autonomy.reacquire_max_speed_mps = min(1.0, cfg.autonomy.reacquire_max_speed_mps)
        cfg.autonomy.max_vertical_mps = min(0.5, cfg.autonomy.max_vertical_mps)
        cfg.autonomy.max_yaw_rate_dps = min(15.0, cfg.autonomy.max_yaw_rate_dps)
    if not args.config:
        cfg.world.geo_reference = None  # simulator coordinates must not place a real drone on a map
    if args.real_flight and args.motion_profile == "full" and (not args.config or cfg.world.geo_reference is None):
        raise ValueError("--real-flight requires --config with the actual site's geo_reference and geofence")
    if args.real_flight and args.motion_profile == "full":
        site = yaml.safe_load(Path(args.config).read_text())
        if not site.get("autonomy", {}).get("geofence") or len(cfg.autonomy.geofence) < 3:
            raise ValueError("site config must explicitly define an autonomy.geofence polygon")
    if args.real_flight and args.engine == "typesafe" and not cfg.secrets.typesafe_api_key:
        raise ValueError("TYPESAFE_API_KEY is required for Jev real-flight mode")
    if args.real_flight and args.engine == "openai" and not cfg.secrets.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI real-flight mode")
    alias = None
    if args.legacy_port:
        if args.legacy_port == args.port:
            raise ValueError("legacy port must differ from backend port")
        # Bind before starting the runtime, so a conflicting receiver.py fails clearly.
        alias = await asyncio.start_server(lambda r, w: relay_port(r, w, args.port), "0.0.0.0", args.legacy_port)
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host="0.0.0.0", port=args.port, log_level="info"))
    try:
        await server.serve()
    finally:
        if alias:
            alias.close()
            await alias.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--legacy-port", type=int, default=8765, help="0 disables the compatibility port")
    parser.add_argument("--vision-device", choices=["cpu", "auto", "mps", "cuda"], default="cpu",
                        help="cpu avoids the native Apple Metal crash observed on this Mac; accelerators are opt-in")
    parser.add_argument("--engine", choices=["openai", "typesafe", "rules"], default="openai")
    parser.add_argument("--motion-profile", choices=["camera_only", "visual_follow", "full"], default="camera_only",
                        help="camera_only rotates and tilts the camera; all translation is zero")
    parser.add_argument("--config", help="site YAML, merged over defaults")
    parser.add_argument("--real-flight", action="store_true", help="compatibility flag for site checks; startup stays disarmed until the website enables control")
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
