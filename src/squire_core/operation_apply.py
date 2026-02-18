from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from squire_core.canonical_store import CanonicalObject, load_frontmatter, write_canonical_object
from squire_core.id_utils import generate_ulid
from squire_core.schema_loader import load_json_schema, validate_json


@dataclass(frozen=True)
class ApplyResult:
    written_paths: list[Path]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fill_common(frontmatter: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    frontmatter.setdefault("created_at", now)
    frontmatter.setdefault("updated_at", now)
    frontmatter.setdefault("archived", False)
    frontmatter.setdefault("tags", [])
    return frontmatter


def _drop_nulls(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def _build_frontmatter(
    object_type: str,
    fields: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frontmatter = dict(existing) if existing else {}
    frontmatter["type"] = object_type
    frontmatter.update(_drop_nulls(fields))
    return _fill_common(frontmatter)


def _require(field: str, fields: dict[str, Any]) -> Any:
    value = fields.get(field)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {field}")
    return value


def _object_path(objects_root: str | Path, object_type: str, object_id: str) -> Path:
    directory = {
        "people": "people",
        "projects": "projects",
        "ideas": "ideas",
        "admin": "admin",
    }.get(object_type)
    if not directory:
        raise ValueError(f"Unsupported object type: {object_type}")
    prefix = {
        "people": "P_",
        "projects": "PR_",
        "ideas": "I_",
        "admin": "A_",
    }.get(object_type, "X_")
    return Path(objects_root) / directory / f"{prefix}{object_id}.md"


def _load_existing_frontmatter(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_frontmatter(path)


def _merge_source_event_ids(existing: list[Any] | None, additional: list[Any] | None, new_id: str) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for source in (existing, additional):
        if not source:
            continue
        for item in source:
            if not isinstance(item, str):
                continue
            if item not in seen:
                merged.append(item)
                seen.add(item)
    if new_id and new_id not in seen:
        merged.append(new_id)
    return merged


def _admin_due_mode(incoming_fields: dict[str, Any]) -> str | None:
    due_at = incoming_fields.get("due_at")
    if due_at not in (None, ""):
        return "due_at"
    due_date = incoming_fields.get("due_date")
    if due_date not in (None, ""):
        return "due_date"
    return None


def apply_operations(
    derived: dict[str, Any],
    objects_root: str | Path,
    canonical_schema_path: str | Path,
    derived_schema_path: str | Path | None = None,
    last_decision_id: str | None = None,
) -> ApplyResult:
    if derived_schema_path is not None:
        validate_json(load_json_schema(derived_schema_path), derived)

    default_object_type = derived["object_type"]
    raw_event_id = derived.get("raw_event_id")
    ops = derived.get("proposed_operations", [])
    written: list[Path] = []

    for op in ops:
        op_object_type = op.get("object_type", default_object_type)
        if not isinstance(op_object_type, str) or not op_object_type.strip():
            raise ValueError("Operation object_type must be a non-empty string")
        object_type = op_object_type.strip()
        action = op.get("op")
        if action not in {"create", "append", "update"}:
            raise ValueError(f"Unsupported operation: {action}")

        incoming_fields = op.get("fields") or {}
        fields = incoming_fields
        extracted = derived.get("extracted_fields") or {}
        if extracted:
            merged = dict(extracted)
            merged.update(fields)
            fields = merged
        due_mode = _admin_due_mode(fields) if object_type == "admin" else None
        existing_frontmatter: dict[str, Any] = {}
        if action in {"append", "update"}:
            target_id = op.get("target_id") or fields.get("id")
            if not target_id:
                raise ValueError("Missing target_id for append/update operation")
            object_id = target_id
            existing_path = _object_path(objects_root, object_type, object_id)
            if not existing_path.exists():
                raise ValueError("Target object not found for append/update operation")
            existing_frontmatter = _load_existing_frontmatter(existing_path)
            existing_type = existing_frontmatter.get("type")
            if existing_type and existing_type != object_type:
                raise ValueError("Target object type does not match operation type")
            fields = dict(existing_frontmatter) | dict(fields)
            fields["id"] = object_id
            fields["updated_at"] = _now_iso()
            if due_mode == "due_at":
                fields.pop("due_date", None)
                existing_frontmatter.pop("due_date", None)
            elif due_mode == "due_date":
                fields.pop("due_at", None)
                existing_frontmatter.pop("due_at", None)
        else:
            object_id = fields.get("id") or generate_ulid()
            fields = dict(fields)
            fields["id"] = object_id
            if due_mode == "due_at":
                fields.pop("due_date", None)
            elif due_mode == "due_date":
                fields.pop("due_at", None)

        body_field = fields.pop("body", None)

        title = fields.get("title")
        if not title and object_type == "people":
            title = _require("name", fields)
        if not title and object_type == "ideas":
            title = _require("one_liner", fields)
        if not title:
            title = _require("title", fields)
        fields["title"] = title

        if raw_event_id:
            existing_ids = existing_frontmatter.get("source_event_ids") if existing_frontmatter else None
            incoming_ids = fields.get("source_event_ids")
            fields["source_event_ids"] = _merge_source_event_ids(existing_ids, incoming_ids, raw_event_id)
        if last_decision_id:
            fields["last_decision_id"] = last_decision_id

        if object_type == "people":
            _require("name", fields)
        if object_type == "projects":
            _require("next_action", fields)
            _require("status", fields)
        if object_type == "ideas":
            _require("one_liner", fields)
        if object_type == "admin":
            if not fields.get("next_action") and fields.get("title"):
                fields["next_action"] = fields["title"]
            if not fields.get("status"):
                fields["status"] = "open"
            _require("next_action", fields)
            _require("status", fields)

        frontmatter = _build_frontmatter(object_type, fields, existing_frontmatter or None)
        body = body_field or fields.get("next_action") or title
        append_text = None

        if action == "append":
            append_text = body_field or fields.get("next_action") or title

        path = write_canonical_object(
            canonical=CanonicalObject(frontmatter=frontmatter, body=body),
            objects_root=objects_root,
            schema_path=canonical_schema_path,
            append_text=append_text,
        )
        written.append(path)

    return ApplyResult(written_paths=written)
