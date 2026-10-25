#!/usr/bin/env python
"""LIVE dashboard coexistence test with no bridge, aircraft, credentials or flight permission."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from e2e_demo import BACKEND, REPO, CHROME, KEYS_OFF, get, wait_health


def main() -> int:
    out = Path(os.environ.get("OVERWATCH_E2E_OUTPUT", str(REPO / "evaluation/current/browser-live")))
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="overwatch-live-ui-"))
    env = {**os.environ, **{key: "" for key in [*KEYS_OFF, "TIGER_DATABASE_URL", "CONNECTION_STRING"]},
           "OVERWATCH_MODE": "LIVE", "OVERWATCH_DB": str(work / "live.db"), "OVERWATCH_DECISIONS_PRIMARY": "rules"}
    results = {}
    with (out / "backend-live.log").open("w") as log:
        proc = subprocess.Popen([str(BACKEND / ".venv/bin/python"), "-m", "uvicorn", "overwatch.main:app",
                                 "--host", "127.0.0.1", "--port", "8023"], cwd=BACKEND, env=env, stdout=log, stderr=log)
        try:
            base = "http://127.0.0.1:8023"; wait_health(base)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=CHROME, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base+"/dashboard")
                page.get_by_role("button", name="Start live session", exact=True).click()
                for title in ["Drone controls", "Incident map", "Review queue", "Recommendations", "Alerts", "Timeline", "Chat with Overwatch"]:
                    page.get_by_text(re.compile(re.escape(title), re.I), exact=False).first.wait_for(timeout=15_000)
                    results[title] = True
                assert get(base, "/api/autonomy/readiness")["autonomy"]["mode"] == "MANUAL"
                assert not get(base, "/api/autonomy/readiness")["autonomy"]["real_flight_allowed"]
                assert not get(base, "/api/autonomy/readiness")["autonomy"]["commands_enabled"]
                results["startup_disarmed"] = True
                page.screenshot(path=str(out / "live-dashboard.png"), full_page=True)
                for path in ["/responder", "/3d", "/drive", "/ops", "/follow", "/demo", "/mission", "/"]:
                    assert page.goto(base+path).ok
                    page.wait_for_function("document.querySelector('#root')?.childElementCount > 0", timeout=15_000)
                results["routes_rendered"] = True
                results["no_browser_errors"] = not errors
                assert not errors, errors
                browser.close()
            results["pass"] = True
            (out / "summary.json").write_text(json.dumps(results, indent=2))
            print(json.dumps(results, indent=2)); print(f"report: {out}")
            return 0
        finally:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
