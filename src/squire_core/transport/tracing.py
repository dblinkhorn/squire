"""Shared trace helpers for matching and NL mutation normalization."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from squire_core.derived_event_store import write_derived_event
from squire_core.schema_loader import load_json_schema, validate_json

_NL_MUTATION_NORMALIZED_SCHEMA_PATH = Path("config/schemas/nl_mutation_normalized_v1.json")
_MATCHING_TRACE_SCHEMA_PATH = Path("config/schemas/matching_trace_v1.json")


def _coerce_non_empty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed


def build_nl_trace_operation(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": entry.get("operation_id"),
        "action_type": entry.get("action_type"),
        "target_ref": entry.get("target_ref"),
        "target_token": entry.get("target_token"),
        "target_resolved_id": entry.get("target_resolved_id"),
        "target_object_type": entry.get("target_object_type"),
        "op_status": entry.get("op_status"),
        "reason_code": entry.get("reason_code"),
        "normalization_notes": entry.get("normalization_notes", []),
        "normalized_fields": entry.get("normalized_fields", {}),
        "proposed_operation": entry.get("proposed_operation"),
    }


def build_unresolved_scope_from_entries(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    scope: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("op_status") != "unresolved":
            continue
        operation_id = _coerce_non_empty_str(entry.get("operation_id"))
        if operation_id is None:
            continue
        target_token = _coerce_non_empty_str(entry.get("target_token"))
        target_tokens: list[str] = []
        if target_token:
            target_tokens.append(target_token)
        existing = scope.get(operation_id)
        if existing is None:
            scope[operation_id] = {
                "action_type": _coerce_non_empty_str(entry.get("action_type")),
                "target_tokens": target_tokens,
                "reason_code": _coerce_non_empty_str(entry.get("reason_code")) or "clarification_insufficient",
            }
            continue
        for token in target_tokens:
            if token not in existing["target_tokens"]:
                existing["target_tokens"].append(token)
    return scope


def summarize_unresolved_scope(unresolved_scope: dict[str, dict[str, Any]]) -> str:
    if not unresolved_scope:
        return "Unresolved operations: none."
    parts: list[str] = []
    for operation_id, detail in unresolved_scope.items():
        reason_code = _coerce_non_empty_str(detail.get("reason_code")) or "clarification_insufficient"
        parts.append(f"{operation_id} ({reason_code})")
    return "Unresolved operations: " + ", ".join(parts)


def write_nl_mutation_normalized_trace(*, config: dict[str, Any], raw_event_id: str, payload: dict[str, Any]) -> None:
    derived_root = Path(config.get("paths", {}).get("events_derived", "events/derived"))
    try:
        schema = load_json_schema(_NL_MUTATION_NORMALIZED_SCHEMA_PATH)
        validate_json(schema, payload)
        write_derived_event(
            derived=payload,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="nl_mutation_normalized",
        )
    except Exception as exc:
        logging.warning("nl_mutation_normalized_write_failed id=%s error=%s", raw_event_id, exc)


def write_matching_trace(
    *,
    derived_root: str | Path,
    raw_event_id: str,
    trace_payload: dict[str, Any],
) -> None:
    try:
        schema = load_json_schema(_MATCHING_TRACE_SCHEMA_PATH)
        validate_json(schema, trace_payload)
        write_derived_event(
            derived=trace_payload,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="matching_trace",
        )
        logging.info("matching_trace_written id=%s mode=%s", raw_event_id, trace_payload.get("retrieval_mode"))
    except Exception as exc:
        logging.warning("matching_trace_write_failed id=%s error=%s", raw_event_id, exc)
