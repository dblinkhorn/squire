from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.loader import SafeLoader

from squire_core.schema_loader import load_json_schema, validate_json


@dataclass(frozen=True)
class CanonicalObject:
    frontmatter: dict[str, Any]
    body: str


_TEXT_FRONTMATTER_FIELDS = {
    "title",
    "name",
    "context",
    "follow_ups",
    "next_action",
    "goal",
    "blocked_reason",
    "one_liner",
    "next_step",
}


def _normalize_frontmatter(frontmatter: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in frontmatter.items():
        if key in _TEXT_FRONTMATTER_FIELDS and value is not None and not isinstance(value, str):
            normalized[key] = str(value)
            continue
        normalized[key] = value
    return normalized


def _parse_frontmatter_text(frontmatter_text: str) -> dict[str, Any]:
    frontmatter = yaml.load(frontmatter_text, Loader=_NoDatesSafeLoader) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError("Frontmatter must deserialize to a mapping")
    return frontmatter


def _format_frontmatter(frontmatter: dict[str, Any]) -> str:
    normalized = _normalize_frontmatter(frontmatter)
    dumped = yaml.safe_dump(
        normalized,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).strip()
    parsed = _parse_frontmatter_text(dumped)
    if parsed != normalized:
        raise ValueError("Frontmatter round-trip validation failed")
    return f"---\n{dumped}\n---"


_TYPE_DIR = {
    "people": "people",
    "projects": "projects",
    "ideas": "ideas",
    "admin": "admin",
}
_TYPE_PREFIX = {
    "people": "P_",
    "projects": "PR_",
    "ideas": "I_",
    "admin": "A_",
}


class _NoDatesSafeLoader(SafeLoader):
    pass


for _ch, _patterns in list(_NoDatesSafeLoader.yaml_implicit_resolvers.items()):
    _NoDatesSafeLoader.yaml_implicit_resolvers[_ch] = [
        (tag, regexp) for tag, regexp in _patterns if tag != "tag:yaml.org,2002:timestamp"
    ]


def _object_path(objects_root: str | Path, object_type: str, object_id: str) -> Path:
    directory = _TYPE_DIR.get(object_type)
    if not directory:
        raise ValueError(f"Unsupported object type: {object_type}")
    prefix = _TYPE_PREFIX.get(object_type, "X_")
    return Path(objects_root) / directory / f"{prefix}{object_id}.md"


def find_object_path(objects_root: str | Path, object_id: str) -> Path | None:
    root = Path(objects_root)
    for object_type, directory in _TYPE_DIR.items():
        prefix = _TYPE_PREFIX.get(object_type, "X_")
        candidate = root / directory / f"{prefix}{object_id}.md"
        if candidate.exists():
            return candidate
    return None


def load_frontmatter(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Invalid frontmatter format")
    frontmatter = _parse_frontmatter_text(parts[1])
    if frontmatter.get("tags") is None:
        frontmatter["tags"] = []
    if frontmatter.get("source_event_ids") is None:
        frontmatter["source_event_ids"] = []
    return frontmatter


def write_canonical_object(
    canonical: CanonicalObject,
    objects_root: str | Path,
    schema_path: str | Path,
    append_text: str | None = None,
) -> Path:
    normalized_frontmatter = _normalize_frontmatter(canonical.frontmatter)
    validate_json(load_json_schema(schema_path), normalized_frontmatter)

    object_type = normalized_frontmatter["type"]
    object_id = normalized_frontmatter["id"]
    output_path = _object_path(objects_root, object_type, object_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing_body = ""
    if output_path.exists():
        existing_content = output_path.read_text(encoding="utf-8")
        parts = existing_content.split("---", 2)
        if len(parts) == 3:
            existing_body = parts[2].lstrip("\n")

    body = canonical.body.strip()
    if append_text:
        body = (existing_body.rstrip("\n") + "\n\n" + append_text.strip()).strip()
    elif existing_body:
        body = existing_body.strip()

    content = f"{_format_frontmatter(normalized_frontmatter)}\n\n{body}\n"
    output_path.write_text(content, encoding="utf-8")
    return output_path
