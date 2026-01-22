from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DecisionCandidate:
    object_id: str
    title: str
    snippet: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.object_id,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
        }


@dataclass(frozen=True)
class DecisionArtifact:
    schema_version: int
    raw_event_id: str
    object_type: str
    confidence: float
    candidates: list[DecisionCandidate]
    proposed_operations: list[dict[str, Any]]
    model: str | None = None
    prompt_version: str | None = None
    timestamp: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "raw_event_id": self.raw_event_id,
            "object_type": self.object_type,
            "confidence": self.confidence,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "proposed_operations": self.proposed_operations,
            "timestamp": self.timestamp or _now_iso(),
        }
        if self.model:
            payload["model"] = self.model
        if self.prompt_version:
            payload["prompt_version"] = self.prompt_version
        return payload
