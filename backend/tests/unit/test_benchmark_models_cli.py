"""The benchmark can write its reports outside the checkout on Windows."""

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def test_benchmark_external_output_is_utf8(tmp_path):
    source = tmp_path / "images"
    source.mkdir()
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.rectangle(image, (20, 10), (30, 45), (0, 0, 255), -1)
    assert cv2.imwrite(str(source / "frame.png"), image)
    output = tmp_path / "reports"
    script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_models.py"
    subprocess.run([sys.executable, str(script), "--source", str(source), "--models", "sim_blob",
                    "--frames", "1", "--output-dir", str(output)], check=True, capture_output=True, text=True)
    assert (output / "results.json").exists()
    assert (output / "per_frame.csv").exists()
    assert "conf ≥" in (output / "summary.md").read_text(encoding="utf-8")
