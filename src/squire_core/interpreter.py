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


def interpret_text(
    provider: LLMProvider,
    text: str,
    model: str,
    system_prompt: str,
    schema_path: str | Path,
) -> Interpretation:
    schema = load_json_schema(schema_path)
    result: LLMResult = provider.interpret(text=text, schema=schema, model=model, system_prompt=system_prompt)
    validate_json(schema, result.payload)
    return Interpretation(derived=result.payload, raw_text=result.raw_text)
