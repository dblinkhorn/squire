from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from squire_core.canonical_store import load_frontmatter
from squire_core.test_seed import ensure_test_safe_archive_root, seed_test_canonical_objects


def _object_path(objects_root: Path, object_type: str, object_id: str) -> Path:
    directory = {
        "admin": "admin",
        "projects": "projects",
        "people": "people",
        "ideas": "ideas",
    }[object_type]
    prefix = {
        "admin": "A_",
        "projects": "PR_",
        "people": "P_",
        "ideas": "I_",
    }[object_type]
    return objects_root / directory / f"{prefix}{object_id}.md"


def test_ensure_test_safe_archive_root_rejects_unsafe_path() -> None:
    unsafe = Path.home() / "squire-ci-unsafe-archive"
    with pytest.raises(ValueError, match="test-safe"):
        ensure_test_safe_archive_root(unsafe)


def test_ensure_test_safe_archive_root_accepts_squire_test_segment(tmp_path: Path) -> None:
    safe = tmp_path / "squire-test-archive"
    safe.mkdir(parents=True, exist_ok=True)
    assert ensure_test_safe_archive_root(safe) == safe.resolve()


def test_seed_test_canonical_objects_writes_expected_dataset(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    now = datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc)

    stats = seed_test_canonical_objects(
        objects_root=objects_root,
        schema_path=Path("config/schemas/canonical_object_v1.json"),
        now=now,
    )

    assert stats.admin_count == 7
    assert stats.projects_count == 3
    assert stats.people_count == 2
    assert stats.ideas_count == 2

    overdue = load_frontmatter(_object_path(objects_root, "admin", "TEST_ADMIN_OVERDUE_OPEN"))
    due_today = load_frontmatter(_object_path(objects_root, "admin", "TEST_ADMIN_DUE_TODAY_OPEN"))
    due_soon = load_frontmatter(_object_path(objects_root, "admin", "TEST_ADMIN_DUE_SOON_OPEN"))
    due_at_open = load_frontmatter(_object_path(objects_root, "admin", "TEST_ADMIN_DUE_AT_OPEN"))
    due_at_blocked = load_frontmatter(_object_path(objects_root, "admin", "TEST_ADMIN_DUE_AT_BLOCKED"))
    done_admin = load_frontmatter(_object_path(objects_root, "admin", "TEST_ADMIN_DONE"))
    stale_project = load_frontmatter(_object_path(objects_root, "projects", "TEST_PROJECT_STALE"))
    overdue_person = load_frontmatter(_object_path(objects_root, "people", "TEST_PERSON_OVERDUE"))
    done_idea = load_frontmatter(_object_path(objects_root, "ideas", "TEST_IDEA_DONE_RECENT"))

    for frontmatter in (
        overdue,
        due_today,
        due_soon,
        due_at_open,
        due_at_blocked,
        done_admin,
        stale_project,
        overdue_person,
        done_idea,
    ):
        assert isinstance(frontmatter.get("id"), str)
        assert isinstance(frontmatter.get("type"), str)
        assert isinstance(frontmatter.get("title"), str)
        assert isinstance(frontmatter.get("created_at"), str)
        assert isinstance(frontmatter.get("updated_at"), str)
        assert frontmatter.get("archived") is False
        assert "seed-test" in frontmatter.get("tags", [])
        created_at = datetime.fromisoformat(str(frontmatter["created_at"]))
        updated_at = datetime.fromisoformat(str(frontmatter["updated_at"]))
        assert created_at.tzinfo is not None
        assert updated_at.tzinfo is not None

    today = now.date().isoformat()
    assert due_today["due_date"] == today
    assert due_soon["due_date"] == (now.date() + timedelta(days=1)).isoformat()
    due_at_open_value = datetime.fromisoformat(str(due_at_open["due_at"]))
    due_at_blocked_value = datetime.fromisoformat(str(due_at_blocked["due_at"]))
    assert due_at_open_value == now + timedelta(hours=4)
    assert due_at_blocked_value == now + timedelta(hours=20)
    assert overdue["status"] == "open"
    assert due_at_open["status"] == "open"
    assert due_at_blocked["status"] == "blocked"
    assert done_admin["status"] == "done"
    assert "completed_at" in done_admin
    assert stale_project["status"] == "in_progress"
    assert overdue_person["next_contact"] < today
    assert done_idea["status"] == "done"
