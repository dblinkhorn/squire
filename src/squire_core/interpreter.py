from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from squire_core.llm.provider import LLMProvider, LLMResult
from squire_core.schema_loader import load_json_schema, validate_json


@dataclass(frozen=True)
class Interpretation:
    derived: dict[str, Any]
    raw_text: str


class InterpretationValidationError(ValueError):
    def __init__(self, message: str, raw_text: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.payload = payload


def interpret_text(
    provider: LLMProvider,
    text: str,
    model: str,
    system_prompt: str,
    schema_path: str | Path,
) -> Interpretation:
    schema = load_json_schema(schema_path)
    result: LLMResult = provider.interpret(text=text, schema=schema, model=model, system_prompt=system_prompt)
    try:
        validate_json(schema, result.payload)
    except Exception as exc:
        raise InterpretationValidationError(str(exc), raw_text=result.raw_text, payload=result.payload) from exc
    return Interpretation(derived=result.payload, raw_text=result.raw_text)
