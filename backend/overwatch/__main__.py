from __future__ import annotations

import uvicorn

from .config import load_config


def main() -> None:
    cfg = load_config()
    uvicorn.run("overwatch.main:app", host=cfg.server.host, port=cfg.server.port, log_level="info", ws="websockets")


if __name__ == "__main__":
    main()
