#!/usr/bin/env python
"""End-to-end demo test in headless Chrome, run twice on fresh backends, compared for identical outcomes.

Each run starts its own backend (SIMULATED, its own database, no API keys so the decisions use the labelled mock and
nothing leaves the machine), serves the built dashboard, and walks the demo as an operator and a responder would:

  1. Start the demo incident; Search flags a candidate.
  2. Confirm it; the rules propose "Notify responder"; the operator approves it.
  3. The responder (phone-sized browser) receives the brief and acknowledges it.
  4. Switch to Structure, set side A, and see the openings organised by side.
  5. Open the 3D world and replay the timeline.

Outcomes (not timings) are compared between the two runs. Writes evaluation/e2e/<timestamp>/ with screenshots.

    python scripts/e2e_demo.py            # needs dashboard/dist (npm run build) and Chrome
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CHROME = os.environ.get("E2E_CHROME", "/usr/bin/google-chrome")
KEYS_OFF = ["OPENAI_API_KEY", "TYPESAFE_API_KEY", "STRANDS_URL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OVERWATCH_TOKEN",
            *(f"ELEVENLABS_API_KEY{n}" for n in ("", "2", "3", "4"))]


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


def wait_health(base: str, timeout: float = 60.0) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if get(base, "/api/health"):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("backend did not start")


def run_once(port: int, out: Path, tag: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="overwatch-e2e-"))
    env = {**os.environ, "OVERWATCH_DB": str(work / "e2e.db"), **{k: "" for k in KEYS_OFF}}
    log = (out / f"backend-{tag}.log").open("w")
    proc = subprocess.Popen([str(BACKEND / ".venv/bin/python"), "-m", "uvicorn", "overwatch.main:app", "--host",
                             "127.0.0.1", "--port", str(port)], cwd=BACKEND, env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    steps: dict[str, object] = {}
    try:
        wait_health(base)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, headless=True)
            op = b.new_page(viewport={"width": 1440, "height": 900})
            op.goto(base + "/dashboard")
            op.get_by_role("button", name=re.compile("Start demo incident")).click()
            confirm = op.locator('button[title="Confirm"]').first
            confirm.wait_for(timeout=90_000)
            steps["1_candidate_flagged"] = True
            confirm.click()
            approve = op.locator('button[title="Approve"]').first
            approve.wait_for(timeout=30_000)
            steps["2_notify_recommended"] = "Notify responder" in op.locator("section", has_text="RECOMMENDATIONS").first.inner_text()

            rctx = b.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
            rp = rctx.new_page()
            rp.goto(base + "/responder")
            try:
                rp.get_by_text("Enable voice").wait_for(timeout=8_000)
            except Exception:  # noqa: BLE001 - not joined automatically: pick the incident
                rp.locator("li button").first.click()
            approve.click()
            iid = get(base, "/api/incidents")["items"][0]["id"]
            brief_id = None
            for _ in range(60):
                al = [a for a in get(base, f"/api/incidents/{iid}/alerts")["items"] if a["source_observation_ids"]]
                if al:
                    brief_id = al[0]["id"]
                    break
                time.sleep(0.5)
            steps["2_brief_sent_after_approval"] = brief_id is not None
            acked = False
            for _ in range(12):  # acknowledge in order until the brief is acknowledged
                btn = rp.get_by_role("button", name=re.compile("ACKNOWLEDGE"))
                try:
                    btn.wait_for(timeout=10_000)
                    btn.click()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.6)
                a = next(x for x in get(base, f"/api/incidents/{iid}/alerts")["items"] if x["id"] == brief_id)
                if a["acknowledged_at"]:
                    acked = a["acknowledged_by"] == "responder"
                    break
            steps["3_responder_acknowledged"] = acked
            rp.screenshot(path=str(out / f"responder-{tag}.png"))

            op.get_by_role("radio", name="Structure").click()
            sel = op.locator('select[aria-label="Side A compass face"]').first
            sel.wait_for(timeout=20_000)
            sel.select_option("S")
            op.wait_for_timeout(1500)
            sv = get(base, f"/api/incidents/{iid}/structure")
            texts = [x["text"] for bld in sv["buildings"] for s in "ABCD" for x in bld["sides"][s]]
            steps["4_side_a_set"] = sv["buildings"][0]["side_a"] == "S"
            steps["4_openings_by_side"] = any(t.startswith("possible doorway, Side A") for t in texts)

            op.get_by_role("tab", name=re.compile("3D world")).click()
            op.locator("canvas").first.wait_for(timeout=15_000)
            steps["5_3d_view_rendered"] = op.locator("canvas").count() > 0
            op.get_by_role("tab", name="Drone view").click()
            op.get_by_role("tab", name="timeline").click()
            op.get_by_role("button", name=re.compile("Replay timeline")).click()
            op.wait_for_timeout(2500)
            steps["5_replay_started"] = op.get_by_role("button", name=re.compile("Pause replay")).count() > 0
            op.screenshot(path=str(out / f"operator-{tag}.png"))

            kinds = {e["kind"] for e in get(base, f"/api/incidents/{iid}/events?limit=10000")["items"]}
            steps["log_has_core_events"] = {"incident_created", "observation", "review", "decision_proposal",
                                            "action_approved", "alert", "alert_acknowledged", "mission_changed",
                                            "side_a_designated"} <= kinds
            b.close()
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        log.close()
    return steps


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = REPO / "evaluation" / "e2e" / stamp
    out.mkdir(parents=True, exist_ok=True)
    if not (REPO / "dashboard" / "dist" / "index.html").exists():
        print("build the dashboard first: cd dashboard && npm run build")
        return 2
    results = {}
    for i, port in enumerate((8011, 8012), 1):
        t0 = time.monotonic()
        try:
            results[f"run{i}"] = run_once(port, out, f"run{i}")
        except Exception as e:  # noqa: BLE001
            results[f"run{i}"] = {"error": f"{type(e).__name__}: {e}"}
        results[f"run{i}_wall_s"] = round(time.monotonic() - t0, 1)
    r1, r2 = results["run1"], results["run2"]
    ok1 = "error" not in r1 and all(v is True for v in r1.values())
    ok2 = "error" not in r2 and all(v is True for v in r2.values())
    results["identical_outcomes"] = r1 == r2
    results["pass"] = ok1 and ok2 and r1 == r2
    (out / "summary.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"report: {out}")
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
