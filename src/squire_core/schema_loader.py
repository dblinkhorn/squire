from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_schema(path: str | Path) -> dict[str, Any]:
    schema_path = Path(path)
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_json(schema: dict[str, Any], data: dict[str, Any]) -> None:
    try:
        from jsonschema import validate
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "jsonschema is required for validation. Install with: pip install jsonschema"
        ) from exc

    validate(instance=data, schema=schema)
