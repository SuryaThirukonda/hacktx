"""Engine-neutral decision types. Each engine adapter translates these to its own wire format."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..schemas import DecisionAnswer

Task = Literal["D1_reacquire", "D2_identity", "D3_describe", "D4_motion", "D5_priority", "D6_obstacle", "D7_lead_triage",
               "D8_next_sector", "D9_route", "D10_dispatch", "D11_opening", "D12_pursuit_route", "D13_intent"]


@dataclass
class Option:
    value: str
    description: str
    outcome: str | None = None  # measured or code-computed consequence of this option, stated with units
    unsafe: str | None = None  # set by code: why this option is removed before any engine is asked

    def text(self) -> str:
        return f"{self.description} Measured: {self.outcome}" if self.outcome else self.description


@dataclass
class Question:
    kind: Literal["choice", "predicate", "score"]
    name: str
    instructions: str
    options: list[Option] = field(default_factory=list)  # choice values / score levels (ordered)


@dataclass
class DecisionJob:
    task: Task
    facts: dict[str, Any]
    questions: list[Question]
    event_ids: list[str] = field(default_factory=list)
    image_jpeg: bytes | None = None
    context: dict[str, Any] = field(default_factory=dict)  # pipeline-side data needed to apply the answer
    created_ms: float = 0.0
    job_id: str = ""


@dataclass
class EngineResult:
    engine: str
    model: str | None
    status: Literal["success", "timeout", "error", "refusal"]
    latency_ms: float
    answers: list[DecisionAnswer] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] | None = None
    mock: bool = False

    def answer(self, name: str) -> DecisionAnswer | None:
        return next((a for a in self.answers if a.name == name), None)


PREAMBLE = (
    "You support a supervised first-responder training exercise with consenting participants. Use ONLY the "
    "supplied structured observations. Never infer intent, hostility, identity or criminality. Distances and speeds "
    "are measured by the system; do not invent new ones. If information is stale or uncertain, prefer the most "
    "conservative option (for notifications: no_alert)."
)
