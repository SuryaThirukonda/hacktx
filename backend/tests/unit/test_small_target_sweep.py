from pathlib import Path

import pytest

from scripts.small_target_sweep import parse_config, read_annotations, score_frame


def test_visdrone_person_mapping_and_ignore_regions(tmp_path: Path):
    annotation = tmp_path / "sample.txt"
    annotation.write_text(
        "10,10,10,20,1,1,0,0\n"  # pedestrian
        "40,40,15,25,1,2,0,0\n"  # people
        "70,70,20,20,0,0,0,0\n"  # ignored region
        "70,70,10,10,1,1,0,0\n"  # person removed inside ignored region
        "0,70,10,10,0,11,0,0\n"  # others
        "30,0,20,20,1,4,0,0\n",  # car is not person
        encoding="utf-8",
    )
    labels = read_annotations(annotation, 100, 100)
    assert len(labels.people) == 2
    assert labels.removed_people == 1
    assert labels.categories[(1, 1)] == 2
    assert labels.categories[(1, 2)] == 1
    assert labels.ignored_coverage((72, 72, 78, 78)) == 1
    predictions = [
        ((10, 10, 20, 30), 0.9),
        ((40, 40, 55, 65), 0.8),
        ((72, 72, 78, 78), 0.7),  # ignored region, not a false positive
        ((0, 70, 10, 80), 0.6),  # "others", not a false positive
        ((30, 0, 50, 20), 0.5),  # vehicle false positive
        ((10, 10, 20, 30), 0.4),  # duplicate person false positive
    ]
    outcome = score_frame(predictions, labels, 0.25)
    assert outcome == {
        "tp": 2, "fp": 2, "fn": 0, "small_hits": 2, "small_total": 2,
        "ignored_predictions": 2, "predictions": 6,
    }
    assert score_frame(predictions, labels, 0.85)["small_hits"] == 1


def test_union_of_ignored_regions_is_used(tmp_path: Path):
    annotation = tmp_path / "sample.txt"
    annotation.write_text("0,0,5,10,0,0,0,0\n5,0,5,10,0,0,0,0\n", encoding="utf-8")
    labels = read_annotations(annotation, 20, 20)
    assert labels.ignored_coverage((0, 0, 10, 10)) == 1
    assert score_frame([((0, 0, 10, 10), 0.8)], labels, 0.25)["fp"] == 0


def test_practical_configuration_names():
    assert parse_config("n1536")["imgsz"] == 1536
    assert parse_config("s960")["checkpoint"] == "yolo26s.pt"
    tiled = parse_config("t640o30nofull")
    assert (tiled["tile_px"], tiled["overlap"], tiled["full_frame"]) == (640, 0.3, False)
    with pytest.raises(ValueError):
        parse_config("t256o90")
