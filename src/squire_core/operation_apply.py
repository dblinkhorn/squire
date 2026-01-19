from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _build_frontmatter(object_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    frontmatter = {"type": object_type}
    frontmatter.update(_drop_nulls(fields))
    return _fill_common(frontmatter)


def _require(field: str, fields: dict[str, Any]) -> Any:
    value = fields.get(field)
    if value in (None, ""):
        raise ValueError(f"Missing required field: {field}")
    return value


def apply_operations(
    derived: dict[str, Any],
    objects_root: str | Path,
    canonical_schema_path: str | Path,
    derived_schema_path: str | Path | None = None,
) -> ApplyResult:
    if derived_schema_path is not None:
        validate_json(load_json_schema(derived_schema_path), derived)

    object_type = derived["object_type"]
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

        frontmatter = _build_frontmatter(object_type, fields)
        body = fields.get("body") or fields.get("next_action") or title
        append_text = None

        if action == "append":
            target_id = op.get("target_id") or fields.get("id")
            if not target_id:
                raise ValueError("Missing target_id for append operation")
            object_id = target_id
            frontmatter["id"] = object_id
            append_text = fields.get("body") or fields.get("next_action") or title

        path = write_canonical_object(
            canonical=CanonicalObject(frontmatter=frontmatter, body=body),
            objects_root=objects_root,
            schema_path=canonical_schema_path,
            append_text=append_text,
        )
        written.append(path)

    return ApplyResult(written_paths=written)

