from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from squire_core.canonical_store import CanonicalObject, write_canonical_object
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
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) != 3:
        return {}
    frontmatter_raw = parts[1]
    return yaml.safe_load(frontmatter_raw) or {}


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


def apply_operations(
    derived: dict[str, Any],
    objects_root: str | Path,
    canonical_schema_path: str | Path,
    derived_schema_path: str | Path | None = None,
) -> ApplyResult:
    if derived_schema_path is not None:
        validate_json(load_json_schema(derived_schema_path), derived)

    object_type = derived["object_type"]
    raw_event_id = derived.get("raw_event_id")
    ops = derived.get("proposed_operations", [])
    written: list[Path] = []

    for op in ops:
        action = op.get("op")
        if action not in {"create", "append"}:
            raise ValueError(f"Unsupported operation: {action}")

        fields = op.get("fields") or {}
        extracted = derived.get("extracted_fields") or {}
        if extracted:
            merged = dict(extracted)
            merged.update(fields)
            fields = merged
        title = fields.get("title")
        if not title and object_type == "people":
            title = _require("name", fields)
        if not title and object_type == "ideas":
            title = _require("one_liner", fields)
        if not title:
            title = _require("title", fields)
        fields["title"] = title
        object_id = fields.get("id") or generate_ulid()
        fields = dict(fields)
        fields["id"] = object_id

        existing_frontmatter: dict[str, Any] = {}
        if action == "append":
            target_id = op.get("target_id") or fields.get("id")
            if not target_id:
                raise ValueError("Missing target_id for append operation")
            object_id = target_id
            fields["id"] = object_id
            existing_frontmatter = _load_existing_frontmatter(_object_path(objects_root, object_type, object_id))
            fields["updated_at"] = _now_iso()

        if raw_event_id:
            existing_ids = existing_frontmatter.get("source_event_ids") if existing_frontmatter else None
            incoming_ids = fields.get("source_event_ids")
            fields["source_event_ids"] = _merge_source_event_ids(existing_ids, incoming_ids, raw_event_id)

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
        body = fields.get("body") or fields.get("next_action") or title
        append_text = None

        if action == "append":
            append_text = fields.get("body") or fields.get("next_action") or title

        path = write_canonical_object(
            canonical=CanonicalObject(frontmatter=frontmatter, body=body),
            objects_root=objects_root,
            schema_path=canonical_schema_path,
            append_text=append_text,
        )
        written.append(path)

    return ApplyResult(written_paths=written)
