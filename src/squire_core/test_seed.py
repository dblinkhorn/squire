from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from squire_core.canonical_store import CanonicalObject, write_canonical_object


@dataclass(frozen=True)
class SeedStats:
    admin_count: int
    projects_count: int
    people_count: int
    ideas_count: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dt_iso(value: datetime) -> str:
    return _as_utc(value).replace(microsecond=0).isoformat()


def _date_iso(value: date) -> str:
    return value.isoformat()


def _under_tmp(path: Path) -> bool:
    rendered = path.as_posix()
    return rendered == "/tmp" or rendered.startswith("/tmp/")


def _has_squire_test_segment(path: Path) -> bool:
    return any("squire-test" in segment.lower() for segment in path.parts)


def ensure_test_safe_archive_root(archive_root: str | Path) -> Path:
    root = Path(archive_root).expanduser()
    if not root.is_absolute():
        raise ValueError("archive_root must be absolute in test mode.")

    try:
        resolved = root.resolve()
    except OSError as exc:
        raise ValueError(f"archive_root failed to resolve: {root}") from exc

    if not (_under_tmp(root) or _has_squire_test_segment(root) or _has_squire_test_segment(resolved)):
        raise ValueError("archive_root is not test-safe; use /tmp or a path containing 'squire-test'.")
    return resolved


