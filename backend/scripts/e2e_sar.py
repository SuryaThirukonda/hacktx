"""Browser acceptance of the existing mission UI plus assistance review and responder ACK.
No external accounts or physical control. Writes screenshots and machine-readable results.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from e2e_demo import BACKEND, CHROME, KEYS_OFF, REPO, get, wait_health
from playwright.sync_api import expect, sync_playwright


def main() -> int:
    out = Path(os.environ.get("OVERWATCH_E2E_OUTPUT", str(REPO / "evaluation/current/browser-sar")))
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="overwatch-sar-browser-"))
    env = {**os.environ, **{k: "" for k in [*KEYS_OFF, "TIGER_DATABASE_URL", "CONNECTION_STRING"]},
           "PYTHONPATH": str(BACKEND), "OVERWATCH_DB": str(work / "sar.db")}
    results = {}
    with (out / "backend.log").open("w") as log:
        proc = subprocess.Popen([str(BACKEND / ".venv/bin/python"), str(BACKEND / "scripts/run_sar_demo.py"),
                                 "--port", "8024"], cwd=REPO, env=env, stdout=log, stderr=log)
        try:
            base = "http://127.0.0.1:8024"
            wait_health(base)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=CHROME, headless=True)
                page = browser.new_page(viewport={"width": 1600, "height": 1100})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/command")
                card = page.get_by_test_id("assistance-card").first
                expect(card).to_be_visible(timeout=60_000)
                expect(card).to_contain_text("simulated flood water")
                results["sar_explanation_visible"] = True
                page.screenshot(path=str(out / "01-review.png"), full_page=True)
                # New clothing input still uses the existing lock and cannot start aircraft movement.
                page.get_by_role("textbox", name="Continuous clothing search").fill("Find the person wearing a red shirt.")
                page.get_by_role("button", name="Search", exact=True).click()
                expect(page.get_by_test_id("target-state")).to_contain_text("VISIBLE", timeout=30_000)
                results["continuous_search_and_persistent_subject"] = True
                card.get_by_role("button", name="Confirm & send guidance").click()
                expect(card.get_by_test_id("assistance-guidance")).to_contain_text("approach point", timeout=30_000)
                expect(card).to_contain_text("Dispatch:", timeout=15_000)
                iid = get(base, "/api/incidents")["current"]
                assistance_items = get(base, f"/api/incidents/{iid}/sar")["items"]
                assistance_alert_id = next(i["dispatch"]["id"] for i in assistance_items if i["dispatch"])
                page.screenshot(path=str(out / "02-guidance.png"), full_page=True)
                results["confirmed_guidance_and_map_route"] = True
                responder = browser.new_page(viewport={"width": 390, "height": 844})
                responder.goto(base + "/responder")
                # The responder joins a sole/current incident automatically; tolerate its incident picker.
                ack = responder.get_by_role("button", name="ACKNOWLEDGE", exact=False)
                try:
                    expect(ack.first).to_be_visible(timeout=8_000)
                except AssertionError:
                    responder.locator("li button").first.click()
                    expect(ack.first).to_be_visible(timeout=15_000)
                expect(responder.get_by_text("Simulation: assistance location confirmed", exact=False).first).to_be_visible()
                responder.screenshot(path=str(out / "03-responder.png"), full_page=True)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    alerts = get(base, f"/api/incidents/{iid}/alerts")["items"]
                    if any(a["id"] == assistance_alert_id and a.get("acknowledged_at") for a in alerts):
                        break
                    # The existing responder presents its newest unacknowledged alert.
                    # Tracking callouts can arrive after the SAR alert; acknowledge
                    # those too until the intended assistance alert is reached.
                    if ack.first.is_visible():
                        ack.first.click()
                    page.wait_for_timeout(150)
                else:
                    raise AssertionError("responder ACK was not persisted")
                expect(card).to_contain_text("acknowledged", timeout=10_000)
                results["responder_delivery_ack_and_dashboard_update"] = True
                page.screenshot(path=str(out / "04-acknowledged.png"), full_page=True)
                ready = get(base, "/api/autonomy/readiness")["autonomy"]
                assert ready["mode"] == "MANUAL" and not ready["real_flight_allowed"]
                results["no_physical_permission_or_follow_start"] = True
                assert not errors, errors
                results["no_browser_errors"] = True
                browser.close()
            results["pass"] = True
            (out / "summary.json").write_text(json.dumps(results, indent=2))
            print(json.dumps(results, indent=2))
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
