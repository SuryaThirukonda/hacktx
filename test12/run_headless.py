"""Run test12 headless over many seeds and score each run against the simulator's ground truth.

    backend/.venv/bin/python test12/run_headless.py --seeds 0-19 --workers 4 --time-limit 240

Nothing ends a run but the time limit (the chase goes on after the police get there), so every seed runs that long.
Writes evaluation/test12/<stamp>/summary.md and runs.json: how long the search took, how much of the time after the
match the drone was following, how close it stayed, how often the suspect was lost and how soon the 360-degree scan
found them again, how far each report and each callout relative to the police car was from the truth, and how close
the police got.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test12  # noqa: E402,F401  (puts backend/ on the path)


def run_seed(seed: int, threads: int = 4, verbose: bool = False, time_limit: float = 240.0, spawn=None) -> dict:
    import cv2

    from test12.sim import Run
    from test12.vision import PersonDetector

    cv2.setNumThreads(1)  # several runs share the CPU: keep OpenCV from starting a thread per core in each
    det = PersonDetector(threads)
    t0 = time.perf_counter()
    run = Run(seed, det.detect, spawn=spawn, time_limit=time_limit)
    while not run.done:
        run.step()
    s = run.summary({"yolo_calls": det.calls, "yolo_ms_mean": round(det.total_ms / max(det.calls, 1), 1)})
    s["wall_s"] = round(time.perf_counter() - t0, 1)
    s["events"] = run.events
    s["suspect_log"] = run.suspect.log
    if verbose:
        for e in run.events:
            print(f"  {e['t']:6.1f}  {e['kind']:8s} {e['text']}")
    return s


def _seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--threads", type=int, default=4, help="torch threads per worker")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--time-limit", type=float, default=240.0, help="seconds of simulation per seed")
    ap.add_argument("--spawn", default=None, help="x,y in world metres: where the suspect starts (default: random)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    spawn = [float(v) for v in a.spawn.split(",")] if a.spawn else None
    # several runs share the CPU: cap every thread pool in each worker before numpy and torch load, and do not let idle
    # OpenMP threads spin (four processes each spinning a pool the size of the machine run 10 times slower)
    for k, v in (("OMP_NUM_THREADS", a.threads), ("MKL_NUM_THREADS", a.threads), ("OPENBLAS_NUM_THREADS", 1),
                 ("OMP_WAIT_POLICY", "PASSIVE"), ("KMP_BLOCKTIME", 0)):
        os.environ.setdefault(k, str(v))
    seeds = _seeds(a.seeds)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) if a.out else Path(test12.REPO_ROOT) / "evaluation" / "test12" / stamp
    out.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    log = (out / "runs.jsonl").open("w")

    def done(r: dict) -> None:  # each run is written as it finishes, so a stopped batch keeps what it has
        runs.append(r)
        log.write(json.dumps(r) + "\n")
        log.flush()
        print(f"seed {r['seed']}: matched at {r['matched_s']} s, following {r['following_pct']} %, lost "
              f"{r['losses']} / found {r['reacquired']}, police on scene at {r['police_on_scene_s']} s "
              f"({r['wall_s']} s wall)", flush=True)

    if a.workers <= 1:
        for s in seeds:
            done(run_seed(s, a.threads, a.verbose, a.time_limit, spawn))
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            for f in as_completed([ex.submit(run_seed, s, a.threads, False, a.time_limit, spawn) for s in seeds]):
                done(f.result())
    log.close()
    runs.sort(key=lambda r: r["seed"])
    (out / "runs.json").write_text(json.dumps(runs, indent=1))
    matched = [r for r in runs if r["matched_s"] is not None]
    scene = [r for r in runs if r["police_on_scene_s"] is not None]
    lines = [f"# test12 headless runs ({stamp})", "",
             f"{len(runs)} seeds, {a.time_limit:.0f} s each. The suspect was found (matched) in **{len(matched)}**; "
             f"wrong matches: {sum(r['wrong_matches'] for r in runs)}; police on scene in {len(scene)}.", "",
             "| seed | matched at s | following % | drone-suspect m (median / p90) | lost / found again | "
             "out of sight s (median / max) | lost at end | report err m (mean / max) | callout err m (mean / max) | "
             "police on scene s | police-suspect m (median) | hides | search revisit % |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        lines.append(f"| {r['seed']} | {r['matched_s']} | {r['following_pct']} | {r['follow_dist_median_m']} / "
                     f"{r['follow_dist_p90_m']} | {r['losses']} / {r['reacquired']} | "
                     f"{r['refind_median_s']} / {r['refind_max_s']} | {'yes' if r['lost_at_end'] else 'no'} | "
                     f"{r['report_error_mean_m']} / {r['report_error_max_m']} | {r['callout_error_mean_m']} / "
                     f"{r['callout_error_max_m']} | {r['police_on_scene_s']} | {r['police_gap_median_m']} | "
                     f"{r['suspect_hides']} | {r['search_revisit_pct']} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
