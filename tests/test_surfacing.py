from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from squire_core.canonical_store import CanonicalObject, write_canonical_object
from squire_core.indexer import rebuild_index
from squire_core.surfacing import build_daily_digest, build_find_list, build_item_detail, build_recent_list


def _write_object(objects_root: Path, frontmatter: dict, body: str = "") -> None:
    schema_path = Path("config/schemas/canonical_object_v1.json")
    write_canonical_object(
        canonical=CanonicalObject(frontmatter=frontmatter, body=body),
        objects_root=objects_root,
        schema_path=schema_path,
    )


def _base_frontmatter(
    *,
    object_id: str,
    object_type: str,
    title: str,
    created_at: str,
    updated_at: str,
    archived: bool = False,
) -> dict:
    return {
        "id": object_id,
        "type": object_type,
        "title": title,
        "created_at": created_at,
        "updated_at": updated_at,
        "archived": archived,
    }


def test_daily_digest_sections_without_ids(tmp_path: Path) -> None:
    now = datetime(2026, 1, 22, 9, 0, tzinfo=timezone.utc)
    objects_root = tmp_path / "objects"
    created_at = "2026-01-01T00:00:00+00:00"

    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM001",
                object_type="admin",
                title="Pay rent",
                created_at=created_at,
                updated_at="2026-01-10T00:00:00+00:00",
            ),
            "status": "open",
            "next_action": "Pay rent",
            "due_date": "2026-01-20",
            "priority": "high",
        },
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM002",
                object_type="admin",
                title="Call vet",
                created_at=created_at,
                updated_at="2026-01-11T00:00:00+00:00",
            ),
            "status": "open",
            "next_action": "Call vet",
            "due_at": "2026-01-22T15:00:00+00:00",
        },
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM003",
                object_type="admin",
                title="Submit report",
                created_at=created_at,
                updated_at="2026-01-12T00:00:00+00:00",
            ),
            "status": "open",
            "next_action": "Submit report",
            "due_date": "2026-01-23",
        },
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="PR001",
                object_type="projects",
                title="Launch beta",
                created_at=created_at,
                updated_at="2026-01-10T00:00:00+00:00",
            ),
            "status": "blocked",
            "next_action": "Wait on vendor",
            "blocked_reason": "Waiting on vendor",
        },
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="P001",
                object_type="people",
                title="Alex",
                created_at=created_at,
                updated_at="2026-01-10T00:00:00+00:00",
            ),
            "name": "Alex",
            "next_contact": "2026-01-22",
        },
    )

    config = {
        "timezone": "UTC",
        "surfacing": {
            "output": {"include_ids": False},
            "admin": {"due_soon_days": 1},
            "projects": {"stale_days": 14, "blocked_limit": 2},
            "people": {"next_contact_days": 0},
        },
    }

    digest = build_daily_digest(objects_root, config, now=now)
    sections = {section.title: section.lines for section in digest.sections}

    assert "Suggested next actions" not in sections
    assert any("Pay rent" in line for line in sections["Admin overdue"])
    assert any("Call vet" in line for line in sections["Admin due today"])
    assert any("Submit report" in line for line in sections["Admin due soon"])
    assert any("Launch beta" in line for line in sections["Projects needing attention"])
    assert any("Alex" in line for line in sections["People to follow up"])

    all_lines = [line for lines in sections.values() for line in lines]
    assert all("ADM001" not in line for line in all_lines)
    assert all("PR001" not in line for line in all_lines)


def test_daily_digest_can_include_ids(tmp_path: Path) -> None:
    now = datetime(2026, 1, 22, 9, 0, tzinfo=timezone.utc)
    objects_root = tmp_path / "objects"

    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM900",
                object_type="admin",
                title="Call dentist",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-10T00:00:00+00:00",
            ),
            "status": "open",
            "next_action": "Call dentist",
            "due_date": "2026-01-22",
        },
    )

    config = {
        "timezone": "UTC",
        "surfacing": {
            "output": {"include_ids": True},
            "admin": {"due_soon_days": 1},
        },
    }

    digest = build_daily_digest(objects_root, config, now=now)
    due_today = next(section for section in digest.sections if section.title == "Admin due today")
    assert any("ADM900" in line for line in due_today.lines)


def test_build_recent_list_orders_and_skips_archived(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"

    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="A_1",
                object_type="admin",
                title="Newest active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-25T00:00:00+00:00",
            ),
            "status": "open",
            "next_action": "Do it",
        },
        body="Body A",
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="A_2",
                object_type="admin",
                title="Archived note",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-26T00:00:00+00:00",
                archived=True,
            ),
            "status": "open",
            "next_action": "Ignore",
        },
        body="Body B",
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="A_3",
                object_type="ideas",
                title="Older active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-20T00:00:00+00:00",
            ),
            "one_liner": "Idea",
        },
        body="Body C",
    )

    config = {"timezone": "UTC", "surfacing": {"pull": {"default_recent_limit": 2}}}
    surfaced = build_recent_list(objects_root, config)

    assert surfaced.object_ids == ["A_1", "A_3"]
    assert len(surfaced.lines) == 2
    assert surfaced.lines[0].startswith("1. Newest active")
    assert all("A_1" not in line and "A_3" not in line for line in surfaced.lines)


def test_build_find_list_and_item_detail(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    index_db = tmp_path / "index.sqlite"

    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM_DENTIST",
                object_type="admin",
                title="Call dentist",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-22T00:00:00+00:00",
            ),
            "status": "open",
            "next_action": "Call dentist",
            "due_date": "2026-01-23",
        },
        body="Need to reschedule cleaning appointment.",
    )

    rebuild_index(objects_root, index_db)
    config = {"timezone": "UTC", "surfacing": {"output": {"include_ids": False}}}

    surfaced = build_find_list(objects_root, index_db, config, "dentist")
    assert surfaced.object_ids == ["ADM_DENTIST"]
    assert len(surfaced.lines) == 1
    assert surfaced.lines[0].startswith("1. Call dentist")
    assert "ADM_DENTIST" not in surfaced.lines[0]

    detail = build_item_detail(objects_root, surfaced.object_ids[0], config)
    assert detail is not None
    assert "Title: Call dentist" in detail
    assert "Type: admin" in detail
    assert "ID:" not in detail
    assert "Notes:" in detail



def test_build_find_list_blank_query_returns_empty(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    index_db = tmp_path / "index.sqlite"
    config = {"timezone": "UTC"}
    surfaced = build_find_list(objects_root, index_db, config, "   ")
    assert surfaced.lines == []
    assert surfaced.object_ids == []
