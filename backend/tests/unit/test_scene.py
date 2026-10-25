"""Phase 12: scene file validation, transform chains, annotation transforms, overlay refused without alignment."""

import numpy as np
import pytest

from overwatch.incident.models import Incident, Observation, Position, SceneAnchor, Transform, utcnow
from overwatch.incident.scene import NoAlignment, inspect_file, overlay, parse_transform_json, resolve
from overwatch.incident.store import IncidentStore

PLY_POINTS = b"ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n1 1 1\n"
PLY_MESH = (b"ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
            b"element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n")
PLY_SPLAT = (b"ply\nformat binary_little_endian 1.0\nelement vertex 10\nproperty float x\nproperty float y\n"
             b"property float z\nproperty float f_dc_0\nproperty float opacity\nproperty float scale_0\n"
             b"property float rot_0\nend_header\n")


def test_inspect_file_kinds_and_rejections():
    assert inspect_file(PLY_POINTS, "ply") == ("point_cloud", 2, None)
    assert inspect_file(PLY_MESH, "ply") == ("mesh", 3, 1)
    assert inspect_file(PLY_SPLAT, "ply")[0] == "splat"
    assert inspect_file(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "obj") == ("mesh", 3, 1)
    for data, fmt in ((b"hello", "ply"), (b"ply\nno header end", "ply"), (b"# empty", "obj"), (b"x", "splat")):
        with pytest.raises(ValueError):
            inspect_file(data, fmt)
    with pytest.raises(ValueError):
        inspect_file(PLY_POINTS, "gltf")


@pytest.fixture()
def store():
    st = IncidentStore(":memory:")
    inc = st.create_incident(Incident(title="t"))
    return st, inc


def test_resolve_direct_inverse_chain_and_expiry(store):
    st, inc = store
    enu = inc.enu_frame
    st.add_transform(inc.id, Transform.from_rt("scene:a", enu, yaw_deg=90, t=(10, 0, 0), method="manual_3pt"))
    st.add_transform(inc.id, Transform.from_rt("arkit:b", "scene:a", t=(0, 5, 0), method="aruco"))
    m, path = resolve(st, inc.id, "arkit:b", enu)  # two hops
    p = Position(frame="arkit:b", x=1, y=0, z=0)
    v = m @ np.array([p.x, p.y, p.z, 1])
    assert np.allclose(v[:3], [10 - 5, 1, 0])  # (1,0)+(0,5) = (1,5) -> rotate 90 -> (-5,1) -> +10 -> (5,1)
    assert len(path) == 2
    mi, _ = resolve(st, inc.id, enu, "arkit:b")  # inverse direction
    assert np.allclose(mi @ v, [1, 0, 0, 1])
    with pytest.raises(NoAlignment):
        resolve(st, inc.id, "scene:unrelated", enu)
    st.add_transform(inc.id, Transform.from_rt("scene:old", enu, method="manual", valid_until=utcnow()))
    with pytest.raises(NoAlignment):
        resolve(st, inc.id, "scene:old", enu)  # expired transforms are not used


def test_overlay_refuses_unaligned_frames_but_keeps_own_annotations(store):
    st, inc = store
    enu = inc.enu_frame
    st.add_anchor(SceneAnchor(incident_id=inc.id, name="Kitchen", type="room", coordinate_frame="arkit:scan1",
                              position=Position(frame="arkit:scan1", x=2, y=3), manually_verified=True))
    now = utcnow()
    st.add_observation(Observation(incident_id=inc.id, sensor_source_id="drone", observed_at=now, category="person",
                                   label="possible person", world_position=Position(frame=enu, x=4, y=1),
                                   horizontal_uncertainty_m=2.0, position_estimation_method="ground_plane",
                                   provenance="simulated"))
    out = overlay(st, inc.id, "arkit:scan1", enu)
    assert [i["label"] for i in out["items"]] == ["Kitchen"]  # its own annotation always shows
    assert out["unaligned_frames"] == [enu] and "no valid alignment" in out["note"]
    st.add_transform(inc.id, Transform.from_rt("arkit:scan1", enu, t=(1, 1, 0), method="manual_3pt", residual_m=0.2))
    out = overlay(st, inc.id, "arkit:scan1", enu)
    obs = next(i for i in out["items"] if i["kind"] == "observation")
    assert (obs["position"]["x"], obs["position"]["y"], obs["position"]["frame"]) == (3.0, 0.0, "arkit:scan1")
    assert obs["frame_of_record"] == enu and obs["via"] and out["note"] is None


def test_transform_json_must_match_asset_frame(store):
    st, inc = store
    tf = parse_transform_json('{"matrix": [[1,0,0,2],[0,1,0,0],[0,0,1,0],[0,0,0,1]], "residual_m": 0.05}',
                              "scene:X", inc.enu_frame)
    assert tf.source_frame == "scene:X" and tf.target_frame == inc.enu_frame and tf.residual_m == 0.05
    with pytest.raises(ValueError):
        parse_transform_json('{"source_frame": "scene:Y", "matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}',
                             "scene:X", inc.enu_frame)
    with pytest.raises(ValueError):
        parse_transform_json('{"matrix": [[1,0],[0,1]]}', "scene:X", inc.enu_frame)
