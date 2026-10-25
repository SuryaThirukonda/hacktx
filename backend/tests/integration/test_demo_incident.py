import hashlib
import json

from overwatch.config import REPO_ROOT, load_config
from overwatch.evaluation.scenario_runner import HeadlessRunner
from overwatch.sim.scenarios import get_scenario
from overwatch.sim.worlds.spec import SceneSpec
from overwatch.sim.worlds.training import TRAINING_AREA


def _state_hash(r: HeadlessRunner, res) -> str:
    tracks = sorted((t.track_id, t.state, t.class_name, [round(float(v), 1) for v in t.box])
                    for t in r.pipeline.raw_tracks)
    blob = json.dumps({"events": [(round(e["ts_ms"]), e["kind"]) for e in res.events], "tracks": tracks},
                      default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def test_demo_scenario_is_registered_with_its_scene():
    sc = get_scenario("DEMO-1")
    assert sc.scene is TRAINING_AREA
    kinds = {b.kind for b in sc.extra_boxes}
    assert {"building", "supply"} <= kinds
    bldg = [b for b in sc.extra_boxes if b.kind == "building"][0]
    assert len(bldg.openings) == 4


def test_sides_follow_incident_command_convention():
    b = TRAINING_AREA.buildings[0]
    assert [b.compass_of(s) for s in "ABCD"] == ["S", "W", "N", "E"]  # A front, then clockwise from above
    gt = TRAINING_AREA.ground_truth()
    by_id = {o["id"]: o for o in gt["openings"]}
    assert by_id["door-a1"]["side"] == "A" and by_id["door-a1"]["xy"][1] == 13.0  # on the south face
    assert by_id["garage-d1"]["compass"] == "E" and by_id["garage-d1"]["blocked"] is True
    # a point just outside each face maps back to that side
    for o in gt["openings"]:
        (nx, ny) = {"N": (0, 1), "E": (1, 0), "S": (0, -1), "W": (-1, 0)}[o["compass"]]
        assert b.side_from_point(o["xy"][0] + nx, o["xy"][1] + ny) == o["side"]


def test_replay_is_deterministic():
    a = HeadlessRunner(load_config(), "DEMO-1", seed=0)
    ra = a.run(8.0)
    b = HeadlessRunner(load_config(), "DEMO-1", seed=0)
    rb = b.run(8.0)
    assert _state_hash(a, ra) == _state_hash(b, rb)


def test_fixture_matches_the_scene_spec():
    path = REPO_ROOT / "fixtures" / "demo_incident" / "scene.json"
    assert SceneSpec.model_validate_json(path.read_text()) == TRAINING_AREA
    exp = json.loads((REPO_ROOT / "fixtures" / "demo_incident" / "expected.json").read_text())
    assert exp["provenance"] == "simulated" and exp["blocked_entrances"] == ["garage-d1"]
