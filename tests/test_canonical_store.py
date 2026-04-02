from __future__ import annotations

from pathlib import Path

import pytest

from squire_core import canonical_store
from squire_core.canonical_store import CanonicalObject, load_frontmatter, write_canonical_object
from squire_core.indexer import rebuild_index


def _write(frontmatter: dict[str, object], tmp_path: Path) -> Path:
    return write_canonical_object(
        canonical=CanonicalObject(frontmatter=frontmatter, body="Body"),
        objects_root=tmp_path / "objects",
        schema_path=Path("config/schemas/canonical_object_v1.json"),
    )


def test_write_canonical_object_preserves_special_text_scalars(tmp_path: Path) -> None:
    path = _write(
        {
            "id": "A_1",
            "type": "admin",
            "title": "true",
            "created_at": "2026-03-18T00:00:00+00:00",
            "updated_at": "2026-03-18T00:00:00+00:00",
            "status": "open",
            "next_action": "Investigate bug: admin items missing",
            "blocked_reason": "Waiting on: vendor",
        },
        tmp_path,
    )

    frontmatter = load_frontmatter(path)

    assert frontmatter["title"] == "true"
    assert isinstance(frontmatter["title"], str)
    assert frontmatter["next_action"] == "Investigate bug: admin items missing"
    assert frontmatter["blocked_reason"] == "Waiting on: vendor"

    stats = rebuild_index(tmp_path / "objects", tmp_path / "index.sqlite")
    assert stats.indexed_count == 1
    assert stats.skipped_count == 0


def test_write_canonical_object_coerces_free_text_fields_to_strings(tmp_path: Path) -> None:
    path = _write(
        {
            "id": "I_1",
            "type": "ideas",
            "title": 404,
            "created_at": "2026-03-18T00:00:00+00:00",
            "updated_at": "2026-03-18T00:00:00+00:00",
            "status": "open",
            "one_liner": False,
            "next_step": 7,
        },
        tmp_path,
    )

    frontmatter = load_frontmatter(path)

    assert frontmatter["title"] == "404"
    assert frontmatter["one_liner"] == "False"
    assert frontmatter["next_step"] == "7"


def test_write_canonical_object_raises_when_round_trip_validation_fails(tmp_path: Path, monkeypatch) -> None:
    def _fake_safe_dump(*args, **kwargs) -> str:
        return "title: true\n"

    monkeypatch.setattr(canonical_store.yaml, "safe_dump", _fake_safe_dump)

    with pytest.raises(ValueError, match="round-trip validation failed"):
        _write(
            {
                "id": "A_1",
                "type": "admin",
                "title": "Hello",
                "created_at": "2026-03-18T00:00:00+00:00",
                "updated_at": "2026-03-18T00:00:00+00:00",
                "status": "open",
                "next_action": "Do thing",
            },
            tmp_path,
        )
