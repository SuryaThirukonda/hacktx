"""Phase 16: near-IR enhancement is labelled image processing; the near-IR source never reports temperature."""

import re

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.sources.near_ir import PROCESSING_LABEL, Enhancement, NearIRVideoSource, enhance, side_by_side


def dim_frame(seed=0):
    rng = np.random.default_rng(seed)
    g = np.full((240, 320), 18, np.uint8)
    cv2.rectangle(g, (140, 80), (170, 170), 46, -1)  # a dim figure
    g = np.clip(g + rng.normal(0, 6, g.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def test_enhancement_is_off_by_default_and_labelled_when_on():
    f = dim_frame()
    raw, steps = enhance(f, Enhancement())
    assert steps == [] and raw.ndim == 2
    proc, steps = enhance(f, Enhancement(enabled=True))
    assert steps and "CLAHE (clip 2.5)" in steps
    fig, bg = proc[90:160, 145:165].mean(), proc[10:60, 10:60].mean()
    raw_fig, raw_bg = raw[90:160, 145:165].mean(), raw[10:60, 10:60].mean()
    assert fig - bg > 2 * (raw_fig - raw_bg)  # the figure stands out more after processing
    assert "not thermal" in PROCESSING_LABEL and "no temperatures" in PROCESSING_LABEL
    sbs = side_by_side(f, Enhancement())
    assert sbs.shape[1] == 1280  # raw and processed side by side


def test_near_ir_source_frames_are_mono_and_carry_their_processing():
    class FakeRt:
        pass
    src = NearIRVideoSource(FakeRt(), "clip.mp4")
    out, meta = src.transform(dim_frame())
    assert out.ndim == 3 and np.array_equal(out[..., 0], out[..., 1])
    assert meta["sensor"] == "near_ir" and meta["image_processing"] == [] and meta["label"] == "near-IR (raw)"
    src.enhancement = Enhancement(enabled=True)
    _, meta = src.transform(dim_frame())
    assert meta["image_processing"] and meta["label"] == PROCESSING_LABEL
    d = src.describe()
    text = repr(d).lower()
    assert "not thermal" in text and not re.search(r"\d\s*(°|deg)\s*c\b|kelvin|celsius", text)


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_preview_endpoint_labels_processing(client):
    ok, buf = cv2.imencode(".png", dim_frame())
    r = client.post("/api/near-ir/preview", content=buf.tobytes())
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "CLAHE" in r.headers["x-image-processing"]
    assert client.post("/api/near-ir/preview", content=b"not an image").status_code == 422
    st = client.get("/api/sources/near-ir").json()
    assert st["active"] is False and "not thermal" in st["note"]
