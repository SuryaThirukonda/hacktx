# Decision engines

Code computes facts; models make bounded, typed judgments on them. The router (`decisions/router.py`) enforces a
deadline (`decisions.deadline_ms`, default 800 ms), falls back (`fallback_chain`, then rules), treats refusals as
"no optional action", rejects answers outside the offered options, and only acts on confidence ≥
`min_confidence` (identity tasks ≥ 0.75). Every call is logged with engine, latency, probabilities, the rules
baseline and whether it was acted on.

## Tasks

| Task | Question(s) | Acted on | Fallback |
|---|---|---|---|
| D1 reacquire | choice `search_action` (continue_predicted, yaw_left_scan, yaw_right_scan, climb_and_widen, orbit_last_seen, hold_and_alert, other) + predicate `target_likely_occluded` + score `urgency` | the bounded primitive the autonomy controller executes | rules (occluder → orbit, left the view → yaw scan, …) |
| D2 identity | choice `which_candidate` (A…, none) + predicate | confirms an UNCERTAIN lock at ≥ 0.75 | stays UNCERTAIN; operator confirms |
| D3 describe | choice `match` over labelled candidates (clothing colour words; tiles only if images allowed) | a *proposal* the operator must confirm | colour keyword match |
| D4 motion | choice `behavior` (observable motion only) | advisory label, never gates alerts | thresholds on speed/closing |
| D5 notify | choice `notification` (no_alert + pending informational event IDs) | which informational alert is spoken | fixed priority; repeats suppressed |

Option IDs are neutral and every choice includes an escape (`other`/`none`/`no_alert`).

## Wire formats (from the vendor docs, read )

**OpenAI Decisions** — `POST https://api.openai.com/v1/decisions`, model `gpt-6-luna`:

```json
{"model": "gpt-6-luna",
 "input": [{"role": "developer", "content": [{"type": "input_text", "text": "<policy preamble>"}]},
           {"role": "user", "content": [{"type": "input_json", "data": {"...facts": "..."}},
                                        {"type": "input_image", "image_url": "data:image/jpeg;base64,..."}]}],
 "questions": [{"type": "choice", "name": "search_action", "instructions": "...",
                "choices": [{"value": "orbit_last_seen", "description": "..."}]},
               {"type": "predicate", "name": "target_likely_occluded", "instructions": "..."},
               {"type": "score", "name": "urgency", "instructions": "...",
                "levels": [{"label": "low", "description": "..."}]}]}
```

Answers come back as a list matched by `name`: choice → `choice`, `confidence`, `probabilities` as
`[{value, probability}]`; predicate → `probability`; score → `score`, `confidence`, `probabilities` as
`[{value, label, probability}]`; or `{"type": "refusal"}`. The create-reference page was not reachable when this was
written, so the response envelope beyond `answers`/`usage` and error semantics are handled defensively.

**TypeSafe Jev** — `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer`, model `jev-latest`:
questions are a map name → `{type: noul|choice|score, instructions, criteria}`; images go in an `assets` map
referenced from `state` with header `TypeSafe-Danger-Zone-Images: true` (280–1568 px per side; crop tiles are padded
to ≥ 280 px). Answers are a map by name: noul → probability, choice → `choice`/`confidence`/`probabilities{}`,
score → `score`/`confidence`/`legend`/`probabilities{}`.

**Strands Decider** — local `POST http://127.0.0.1:8099/v1/systemone` with the same question shapes, no auth,
text-only (its vision tower is not trained on images).

## Injected failures (Sim & faults tab)

`api_timeout` (sleeps past the deadline), `api_429`, `api_refusal`, `network_loss` apply to every cloud engine
including the mock, so the fallback path can be demonstrated without keys.

## Scenario S16: engine vs fixed rules, in the loop

Six scripted conflicts between informational alerts are injected into the arbiter (simulation only, labelled
`scripted`), each with a ground-truth answer and its reason written into the scenario before anything runs. The
report scores the engine's D5 choice, the rules baseline logged with every decision, and what was actually
announced. Fixed priorities get 3/6: three conflicts need context they ignore (a responder busy with an obstacle,
an approaching subject at 9 m versus a far zone entry, a stale reacquisition report). Without a key the "engine"
is the mock, which mirrors the rules and is flagged MOCK in the report. With a key:

```bash
cd backend && .venv/bin/python scripts/run_scenarios.py S16 --engine openai
```

calls the real engine for every decision (measured latency replayed in simulation time).

## Live results with OpenAI Decisions ()

First calls with the real key, `gpt-6-luna`, warm connection:

| Measure | Result |
|---|---|
| Latency | p50 ~185 ms, p95 ~270-290 ms (a cold first call took ~1.3 s, so the engine pre-opens its connection) |
| Designed cases (agreement with the designed answer, 3 repeats, renamed option IDs give the same scores) | D1 0.8 · D2 1.0 · D3 1.0 · D4 0.67 · D5 1.0 |
| S16 in the loop (6 scripted conflicts, written answers) | engine 6/6 · fixed rules 3/6 · 4/6 announced (two right answers were below the 0.7 gate) |

What the live API taught us (all now in `decisions/tasks.py`):

* Predicates must be phrased "True if ...; false otherwise". Statements or questions came back as refusals.
* "Is candidate A the same person as the reference" was refused. D2 is now worded as what the system actually
  decides - which candidate track continues the interrupted one - and is answered (1.0 on the designed cases).
* Facts must be self-describing. `prediction_bearing_rel_drone_deg: -70` gave left/right confusion; adding
  "outside the camera view, about 70 degrees to the LEFT", "no person is detected there" and whether cover was seen
  at the last-seen point fixed D1 (0.6 -> 0.8) and made it orbit a hidden subject instead of always continuing.
* The first D5 wording ("prefer no_alert when...") made the model too quiet (S16 2/6). Restating the policy
  precisely took it to 6/6. The wording was revised once with the S16 conflicts in view, so treat 6/6 as
  optimistic; the separate designed D5 cases also went from 0.67 to 1.0.
* D1 confidences are often 0.2-0.6. D1 acts at >= 0.5 (every option is a bounded manoeuvre under the safety
  supervisor); below that the rules' pick is flown for the same 3 s and the card says so.

## Benchmark

`make bench-decisions` runs designed cases for D1–D5 on each available engine: accuracy against the designed
answer, true latency p50/p95 (6 s timeout), refusal/error rates, cost from `usage.input_tokens`, and a
label-sensitivity pass with neutral option IDs. Engines without keys are reported NOT RUN.
