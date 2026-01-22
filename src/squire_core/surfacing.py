from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Iterable

from squire_core.canonical_store import load_frontmatter
from squire_core.timezone_utils import resolve_timezone


@dataclass(frozen=True)
class DigestSection:
    title: str
    lines: list[str]


@dataclass(frozen=True)
class DailyDigest:
    generated_at: datetime
    sections: list[DigestSection]

    def render(self) -> str:
        header = f"Daily digest — {self.generated_at:%Y-%m-%d} ({self.generated_at:%A})"
        lines = [header]
        for section in self.sections:
            lines.append("")
            lines.append(section.title)
            if section.lines:
                for line in section.lines:
                    lines.append(f"- {line}")
            else:
                lines.append("- None")
        return "\n".join(lines)


@dataclass(frozen=True)
class SurfacingConfig:
    admin_due_soon_days: int
    admin_open_limit: int
    projects_stale_days: int
    ideas_weekly_review: bool
    people_next_contact_days: int


@dataclass(frozen=True)
class CanonicalItem:
    object_id: str
    object_type: str
    title: str
    frontmatter: dict[str, Any]


@dataclass(frozen=True)
class AdminEntry:
    object_id: str
    title: str
    next_action: str
    status: str
    due_at: datetime | None
    due_date: date | None
    priority: str | None
    updated_at: datetime | None
    blocked_reason: str | None


@dataclass(frozen=True)
class ProjectEntry:
    object_id: str
    title: str
    status: str
    next_action: str
    updated_at: datetime | None
    blocked_reason: str | None


@dataclass(frozen=True)
class PeopleEntry:
    object_id: str
    name: str
    next_contact: date | None
    updated_at: datetime | None


_DEFAULT_SURFACING_CONFIG = {
    "admin": {"due_soon_days": 1, "include_open_limit": 5},
    "projects": {"stale_days": 14},
    "ideas": {"weekly_review": True},
    "people": {"next_contact_days": 0},
}


def load_surfacing_config(config: dict[str, Any]) -> SurfacingConfig:
    raw = config.get("surfacing")
    if not isinstance(raw, dict):
        raw = {}

    def _get_int(path: tuple[str, str], fallback: int) -> int:
        section, key = path
        section_data = raw.get(section)
        if not isinstance(section_data, dict):
            section_data = {}
        value = section_data.get(key, fallback)
        if isinstance(value, (int, float)):
            return int(value)
        return int(fallback)

    def _get_bool(path: tuple[str, str], fallback: bool) -> bool:
        section, key = path
        section_data = raw.get(section)
        if not isinstance(section_data, dict):
            section_data = {}
        value = section_data.get(key, fallback)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(fallback)

    return SurfacingConfig(
        admin_due_soon_days=_get_int(("admin", "due_soon_days"), _DEFAULT_SURFACING_CONFIG["admin"]["due_soon_days"]),
        admin_open_limit=_get_int(("admin", "include_open_limit"), _DEFAULT_SURFACING_CONFIG["admin"]["include_open_limit"]),
        projects_stale_days=_get_int(("projects", "stale_days"), _DEFAULT_SURFACING_CONFIG["projects"]["stale_days"]),
        ideas_weekly_review=_get_bool(("ideas", "weekly_review"), _DEFAULT_SURFACING_CONFIG["ideas"]["weekly_review"]),
        people_next_contact_days=_get_int(
            ("people", "next_contact_days"), _DEFAULT_SURFACING_CONFIG["people"]["next_contact_days"]
        ),
    )


def _coerce_datetime(value: Any, tz: tzinfo) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _min_datetime(tz: tzinfo) -> datetime:
    return datetime.min.replace(tzinfo=tz)


