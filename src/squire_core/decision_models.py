from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
