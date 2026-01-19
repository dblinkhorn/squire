from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DerivedWriteResult:
    derived_path: Path | None
    raw_output_path: Path
    error_path: Path | None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ensure_dir(root: str | Path, raw_event_id: str | None, fallback: str) -> Path:
    directory = raw_event_id or fallback
    derived_dir = Path(root) / directory
    derived_dir.mkdir(parents=True, exist_ok=True)
    return derived_dir


def write_derived_event(
    *,
    derived: dict[str, Any] | None,
    raw_text: str,
    derived_root: str | Path,
    raw_event_id: str | None,
    label: str = "derived",
    error: Exception | None = None,
    fallback_id: str = "manual",
) -> DerivedWriteResult:
    timestamp = _timestamp()
    derived_dir = _ensure_dir(derived_root, raw_event_id, f"{fallback_id}_{timestamp}")

    raw_output_path = derived_dir / "raw_model_output.txt"
    raw_output_path.write_text(raw_text, encoding="utf-8")

    derived_path = None
    if derived is not None:
        derived_path = derived_dir / f"{label}_v1_{timestamp}.json"
        derived_path.write_text(json.dumps(derived, indent=2, sort_keys=True), encoding="utf-8")

    error_path = None
    if error is not None:
        error_path = derived_dir / "errors.json"
        error_payload = {
            "error": str(error),
            "type": error.__class__.__name__,
            "timestamp": timestamp,
        }
        error_path.write_text(json.dumps(error_payload, indent=2, sort_keys=True), encoding="utf-8")

    return DerivedWriteResult(
        derived_path=derived_path,
        raw_output_path=raw_output_path,
        error_path=error_path,
    )