def _is_archived(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _load_items(objects_root: str | Path) -> list[CanonicalItem]:
    root = Path(objects_root)
    if not root.exists():
        return []
    items: list[CanonicalItem] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        object_id = frontmatter.get("id")
        object_type = frontmatter.get("type")
        title = frontmatter.get("title")
        if not isinstance(object_id, str) or not isinstance(object_type, str) or not isinstance(title, str):
            continue
        items.append(
            CanonicalItem(
                object_id=object_id,
                object_type=object_type,
                title=title,
                frontmatter=frontmatter,
            )
        )
    return items


def _admin_entries(items: Iterable[CanonicalItem], tz: tzinfo) -> list[AdminEntry]:
    entries: list[AdminEntry] = []
    for item in items:
        if item.object_type != "admin":
            continue
        if _is_archived(item.frontmatter.get("archived")):
            continue
        status = str(item.frontmatter.get("status") or "open")
        if status == "done":
            continue
        due_at = _coerce_datetime(item.frontmatter.get("due_at"), tz)
        due_date = _coerce_date(item.frontmatter.get("due_date"))
        if due_at:
            due_date = due_at.date()
        updated_at = _coerce_datetime(item.frontmatter.get("updated_at"), tz)
        entries.append(
            AdminEntry(
                object_id=item.object_id,
                title=item.title,
                next_action=str(item.frontmatter.get("next_action") or item.title),
                status=status,
                due_at=due_at,
                due_date=due_date,
                priority=item.frontmatter.get("priority"),
                updated_at=updated_at,
                blocked_reason=item.frontmatter.get("blocked_reason"),
            )
        )
    return entries


def _project_entries(items: Iterable[CanonicalItem], tz: tzinfo) -> list[ProjectEntry]:
    entries: list[ProjectEntry] = []
    for item in items:
        if item.object_type != "projects":
            continue
        if _is_archived(item.frontmatter.get("archived")):
            continue
        status = str(item.frontmatter.get("status") or "")
        updated_at = _coerce_datetime(item.frontmatter.get("updated_at"), tz)
        entries.append(
            ProjectEntry(
                object_id=item.object_id,
                title=item.title,
                status=status,
                next_action=str(item.frontmatter.get("next_action") or ""),
                updated_at=updated_at,
                blocked_reason=item.frontmatter.get("blocked_reason"),
            )
        )
    return entries


def _people_entries(items: Iterable[CanonicalItem], tz: tzinfo) -> list[PeopleEntry]:
    entries: list[PeopleEntry] = []
    for item in items:
        if item.object_type != "people":
            continue
        if _is_archived(item.frontmatter.get("archived")):
            continue
        next_contact = _coerce_date(item.frontmatter.get("next_contact"))
        updated_at = _coerce_datetime(item.frontmatter.get("updated_at"), tz)
        entries.append(
            PeopleEntry(
                object_id=item.object_id,
                name=str(item.frontmatter.get("name") or item.title),
                next_contact=next_contact,
                updated_at=updated_at,
            )
        )
    return entries


def _priority_rank(priority: str | None) -> int:
    if not isinstance(priority, str):
        return 1
    normalized = priority.strip().lower()
    if normalized == "high":
        return 0
    if normalized == "low":
        return 2
    return 1


def _due_bucket(entry: AdminEntry, now: datetime, due_soon_days: int) -> str | None:
    if not entry.due_date:
        return None
    today = now.date()
    due_date = entry.due_date
    if entry.due_at and entry.due_at < now:
        return "overdue"
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    if due_soon_days > 0 and due_date <= today + timedelta(days=due_soon_days):
        return "soon"
    return None


def _format_due(entry: AdminEntry) -> str:
    if entry.due_at:
        return entry.due_at.strftime("%Y-%m-%d %H:%M")
    if entry.due_date:
        return entry.due_date.isoformat()
    return "unscheduled"


def _format_admin_due_lines(due_items: list[tuple[str, AdminEntry]]) -> list[str]:
    lines = []
    for bucket, entry in due_items:
        lines.append(f"[{bucket}] {entry.title} ({entry.object_id}) — due {_format_due(entry)}")
    return lines


def _format_open_admin_lines(entries: list[AdminEntry], limit: int) -> list[str]:
    lines: list[str] = []
    for entry in entries[:limit]:
        priority = entry.priority
        suffix = f" (priority {priority})" if isinstance(priority, str) else ""
        lines.append(f"{entry.title} ({entry.object_id}) — next: {entry.next_action}{suffix}")
    return lines


def _format_people_lines(entries: list[PeopleEntry], cutoff: date, tz: tzinfo) -> list[str]:
    due_entries = [entry for entry in entries if entry.next_contact and entry.next_contact <= cutoff]
    min_dt = _min_datetime(tz)
    due_entries.sort(key=lambda entry: (entry.next_contact, entry.updated_at or min_dt))
    return [
        f"{entry.name} ({entry.object_id}) — next contact {entry.next_contact.isoformat()}"
        for entry in due_entries
    ]


def _find_stuck_item(
    projects: list[ProjectEntry],
    admin_items: list[AdminEntry],
    now: datetime,
    stale_days: int,
) -> str | None:
    min_dt = _min_datetime(now.tzinfo)
    blocked_projects = [item for item in projects if item.status == "blocked"]
    blocked_projects.sort(key=lambda item: item.updated_at or min_dt)
    if blocked_projects:
        item = blocked_projects[0]
        reason = f": {item.blocked_reason}" if item.blocked_reason else ""
        return f"{item.title} ({item.object_id}) — blocked{reason}"

    stale_cutoff = now.date() - timedelta(days=stale_days)
    stale_projects = [
        item
        for item in projects
        if item.status in {"planning", "in_progress"} and item.updated_at and item.updated_at.date() <= stale_cutoff
    ]
    stale_projects.sort(key=lambda item: item.updated_at or min_dt)
    if stale_projects:
        item = stale_projects[0]
        updated = item.updated_at.date().isoformat() if item.updated_at else "unknown"
        return f"{item.title} ({item.object_id}) — stale (last updated {updated})"

    blocked_admin = [item for item in admin_items if item.status == "blocked"]
    blocked_admin.sort(key=lambda item: item.updated_at or min_dt)
    if blocked_admin:
        item = blocked_admin[0]
        reason = f": {item.blocked_reason}" if item.blocked_reason else ""
        return f"{item.title} ({item.object_id}) — blocked{reason}"

    return None


def _build_suggestions(
    due_items: list[tuple[str, AdminEntry]],
    open_items: list[AdminEntry],
    projects: list[ProjectEntry],
    limit: int,
    tz: tzinfo,
) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()

    def _add(line: str, object_id: str) -> None:
        if object_id in seen:
            return
        seen.add(object_id)
        suggestions.append(line)

    for _, entry in due_items:
        if len(suggestions) >= limit:
            return suggestions
        _add(f"{entry.next_action} ({entry.object_id})", entry.object_id)

    for entry in open_items:
        if len(suggestions) >= limit:
            return suggestions
        _add(f"{entry.next_action} ({entry.object_id})", entry.object_id)

    active_projects = [
        item for item in projects if item.status in {"planning", "in_progress", "blocked"} and item.next_action
    ]
    min_dt = _min_datetime(tz)
    active_projects.sort(key=lambda item: item.updated_at or min_dt)
    for entry in active_projects:
        if len(suggestions) >= limit:
            return suggestions
        _add(f"{entry.title} — {entry.next_action} ({entry.object_id})", entry.object_id)

    return suggestions


def build_daily_digest(
    objects_root: str | Path,
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> DailyDigest:
    tz = resolve_timezone(config.get("timezone"))
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    surfacing = load_surfacing_config(config)
    items = _load_items(objects_root)
    admin_items = _admin_entries(items, tz)
    project_items = _project_entries(items, tz)
    people_items = _people_entries(items, tz)

    due_items: list[tuple[str, AdminEntry]] = []
    open_items: list[AdminEntry] = []
    for entry in admin_items:
        bucket = _due_bucket(entry, now, surfacing.admin_due_soon_days)
        if bucket:
            due_items.append((bucket, entry))
        else:
            open_items.append(entry)

    bucket_rank = {"overdue": 0, "today": 1, "soon": 2}
    due_items.sort(
        key=lambda item: (
            bucket_rank.get(item[0], 3),
            item[1].due_at or datetime.combine(item[1].due_date or now.date(), time.min, tzinfo=tz),
            _priority_rank(item[1].priority),
        )
    )

    min_dt = _min_datetime(tz)
    open_items.sort(key=lambda item: (_priority_rank(item.priority), item.updated_at or min_dt))

    due_lines = _format_admin_due_lines(due_items)
    open_lines = _format_open_admin_lines(open_items, surfacing.admin_open_limit)
    stuck_line = _find_stuck_item(project_items, admin_items, now, surfacing.projects_stale_days)
    suggestion_lines = _build_suggestions(due_items, open_items, project_items, limit=3, tz=tz)

    people_cutoff = now.date() + timedelta(days=surfacing.people_next_contact_days)
    people_lines = _format_people_lines(people_items, people_cutoff, tz)

    sections = [
        DigestSection(
            title="Admin due/overdue",
            lines=due_lines or ["No admin items due today or overdue."],
        )
    ]
    if open_lines:
        sections.append(DigestSection(title="Open admin", lines=open_lines))
    if stuck_line:
        sections.append(DigestSection(title="Stuck item", lines=[stuck_line]))
    sections.append(
        DigestSection(
            title="Suggested next actions",
            lines=suggestion_lines or ["No suggested next actions."],
        )
    )
    if people_lines:
        sections.append(DigestSection(title="People to follow up", lines=people_lines))

    return DailyDigest(generated_at=now, sections=sections)
