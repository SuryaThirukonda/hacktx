"""Voice-command scenarios: an operator's spoken commands (as transcripts) turned into mission primitives.

VC1 (pursuit world): "Follow the person in the red shirt" resolves to a track by clothing words and, once a person
approves, enters TRACKING; "Stop" holds within one tick; "Go to the house with the carport" resolves to one building
or asks which, and the scripted operator answers. VC2 (search and rescue world): "Search sector two" starts the
sweep of sector S2. Every command is logged with its transcript, parsed primitive, confidence and approval.
"""

from __future__ import annotations

from typing import Any

from ..actors import MoveTo, Wait, Wander


def _first(cmds: list[dict], words: str) -> dict | None:
    return next((c for c in cmds if words in c.get("transcript", "").lower()), None)


def _voice_eval(res, sc) -> dict[str, Any]:
    from ...evaluation.gt_eval import _iou

    cmds = list(getattr(res, "voice", []) or [])
    out: dict[str, Any] = {"commands": [{k: c.get(k) for k in ("transcript", "intent", "confidence", "engine", "status",
                                                              "reply", "question")}
                                        | {"primitive": (c.get("primitive") or {}).get("primitive"),
                                           "approval": (c.get("primitive") or {}).get("approval_status"),
                                           "execution": (c.get("primitive") or {}).get("execution_status")}
                                        for c in cmds]}
    out["all_logged"] = int(bool(cmds) and all(c.get("transcript") and c.get("intent") and c.get("confidence") is not None
                                               and (c.get("primitive") is None or c["primitive"].get("approval_status"))
                                               for c in cmds if c.get("status") not in ("ignored",)))
    f = _first(cmds, "follow")
    if f is not None:
        prim = f.get("primitive") or {}
        out["follow_primitive"] = prim.get("primitive")
        out["follow_approval"] = prim.get("approval_status")
        t_done = next((a["t_ms"] for a in res.operator_log if a["action"] == "approve_voice"
                       and a.get("primitive") == "FOLLOW"), None)
        ok = tracking = 0
        if t_done is not None:
            for st in res.steps:
                if st.t_ms < t_done or st.t_ms > t_done + 3000:
                    continue
                tid = (st.locks.get("subject") or {}).get("track_id")
                box = next((t["box"] for t in st.tracks if t["id"] == tid and t["box"]), None)
                g = (st.gt.get("actors") or {}).get("subject") or {}
                if box is not None and g.get("box_xyxy") and _iou(box, g["box_xyxy"]) >= 0.3:
                    ok = 1
                if st.autonomy.get("mode") == "FOLLOW" and st.autonomy.get("fsm") == "TRACKING":
                    tracking = 1
        out["follow_resolved_to_subject"] = ok
        out["follow_tracking_within_3s"] = tracking
    s = _first(cmds, "stop")
    if s is not None:
        nxt = next((st for st in res.steps if st.t_ms >= s["ts_ms"]), None)
        out["stop_hold_next_step_ms"] = round(nxt.t_ms - s["ts_ms"], 1) if nxt is not None else None
        out["stop_hold_within_tick"] = int(nxt is not None and nxt.autonomy.get("mode") == "HOLD"
                                           and nxt.t_ms - s["ts_ms"] <= 100.0 + 1e-6)
        out["stop_engine"] = s.get("engine")
    c = _first(cmds, "carport")
    if c is not None:
        prim = c.get("primitive") or {}
        asked = bool(c.get("question")) and str(c.get("question")).startswith("Which building?")
        out["carport_question"] = c.get("question")
        out["carport_resolved_or_asked"] = int(asked or (prim.get("primitive") == "GOTO"
                                                         and str(prim.get("parameters", {}).get("target_id", "")).startswith("box:")))
        answered = next((x for x in cmds if x.get("answers") == c["id"]), None)
        out["carport_answer_primitive"] = ((answered or {}).get("primitive") or {}).get("primitive")
    q = _first(cmds, "sector")
    if q is not None:
        prim = q.get("primitive") or {}
        out["search_primitive"] = prim.get("primitive")
        out["search_sector"] = (prim.get("parameters") or {}).get("sector_id")
        started = any((st.autonomy.get("dispatch") or {}).get("phase") in ("TAKEOFF", "TRANSIT", "SEARCH")
                      for st in res.steps if st.t_ms >= q["ts_ms"])
        out["search_started"] = int(prim.get("execution_status") == "done" and started)
    return out


def scenarios():
    from ..scenarios import BLUE, KHAKI, RED, DroneInit, MetricLimit, Scenario, ScriptedAction, person, responder
    from . import pursuit, sar

    out = []
    sc = Scenario(
        "VC1", "Voice: follow by clothing, stop, and a building to choose",
        "Spoken commands as transcripts. 'Follow the person in the red shirt' resolves by clothing words to one "
        "track and enters TRACKING once a person approves; 'Stop' holds within one tick without asking any model; "
        "'Go to the house with the carport' resolves to one building, or asks which when the camera shows two.",
        duration_s=40.0, drone=DroneInit(-25.0, -16.0, 18.0, 0.0, -55.0),
        actors=[responder((-30.0, -8.0), [Wait(999)], heading=90.0),
                person("subject", "subject", (-34.0, 6.0), RED, [Wait(2.0), MoveTo(10.0, 6.0, 1.1), Wait(99)], heading=90),
                person("ped-1", "bystander", (-12.0, -6.0), BLUE, [Wander((-20.0, -4.0, -7.0, -5.0), 99.0, 0.8)],
                       pants=KHAKI)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"),
                 ScriptedAction(3.0, "voice", "Follow the person in the red shirt"),
                 ScriptedAction(5.0, "approve_voice"),
                 ScriptedAction(16.0, "voice", "Stop"),
                 ScriptedAction(19.0, "voice", "Go to the house with the carport"),
                 ScriptedAction(22.0, "answer_voice", 0),
                 ScriptedAction(34.0, "voice", "Stop")],
        limits=[MetricLimit("world.follow_resolved_to_subject", min=1), MetricLimit("world.follow_tracking_within_3s", min=1),
                MetricLimit("world.stop_hold_within_tick", min=1), MetricLimit("world.carport_resolved_or_asked", min=1),
                MetricLimit("world.all_logged", min=1)],
        tags=["world:pursuit", "voice", "autonomy"])
    sc.world = pursuit.build_world
    sc.world_eval = _voice_eval
    out.append(sc)

    sc = Scenario(
        "VC2", "Voice: search sector two",
        "'Search sector two' grounds to sector S2 of the search-and-rescue map and starts its sweep (no approval in "
        "the simulator; a real aircraft would wait for a person).",
        duration_s=25.0, drone=DroneInit(-90.0, -150.0, 30.0, 180.0, -80.0),
        actors=[responder((-100.0, -92.0), [Wait(999)], heading=60.0)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder"), ScriptedAction(3.0, "voice", "Search sector two")],
        limits=[MetricLimit("world.search_started", min=1), MetricLimit("world.all_logged", min=1)],
        tags=["world:sar", "voice", "search"])
    sc.world = sar.build_world
    sc.world_eval = _voice_eval
    out.append(sc)
    return out