def seed_test_canonical_objects(
    *,
    objects_root: str | Path,
    schema_path: str | Path,
    now: datetime | None = None,
) -> SeedStats:
    reference = _as_utc(now or datetime.now(timezone.utc)).replace(microsecond=0)
    today = reference.date()
    earlier = reference - timedelta(days=21)
    recent = reference - timedelta(days=1)
    old = reference - timedelta(days=35)
    seed_source_ids = ["R_TEST_SEED"]
    root = Path(objects_root)

    def _write(frontmatter: dict[str, object], body: str) -> None:
        write_canonical_object(
            canonical=CanonicalObject(frontmatter=frontmatter, body=body),
            objects_root=root,
            schema_path=schema_path,
        )

    admin_objects = [
        {
            "id": "TEST_ADMIN_OVERDUE_OPEN",
            "type": "admin",
            "title": "Pay quarterly taxes",
            "status": "open",
            "next_action": "Submit Q1 estimated taxes",
            "due_date": _date_iso(today - timedelta(days=2)),
            "priority": "high",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference - timedelta(days=2)),
            "archived": False,
            "tags": ["seed-test", "finance"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_ADMIN_DUE_TODAY_OPEN",
            "type": "admin",
            "title": "Call internet provider",
            "status": "open",
            "next_action": "Ask about billing issue",
            "due_date": _date_iso(today),
            "priority": "normal",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference),
            "archived": False,
            "tags": ["seed-test"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_ADMIN_DUE_SOON_OPEN",
            "type": "admin",
            "title": "Book annual physical",
            "status": "open",
            "next_action": "Confirm appointment availability",
            "due_date": _date_iso(today + timedelta(days=1)),
            "priority": "normal",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference),
            "archived": False,
            "tags": ["seed-test", "health"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_ADMIN_BLOCKED",
            "type": "admin",
            "title": "Finalize contractor agreement",
            "status": "blocked",
            "next_action": "Wait for legal review",
            "blocked_reason": "Legal team review pending",
            "priority": "high",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference - timedelta(days=3)),
            "archived": False,
            "tags": ["seed-test", "blocked"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_ADMIN_DUE_AT_OPEN",
            "type": "admin",
            "title": "Join partner onboarding call",
            "status": "open",
            "next_action": "Open call agenda and take notes",
            "due_at": _dt_iso(reference + timedelta(hours=4)),
            "priority": "high",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference),
            "archived": False,
            "tags": ["seed-test", "timed"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_ADMIN_DUE_AT_BLOCKED",
            "type": "admin",
            "title": "Send signed contract packet",
            "status": "blocked",
            "next_action": "Confirm legal sign-off and send packet",
            "due_at": _dt_iso(reference + timedelta(hours=20)),
            "blocked_reason": "Awaiting final legal approval",
            "priority": "normal",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference - timedelta(hours=6)),
            "archived": False,
            "tags": ["seed-test", "timed", "blocked"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_ADMIN_DONE",
            "type": "admin",
            "title": "Renew passport",
            "status": "done",
            "next_action": "Store renewal receipt",
            "completed_at": _dt_iso(recent),
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(recent),
            "archived": False,
            "tags": ["seed-test", "done"],
            "source_event_ids": seed_source_ids,
        },
    ]

    project_objects = [
        {
            "id": "TEST_PROJECT_BLOCKED",
            "type": "projects",
            "title": "Home office refresh",
            "status": "blocked",
            "next_action": "Pick final monitor",
            "goal": "Improve daily work setup",
            "blocked_reason": "Waiting for reimbursement approval",
            "created_at": _dt_iso(old),
            "updated_at": _dt_iso(reference - timedelta(days=5)),
            "archived": False,
            "tags": ["seed-test", "blocked"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_PROJECT_STALE",
            "type": "projects",
            "title": "Personal website refresh",
            "status": "in_progress",
            "next_action": "Draft new homepage copy",
            "goal": "Ship an updated portfolio",
            "created_at": _dt_iso(old),
            "updated_at": _dt_iso(reference - timedelta(days=30)),
            "archived": False,
            "tags": ["seed-test", "stale"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_PROJECT_COMPLETED",
            "type": "projects",
            "title": "Tax document organizer",
            "status": "completed",
            "next_action": "Archive records",
            "goal": "Centralize yearly tax docs",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(recent),
            "archived": False,
            "tags": ["seed-test", "done"],
            "source_event_ids": seed_source_ids,
        },
    ]

    people_objects = [
        {
            "id": "TEST_PERSON_OVERDUE",
            "type": "people",
            "title": "Alex Chen",
            "name": "Alex Chen",
            "context": "Former teammate",
            "follow_ups": "Discuss meetup plans",
            "last_contacted": _date_iso(today - timedelta(days=30)),
            "next_contact": _date_iso(today - timedelta(days=3)),
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference - timedelta(days=10)),
            "archived": False,
            "tags": ["seed-test"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_PERSON_DUE_TODAY",
            "type": "people",
            "title": "Taylor Park",
            "name": "Taylor Park",
            "context": "Mentor",
            "follow_ups": "Send project update",
            "last_contacted": _date_iso(today - timedelta(days=14)),
            "next_contact": _date_iso(today),
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference - timedelta(days=1)),
            "archived": False,
            "tags": ["seed-test"],
            "source_event_ids": seed_source_ids,
        },
    ]

    idea_objects = [
        {
            "id": "TEST_IDEA_ACTIVE_RECENT",
            "type": "ideas",
            "title": "Weekly planning template",
            "one_liner": "Template to streamline weekly planning",
            "status": "active",
            "next_step": "Draft first template version",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(reference - timedelta(hours=10)),
            "archived": False,
            "tags": ["seed-test"],
            "source_event_ids": seed_source_ids,
        },
        {
            "id": "TEST_IDEA_DONE_RECENT",
            "type": "ideas",
            "title": "Desk cable labels",
            "one_liner": "Color-coded cable labels for desk setup",
            "status": "done",
            "next_step": "Share template with team",
            "created_at": _dt_iso(earlier),
            "updated_at": _dt_iso(recent),
            "archived": False,
            "tags": ["seed-test", "done"],
            "source_event_ids": seed_source_ids,
        },
    ]

    for row in admin_objects:
        _write(row, body=str(row["next_action"]))
    for row in project_objects:
        _write(row, body=str(row["next_action"]))
    for row in people_objects:
        _write(row, body=str(row["follow_ups"]))
    for row in idea_objects:
        _write(row, body=str(row["one_liner"]))

    return SeedStats(
        admin_count=len(admin_objects),
        projects_count=len(project_objects),
        people_count=len(people_objects),
        ideas_count=len(idea_objects),
    )
