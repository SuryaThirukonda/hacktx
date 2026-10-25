"""Browser check of repaired picker, coverage, beacon review, shared 3D and City handoff."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from e2e_demo import BACKEND, CHROME, KEYS_OFF, REPO, get, wait_health


def main():
    out = REPO / "evaluation/current/browser-workspace"
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="overwatch-workspace-"))
    env = {**os.environ, **{key: "" for key in KEYS_OFF}, "OVERWATCH_DB": str(work / "browser.db"),
           "OVERWATCH_MODE": "SIMULATED", "OVERWATCH_DECISIONS_PRIMARY": "rules"}
    checks = {}
    with (out / "backend.log").open("w") as log:
        proc = subprocess.Popen([str(BACKEND / ".venv/bin/python"), "-m", "uvicorn", "overwatch.main:app",
                                 "--host", "127.0.0.1", "--port", "8025"], cwd=BACKEND, env=env,
                                stdout=log, stderr=log)
        base = "http://127.0.0.1:8025"
        try:
            wait_health(base)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=CHROME, headless=True)
                page = browser.new_page(viewport={"width": 1600, "height": 1050})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/dashboard")
                expect(page.locator(".world-card")).to_have_count(6)
                page.get_by_role("button", name="Toggle color theme").click()
                theme = page.locator(".simulation-app").get_attribute("data-appearance")
                page.reload()
                expect(page.locator(".simulation-app")).to_have_attribute("data-appearance", theme)
                checks["picker_and_saved_theme"] = True
                page.locator(".world-flood").click()
                page.get_by_label("Choose a scenario").select_option("FR3")
                page.get_by_role("button", name="Launch simulation").click()
                expect(page.get_by_label("Survey map with camera coverage, estimated tracks, report history and crew pings")).to_be_visible(timeout=30_000)
                card = page.get_by_test_id("assistance-card").first
                expect(card).to_be_visible(timeout=90_000)
                expect(card).to_contain_text("simulated missing-person request")
                page.screenshot(path=str(out / "review.png"), full_page=True)
                card.get_by_role("button", name="Confirm & send guidance").click()
                expect(card).to_contain_text("Dispatch:", timeout=20_000)
                iid = get(base, "/api/incidents")["current"]
                item = next(i for i in get(base, f"/api/incidents/{iid}/sar")["items"] if i["dispatch"])
                alert_id = item["dispatch"]["id"]
                phone = browser.new_page(viewport={"width": 390, "height": 844})
                phone.goto(base + "/responder")
                ack = phone.get_by_role("button", name="ACKNOWLEDGE", exact=False).first
                expect(ack).to_be_visible(timeout=20_000)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    alerts = get(base, f"/api/incidents/{iid}/alerts")["items"]
                    if any(a["id"] == alert_id and a["acknowledged_at"] for a in alerts):
                        break
                    if ack.is_visible():
                        ack.click()
                    page.wait_for_timeout(150)
                else:
                    raise AssertionError("specific beacon assistance alert not acknowledged")
                checks["beacon_human_review_route_delivery_ack"] = True
                page.get_by_role("tab", name="3D world").click()
                expect(page.locator(".mission-visual canvas")).to_be_visible(timeout=30_000)
                page.wait_for_timeout(1000)
                page.screenshot(path=str(out / "shared-3d.png"), full_page=True)
                checks["shared_3d_and_survey"] = True
                page.get_by_role("tab", name="Callouts").click()
                expect(page.get_by_text("Responder callouts", exact=True)).to_be_visible()
                checks["callout_history"] = True
                page.get_by_role("button", name="Situations").click()
                page.locator(".world-city").click()
                page.get_by_label("Choose a scenario").select_option("T12")
                page.get_by_role("button", name="Launch simulation").click()
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    city = get(base, "/api/test12/state")
                    if city.get("error"):
                        raise AssertionError(city)
                    if city.get("frame3d") and city.get("survey"):
                        break
                    page.wait_for_timeout(500)
                else:
                    raise AssertionError("City did not publish shared 3D/coverage")
                expect(page.locator(".mission-visual canvas")).to_be_visible(timeout=30_000)
                page.screenshot(path=str(out / "city.png"), full_page=True)
                checks["city_shared_3d_coverage_and_process"] = True
                page.get_by_role("button", name="Situations").click()
                page.locator(".world-training").click()
                page.get_by_role("button", name="Launch simulation").click()
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if get(base, "/api/session")["scenario"] == "DEMO-1":
                        break
                    page.wait_for_timeout(200)
                else:
                    raise AssertionError("main simulator did not resume after City stop acknowledgment")
                checks["city_to_main_ownership_handoff"] = True
                assert not errors, errors
                checks["no_page_errors"] = True
                browser.close()
            checks["passed"] = True
            (out / "summary.json").write_text(json.dumps(checks, indent=2))
            print(json.dumps(checks, indent=2))
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
