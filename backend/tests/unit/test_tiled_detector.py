import numpy as np

from overwatch.vision.detectors import Detection, TiledDetector


class ScaleLimitedDetector:
    """Finds white squares, but only those at least 3 % of the input height (like a model run at a fixed imgsz)."""

    model_id = "fake"
    valid_on = "any"

    def detect(self, image):
        H = image.shape[0]
        mask = (image[..., 0] > 200).astype(np.uint8)
        import cv2

        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        out = []
        for i in range(1, n):
            x, y, w, h, _ = stats[i]
            if h >= 0.03 * H:
                out.append(Detection((float(x), float(y), float(x + w), float(y + h)), 0.9))
        return out


def _canvas(squares):
    img = np.zeros((720, 1280, 3), np.uint8)
    for x, y, s in squares:
        img[y:y + s, x:x + s] = 255
    return img


def test_tiles_find_small_targets_and_map_back():
    img = _canvas([(900, 500, 20)])  # 20 px: 2.8 % of 720 (missed), 3.1 % of a 640 tile (found)
    assert ScaleLimitedDetector().detect(img) == []
    dets = TiledDetector(ScaleLimitedDetector(), tile_px=640, overlap=0.2).detect(img)
    assert len(dets) == 1
    assert dets[0].box == (900.0, 500.0, 920.0, 520.0)


def test_overlapping_tiles_do_not_duplicate_and_seam_cuts_lose():
    td = TiledDetector(ScaleLimitedDetector(), tile_px=640, overlap=0.2)
    xs = sorted({t[0] for t in td.tiles(1280, 720)})
    seam = xs[1] + 640  # right edge of the second tile column
    img = _canvas([(seam - 10, 300, 22), (100, 100, 21)])  # first square straddles a tile seam
    dets = td.detect(img)
    boxes = sorted(d.box for d in dets)
    assert boxes == [(100.0, 100.0, 121.0, 121.0), (float(seam - 10), 300.0, float(seam + 12), 322.0)]


def test_tiles_cover_the_frame_with_overlap():
    td = TiledDetector(ScaleLimitedDetector(), tile_px=640, overlap=0.2)
    tiles = td.tiles(1920, 1080)
    cover = np.zeros((1080, 1920), bool)
    for x0, y0, x1, y1 in tiles:
        assert x1 - x0 == 640 and y1 - y0 == 640
        cover[y0:y1, x0:x1] = True
    assert cover.all()
    xs = sorted({t[0] for t in tiles})
    assert all(b - a <= 512 for a, b in zip(xs, xs[1:]))  # >= 20 % overlap between neighbours
