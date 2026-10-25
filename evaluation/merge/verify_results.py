"""Check saved independent headless evidence; absent evidence is a failure."""
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    current = root.parent / "current"
    platform = list((root / "platform").glob("*.json"))
    assert platform, "no platform evidence"
    for file in platform:
        report = json.loads(file.read_text())
        assert report["expected_pass"], file
    for sid in ("FR3", "FF3", "SR5"):
        report = json.loads((current / "rescue" / f"{sid}.json").read_text())
        assert report["passed"] and all(report["checks"].values()), (sid, report["checks"])
        mission = report["rescue"]
        error = report["evidence"]["beacon_error_m"]
        assert error is not None and error <= 3, (sid, report)
        assert mission["unsafe_steps"] == 0 and mission["crew_distance_m"] > 1, (sid, mission)
        assert mission["arrived_s"] is not None, (sid, mission)
        if sid.startswith("FR"):
            assert mission["max_water_depth_m"] <= .15, (sid, mission)
    print("Saved platform and rescue handoffs meet the recorded criteria.")


if __name__ == "__main__":
    main()
