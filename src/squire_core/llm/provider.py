from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResult:
    payload: dict
    raw_text: str


class LLMProvider(Protocol):
    def interpret(self, text: str, schema: dict, model: str, system_prompt: str) -> LLMResult:
        ...
