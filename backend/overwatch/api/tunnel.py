"""A Cloudflare quick tunnel to this server, started on demand from the demo page, so a phone anywhere can open the
target controller. ``cloudflared`` prints the public address a few seconds after it starts; it is kept here and the
dashboard turns it into a QR code. The address is public for as long as the tunnel runs."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "cloudflared"
    return str(local) if local.exists() and os.access(local, os.X_OK) else None


class Tunnel:
    def __init__(self, port: int = 8000) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self.started_at: float | None = None
        self.error: str | None = None
        self.log_tail: list[str] = []
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self.proc is not None and self.proc.poll() is None
            return {"available": find_cloudflared() is not None, "running": running, "url": self.url if running else None,
                    "starting": running and self.url is None, "error": self.error,
                    "uptime_s": round(time.time() - self.started_at) if running and self.started_at else None,
                    "note": "a Cloudflare quick tunnel: the address is public while it runs"}

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return self.status()
            binary = find_cloudflared()
            if binary is None:
                self.error = "cloudflared not found: install it from github.com/cloudflare/cloudflared/releases"
                return self.status()
            self.url, self.error, self.log_tail = None, None, []
            self.proc = subprocess.Popen([binary, "tunnel", "--url", f"http://localhost:{self.port}", "--no-autoupdate"],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.started_at = time.time()
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
            log.info("cloudflared quick tunnel starting for port %d", self.port)
        return self.status()

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            with self._lock:
                self.log_tail = (self.log_tail + [line])[-40:]
                m = URL_RE.search(line)
                if m and self.url is None:
                    self.url = m.group(0)
                    log.info("cloudflared tunnel up: %s", self.url)
                if "ERR" in line and "failed" in line.lower() and self.proc is proc:
                    self.error = line[-200:]
        with self._lock:
            if self.proc is proc:
                self.url = None

    def stop(self) -> dict[str, Any]:
        with self._lock:
            proc, self.proc, self.url, self.error = self.proc, None, None, None  # a stop is not an error
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return self.status()
