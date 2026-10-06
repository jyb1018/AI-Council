"""Validated public contracts. Model output is data, never an executable instruction."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,48}$")]
Notes = Annotated[list[Text], Field(max_length=12)]
SourceIDs = Annotated[list[Identifier], Field(max_length=20)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Contract):
    id: Identifier
    title: Text
    content: Annotated[str, Field(min_length=1, max_length=12000)]
    url: Annotated[str, StringConstraints(pattern=r"^https?://", max_length=2000)] | None = None


class Request(Contract):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
    context: Annotated[str, Field(max_length=60000)] = ""
    participants: Annotated[list[Identifier], Field(min_length=1, max_length=6)]
    chair: Identifier | None = None
    mode: Literal["ask", "debate", "review"] = "debate"
    rounds: Annotated[int, Field(ge=0, le=3)] = 1
    language: Annotated[str, Field(min_length=1, max_length=80)] = "Korean (polite)"
    sources: Annotated[list[Source], Field(max_length=20)] = Field(default_factory=list)
    idempotency_key: Identifier | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> Request:
        if len(set(self.participants)) != len(self.participants):
            raise ValueError("participants must be distinct configured provider IDs")
        if self.chair is not None and self.chair not in self.participants:
            raise ValueError("chair must be a participant")
        if self.mode == "ask":
            if len(self.participants) != 1 or self.rounds != 0:
                raise ValueError("ask requires one participant and rounds=0")
        elif len(self.participants) < 2 or self.rounds == 0:
            raise ValueError("debate/review requires at least two participants and 1-3 rounds")
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("source IDs must be unique")
        return self

    @property
    def planned_calls(self) -> int:
        return 1 if self.mode == "ask" else len(self.participants) * (1 + 2 * self.rounds) + 1


class Position(Contract):
    answer: Answer
    rationale: Notes
    assumptions: Notes
    risks: Notes
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    supporting_sources: SourceIDs


class Critique(Contract):
    target: Identifier
    strength: Text
    weakness: Text
    recommendation: Text
    severity: Literal["low", "medium", "high"]


class Critiques(Contract):
    critiques: Annotated[list[Critique], Field(min_length=1, max_length=5)]


class Revision(Position):
    changes: Notes


class Verdict(Contract):
    answer: Answer
    agreements: Notes
    disagreements: Notes
    next_steps: Notes
    limitations: Notes
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    supporting_sources: SourceIDs


SCHEMAS = {"position": Position, "critique": Critiques, "revision": Revision, "synthesis": Verdict}


class CouncilError(Exception):
    """A stable error code and a deliberately non-sensitive diagnostic."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}
