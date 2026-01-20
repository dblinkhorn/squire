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


def _format_frontmatter(frontmatter: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.append("  -")
                        for sub_key, sub_value in item.items():
                            lines.append(f"    {sub_key}: {sub_value}")
                    else:
                        lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


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
    frontmatter = yaml.load(parts[1], Loader=_NoDatesSafeLoader) or {}
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
    validate_json(load_json_schema(schema_path), canonical.frontmatter)

    object_type = canonical.frontmatter["type"]
    object_id = canonical.frontmatter["id"]
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

    content = f"{_format_frontmatter(canonical.frontmatter)}\n\n{body}\n"
    output_path.write_text(content, encoding="utf-8")
    return output_path
