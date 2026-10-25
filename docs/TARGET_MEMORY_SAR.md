# Persistent target memory and simulated SAR

The current combined-repository audit and measured test scope are in [6am.md](../6am.md). The setup and interactive walkthroughs are in [README.md](../README.md). This document describes the memory/SAR implementation now integrated with the newer pursuit and simulation workspace.

## Implementation boundary

`tracking/memory.py:SubjectMemory` is a read model over the existing subject lock and calibrated `ObjectMemory`; it does not associate detections or command aircraft. `incident/targets.py:TargetCoordinator` scopes it to the selected incident/session, updates it at publication cadence and throttles durable snapshots. Existing observation/event/replay records hold its history.

Measured trail points, last verified time and position, current predictions, appearance, uncertainty and continuity state remain distinct. Loss does not fabricate a fresh point. Extrapolation uses at most 2.5 seconds of last measured velocity while uncertainty continues to grow. A tracker-ID change can preserve a logical subject, but a session restart deliberately does not infer physical identity. Release/new designation/incident change archives the old subject.

The newer pursuit belief, tracker, lock and object-memory behavior is retained. Automatic continuity requires supported synthetic measurements, distinct frames, geometry, appearance and motion consistency. Ambiguity and rejected tracks prevent silent selection. Physical confirmation and arming remain separate.

## Continuous search

`incident/person_mark.py` reuses clothing constraints and measurement-quality checks for a continuous request. Three supported distinct frames of a unique candidate can select only in SIMULATED mode with synthetic input and a simulator bridge. LIVE supplies proposals for explicit human confirmation. Unsupported attributes, stale evidence, overlapping/look-alike people and responder tracks do not become automatic identity claims.

The existing one-shot person-mark path remains available. Requests are invalidated by incident/session/tracker-epoch/designation changes. Human binding uses existing incident evidence and does not start Follow or allow physical flight.

## Assistance and beacons

`missions/sar.py` scores observed people using explicitly simulated environmental context. Defaults are +3 for observed flood depth at least 0.15 m or fire keep-out, +1 for sufficiently stationary observations, threshold 3 and three supporting frames. Dry stationary people alone are insufficient. Responder tracks and ambiguous object attachments are excluded. Hidden actor casualty labels are reserved for evaluation, not detection.

The repaired base-launch rescue mission can also nominate a measured match to a reported missing-person request. Pending beacons link to the same incident observation/logical subject as SAR. The operator reviews evidence; confirmation/dispatch uses the existing action and alert system, rejection dispatches nothing, and retries do not duplicate notification. Stale or foreign-session review/dispatch is rejected.

Routes use `missions/sar_guidance.py` via the shared `crew_routing.assessed_route` connector: bounded 1 m A*, <=0.5 m edge samples, structures/bounds/flood/fire checks, explicit approach-only/blocked/unavailable outcomes and optional arrival-time hazard checks. The original road router remains. Location uncertainty is included in the arrival explanation; arrival does not claim exact physical contact with a hidden actor. All rescue movement and guidance here are simulated.

The crew does not move before approval. After reaching the assessed approach, the drone holds for ten seconds and returns to base. Tracking loss/staleness, unsafe ground and stop/manual override prevent continued movement. The responder receives the existing alert channel and ACK is persisted idempotently.

## Interfaces

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/incidents/{iid}/target` | Current search/subject and saved history |
| POST | `/api/incidents/{iid}/target/search` | Continuous description or clothing fields |
| POST | `/api/incidents/{iid}/target/confirm` | Human confirmation of a current request/candidate |
| GET | `/api/incidents/{iid}/sar` | Assistance observations, reviews, guidance and dispatch |
| POST | `/api/incidents/{iid}/sar/{oid}/review` | Session-scoped confirm/reject and explicit dispatch |
| POST | `/api/incidents/{iid}/person-mark/release` | Existing release path, also releases continuous search |
| POST | `/api/alerts/{id}/acknowledge` | Existing delivery acknowledgment |

The command page and simulation workspace share `TargetSARPanel`; the map shows measured trails, dashed predictions, responder positions and reviewed routes. The responder UI retains its delivery/ACK workflow. Request mutations follow existing authentication. Real-flight controls retain their independent permission, freshness, current-target, bridge and pilot-override checks.

## Demo and evidence

`make sar-demo` launches SAR1 on loopback port 8011; open `/command` and `/responder`. `make sar-eval` reproduces the 13-criterion rendered SAR acceptance. `make rescue-eval` reproduces the ten workflow checks for each of FR3, FF3 and SR5. The situation picker exposes these scenarios without replacing existing variants. Browser runners are `make e2e-sar` and `make e2e-workspace`.

Current evidence is under `evaluation/current/`. The full regression suite covers memory loss/track changes, ambiguity, stale/capture evidence, incident/session isolation, control scopes, rejection, routes, delivery/ACK and physical-flight separation. See the root audit for exact results and limitations. Clothing continuity is not identity verification; simulated assistance is not a medical finding or validated real-world rescue navigation.
