from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from squire_core.canonical_store import CanonicalObject, write_canonical_object
from squire_core.surfacing import build_daily_digest


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
) -> dict:
    return {
        "id": object_id,
        "type": object_type,
        "title": title,
        "created_at": created_at,
        "updated_at": updated_at,
        "archived": False,
    }


def test_daily_digest_rules(tmp_path: Path) -> None:
    now = datetime(2026, 1, 22, 9, 0, tzinfo=timezone.utc)
    objects_root = tmp_path / "objects"
    created_at = "2026-01-01T00:00:00+00:00"
    updated_at = "2026-01-10T00:00:00+00:00"

    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM001",
                object_type="admin",
                title="Pay rent",
                created_at=created_at,
                updated_at=updated_at,
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
                updated_at=updated_at,
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
                updated_at=updated_at,
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
                object_id="ADM004",
                object_type="admin",
                title="File taxes",
                created_at=created_at,
                updated_at=updated_at,
            ),
            "status": "open",
            "next_action": "File taxes",
        },
    )
    _write_object(
        objects_root,
        {
            **_base_frontmatter(
                object_id="ADM005",
                object_type="admin",
                title="Update budget",
                created_at=created_at,
                updated_at=updated_at,
            ),
            "status": "open",
            "next_action": "Update budget",
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
                updated_at=updated_at,
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
                updated_at=updated_at,
            ),
            "name": "Alex",
            "next_contact": "2026-01-22",
        },
    )

    config = {
        "timezone": "UTC",
        "surfacing": {
            "admin": {"due_soon_days": 1, "include_open_limit": 1},
            "projects": {"stale_days": 14},
            "people": {"next_contact_days": 0},
        },
    }

    digest = build_daily_digest(objects_root, config, now=now)
    sections = {section.title: section.lines for section in digest.sections}

    due_lines = sections["Admin due/overdue"]
    assert any("[overdue]" in line and "ADM001" in line for line in due_lines)
    assert any("[today]" in line and "ADM002" in line for line in due_lines)
    assert any("[soon]" in line and "ADM003" in line for line in due_lines)

    open_lines = sections.get("Open admin")
    assert open_lines is not None
    assert len(open_lines) == 1
    assert ("ADM004" in open_lines[0]) or ("ADM005" in open_lines[0])

    stuck_lines = sections.get("Stuck item")
    assert stuck_lines is not None
    assert "PR001" in stuck_lines[0]

    suggestions = sections["Suggested next actions"]
    assert len(suggestions) <= 3
    assert any("ADM001" in line for line in suggestions)

    people_lines = sections.get("People to follow up")
    assert people_lines is not None
    assert any("P001" in line for line in people_lines)
