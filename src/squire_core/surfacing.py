from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Iterable

from squire_core.canonical_store import find_object_path, load_frontmatter
from squire_core.indexer import find_candidates
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
        header = f"Daily digest - {self.generated_at:%Y-%m-%d} ({self.generated_at:%A})"
        return _render_sectioned_message(header, self.sections)


@dataclass(frozen=True)
class WeeklyReview:
    generated_at: datetime
    sections: list[DigestSection]

    def render(self) -> str:
        header = f"Weekly review - {self.generated_at:%Y-%m-%d} ({self.generated_at:%A})"
        return _render_sectioned_message(header, self.sections)


@dataclass(frozen=True)
class SurfacingConfig:
    admin_due_soon_days: int
    projects_stale_days: int
    projects_blocked_limit: int
    ideas_weekly_review: bool
    people_next_contact_days: int
    include_ids: bool
    pull_default_recent_limit: int
    pull_default_find_limit: int
    pull_cursor_ttl_minutes: int


@dataclass(frozen=True)
class CanonicalItem:
    object_id: str
    object_type: str
    title: str
    frontmatter: dict[str, Any]
    path: Path


@dataclass(frozen=True)
class AdminEntry:
    object_id: str
    title: str
    next_action: str
    status: str
    due_at: datetime | None
    due_date: date | None
    priority: str | None
    created_at: datetime | None
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


@dataclass(frozen=True)
class SurfacedList:
    lines: list[str]
    object_ids: list[str]


_DEFAULT_SURFACING_CONFIG = {
    "admin": {"due_soon_days": 1},
    "projects": {"stale_days": 14, "blocked_limit": 3},
    "ideas": {"weekly_review": True},
    "people": {"next_contact_days": 0},
    "output": {"include_ids": False},
    "pull": {"default_recent_limit": 10, "default_find_limit": 5, "cursor_ttl_minutes": 45},
}

_WEEKLY_RECENT_DAYS = 7
_WEEKLY_RECENT_LIMIT = 10
_WEEKLY_UNSCHEDULED_LIMIT = 10
_WEEKLY_PEOPLE_OVERDUE_LIMIT = 10
_WEEKLY_IDEAS_LIMIT = 5


def load_surfacing_config(config: dict[str, Any]) -> SurfacingConfig:
    raw = config.get("surfacing")
    if not isinstance(raw, dict):
        raw = {}

    def _section(name: str) -> dict[str, Any]:
        value = raw.get(name)
        if isinstance(value, dict):
            return value
        return {}

    def _get_int(section_name: str, key: str, fallback: int) -> int:
        value = _section(section_name).get(key, fallback)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return int(fallback)

    def _get_bool(section_name: str, key: str, fallback: bool) -> bool:
        value = _section(section_name).get(key, fallback)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(fallback)

    return SurfacingConfig(
        admin_due_soon_days=_get_int("admin", "due_soon_days", _DEFAULT_SURFACING_CONFIG["admin"]["due_soon_days"]),
        projects_stale_days=_get_int("projects", "stale_days", _DEFAULT_SURFACING_CONFIG["projects"]["stale_days"]),
        projects_blocked_limit=_get_int(
            "projects", "blocked_limit", _DEFAULT_SURFACING_CONFIG["projects"]["blocked_limit"]
        ),
        ideas_weekly_review=_get_bool("ideas", "weekly_review", _DEFAULT_SURFACING_CONFIG["ideas"]["weekly_review"]),
        people_next_contact_days=_get_int(
            "people", "next_contact_days", _DEFAULT_SURFACING_CONFIG["people"]["next_contact_days"]
        ),
        include_ids=_get_bool("output", "include_ids", _DEFAULT_SURFACING_CONFIG["output"]["include_ids"]),
        pull_default_recent_limit=_get_int(
            "pull", "default_recent_limit", _DEFAULT_SURFACING_CONFIG["pull"]["default_recent_limit"]
        ),
        pull_default_find_limit=_get_int("pull", "default_find_limit", _DEFAULT_SURFACING_CONFIG["pull"]["default_find_limit"]),
        pull_cursor_ttl_minutes=_get_int("pull", "cursor_ttl_minutes", _DEFAULT_SURFACING_CONFIG["pull"]["cursor_ttl_minutes"]),
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


def _render_sectioned_message(header: str, sections: list[DigestSection]) -> str:
    lines = [header]
    for section in sections:
        lines.append("")
        lines.append(section.title)
        if section.lines:
            for line in section.lines:
                lines.append(f"- {line}")
        else:
            lines.append("- None")
    return "\n".join(lines)


def _is_archived(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _safe_int(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return fallback


def _clamp_limit(limit: int | None, fallback: int, *, minimum: int = 1, maximum: int = 50) -> int:
    resolved = fallback if limit is None else _safe_int(limit, fallback)
    if resolved < minimum:
        return minimum
    if resolved > maximum:
        return maximum
    return resolved


def _format_title(title: str, object_id: str, include_ids: bool) -> str:
    if include_ids:
        return f"{title} ({object_id})"
    return title


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
                path=path,
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
        created_at = _coerce_datetime(item.frontmatter.get("created_at"), tz)
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
                created_at=created_at,
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


def _format_admin_due_lines(entries: list[AdminEntry], include_ids: bool) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        title = _format_title(entry.title, entry.object_id, include_ids)
        lines.append(f"{title} - due {_format_due(entry)}")
    return lines


def _format_people_lines(entries: list[PeopleEntry], cutoff: date, tz: tzinfo, include_ids: bool) -> list[str]:
    due_entries = [entry for entry in entries if entry.next_contact and entry.next_contact <= cutoff]
    min_dt = _min_datetime(tz)
    due_entries.sort(key=lambda entry: (entry.next_contact, entry.updated_at or min_dt))
    lines: list[str] = []
    for entry in due_entries:
        title = _format_title(entry.name, entry.object_id, include_ids)
        lines.append(f"{title} - next contact {entry.next_contact.isoformat()}")
    return lines


def _build_project_attention_lines(
    projects: list[ProjectEntry],
    *,
    now: datetime,
    stale_days: int,
    limit: int,
    include_ids: bool,
) -> list[str]:
    min_dt = _min_datetime(now.tzinfo)
    blocked = [item for item in projects if item.status == "blocked"]
    blocked.sort(key=lambda item: item.updated_at or min_dt)

    stale_cutoff = now.date() - timedelta(days=stale_days)
    stale = [
        item
        for item in projects
        if item.status in {"planning", "in_progress"} and item.updated_at and item.updated_at.date() <= stale_cutoff
    ]
    stale.sort(key=lambda item: item.updated_at or min_dt)

    lines: list[str] = []
    seen: set[str] = set()

    def _add(line: str, object_id: str) -> None:
        if object_id in seen:
            return
        if len(lines) >= limit:
            return
        seen.add(object_id)
        lines.append(line)

    for item in blocked:
        title = _format_title(item.title, item.object_id, include_ids)
        reason = f": {item.blocked_reason}" if item.blocked_reason else ""
        _add(f"{title} - blocked{reason}", item.object_id)

    for item in stale:
        if len(lines) >= limit:
            break
        title = _format_title(item.title, item.object_id, include_ids)
        updated = item.updated_at.date().isoformat() if item.updated_at else "unknown"
        _add(f"{title} - stale (last updated {updated})", item.object_id)

    return lines


def _build_recently_changed_lines(
    items: list[CanonicalItem],
    *,
    now: datetime,
    tz: tzinfo,
    include_ids: bool,
    days: int = _WEEKLY_RECENT_DAYS,
    limit: int = _WEEKLY_RECENT_LIMIT,
) -> list[str]:
    cutoff = now - timedelta(days=max(0, days))
    candidates = [
        item
        for item in items
        if not _is_archived(item.frontmatter.get("archived")) and _object_updated_at(item, tz) >= cutoff
    ]
    candidates.sort(key=lambda item: _object_updated_at(item, tz), reverse=True)

    lines: list[str] = []
    for item in candidates[:limit]:
        title = _format_title(item.title, item.object_id, include_ids)
        updated = _object_updated_at(item, tz)
        lines.append(f"{title} - {_render_type_label(item.object_type)}, updated {updated:%Y-%m-%d}")
    return lines


def _build_open_admin_without_due_lines(
    entries: list[AdminEntry],
    *,
    tz: tzinfo,
    include_ids: bool,
    limit: int = _WEEKLY_UNSCHEDULED_LIMIT,
) -> list[str]:
    min_dt = _min_datetime(tz)
    unscheduled = [
        entry
        for entry in entries
        if entry.status == "open" and entry.due_at is None and entry.due_date is None
    ]
    unscheduled.sort(key=lambda entry: entry.created_at or entry.updated_at or min_dt)

    lines: list[str] = []
    for entry in unscheduled[:limit]:
        title = _format_title(entry.title, entry.object_id, include_ids)
        lines.append(f"{title} - open, unscheduled")
    return lines


def _build_overdue_people_lines(
    entries: list[PeopleEntry],
    *,
    today: date,
    tz: tzinfo,
    include_ids: bool,
    limit: int = _WEEKLY_PEOPLE_OVERDUE_LIMIT,
) -> list[str]:
    min_dt = _min_datetime(tz)
    overdue = [entry for entry in entries if entry.next_contact and entry.next_contact < today]
    overdue.sort(key=lambda entry: (entry.next_contact, entry.updated_at or min_dt))

    lines: list[str] = []
    for entry in overdue[:limit]:
        title = _format_title(entry.name, entry.object_id, include_ids)
        lines.append(f"{title} - next contact {entry.next_contact.isoformat()}")
    return lines


def _build_recent_idea_lines(
    items: list[CanonicalItem],
    *,
    now: datetime,
    tz: tzinfo,
    include_ids: bool,
    days: int = _WEEKLY_RECENT_DAYS,
    limit: int = _WEEKLY_IDEAS_LIMIT,
) -> list[str]:
    cutoff = now - timedelta(days=max(0, days))
    ideas = [
        item
        for item in items
        if item.object_type == "ideas"
        and not _is_archived(item.frontmatter.get("archived"))
        and _object_updated_at(item, tz) >= cutoff
    ]
    ideas.sort(key=lambda item: _object_updated_at(item, tz), reverse=True)

    lines: list[str] = []
    for item in ideas[:limit]:
        title = _format_title(item.title, item.object_id, include_ids)
        updated = _object_updated_at(item, tz)
        lines.append(f"{title} - updated {updated:%Y-%m-%d}")
    return lines


def _object_updated_at(item: CanonicalItem, tz: tzinfo) -> datetime:
    frontmatter = item.frontmatter
    updated = _coerce_datetime(frontmatter.get("updated_at"), tz)
    if updated:
        return updated
    created = _coerce_datetime(frontmatter.get("created_at"), tz)
    if created:
        return created
    return _min_datetime(tz)


def _render_type_label(object_type: str) -> str:
    mapping = {
        "people": "person",
        "projects": "project",
        "ideas": "idea",
        "admin": "admin",
    }
    return mapping.get(object_type, object_type)


def _render_list_row(index: int, item: CanonicalItem, *, include_ids: bool, tz: tzinfo) -> str:
    title = _format_title(item.title, item.object_id, include_ids)
    parts: list[str] = [_render_type_label(item.object_type)]

    status = item.frontmatter.get("status")
    if isinstance(status, str) and status.strip():
        parts.append(status.strip())

    due_at = item.frontmatter.get("due_at")
    due_date = item.frontmatter.get("due_date")
    if isinstance(due_at, str) and due_at.strip():
        parts.append(f"due {due_at.strip()}")
    elif isinstance(due_date, str) and due_date.strip():
        parts.append(f"due {due_date.strip()}")

    updated = _object_updated_at(item, tz)
    if updated > _min_datetime(tz):
        parts.append(f"updated {updated:%Y-%m-%d}")

    return f"{index}. {title} - {', '.join(parts)}"


def _render_find_row(
    index: int,
    item: CanonicalItem,
    snippet: str,
    *,
    include_ids: bool,
    tz: tzinfo,
) -> str:
    base = _render_list_row(index, item, include_ids=include_ids, tz=tz)
    cleaned_snippet = " ".join(snippet.split())
    if not cleaned_snippet:
        return base
    if len(cleaned_snippet) > 120:
        cleaned_snippet = f"{cleaned_snippet[:117].rstrip()}..."
    return f"{base} - {cleaned_snippet}"


def _load_body(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) == 3:
        return parts[2].strip()
    return content.strip()


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

    overdue: list[AdminEntry] = []
    today: list[AdminEntry] = []
    soon: list[AdminEntry] = []

    for entry in admin_items:
        bucket = _due_bucket(entry, now, surfacing.admin_due_soon_days)
        if bucket == "overdue":
            overdue.append(entry)
        elif bucket == "today":
            today.append(entry)
        elif bucket == "soon":
            soon.append(entry)

    def _sort_due(entries: list[AdminEntry]) -> list[AdminEntry]:
        return sorted(
            entries,
            key=lambda item: (
                item.due_at or datetime.combine(item.due_date or now.date(), time.min, tzinfo=tz),
                _priority_rank(item.priority),
            ),
        )

    overdue_lines = _format_admin_due_lines(_sort_due(overdue), surfacing.include_ids)
    today_lines = _format_admin_due_lines(_sort_due(today), surfacing.include_ids)
    soon_lines = _format_admin_due_lines(_sort_due(soon), surfacing.include_ids)
    project_lines = _build_project_attention_lines(
        project_items,
        now=now,
        stale_days=surfacing.projects_stale_days,
        limit=surfacing.projects_blocked_limit,
        include_ids=surfacing.include_ids,
    )

    people_cutoff = now.date() + timedelta(days=surfacing.people_next_contact_days)
    people_lines = _format_people_lines(people_items, people_cutoff, tz, surfacing.include_ids)

    sections = [
        DigestSection(title="Admin overdue", lines=overdue_lines),
        DigestSection(title="Admin due today", lines=today_lines),
        DigestSection(title="Admin due soon", lines=soon_lines),
        DigestSection(title="Projects needing attention", lines=project_lines),
        DigestSection(title="People to follow up", lines=people_lines),
    ]

    return DailyDigest(generated_at=now, sections=sections)


def build_weekly_review(
    objects_root: str | Path,
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> WeeklyReview:
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

    recently_changed_lines = _build_recently_changed_lines(
        items,
        now=now,
        tz=tz,
        include_ids=surfacing.include_ids,
    )
    unscheduled_admin_lines = _build_open_admin_without_due_lines(
        admin_items,
        tz=tz,
        include_ids=surfacing.include_ids,
    )
    project_lines = _build_project_attention_lines(
        project_items,
        now=now,
        stale_days=surfacing.projects_stale_days,
        limit=surfacing.projects_blocked_limit,
        include_ids=surfacing.include_ids,
    )
    overdue_people_lines = _build_overdue_people_lines(
        people_items,
        today=now.date(),
        tz=tz,
        include_ids=surfacing.include_ids,
    )

    sections = [
        DigestSection(title="Recently changed notes", lines=recently_changed_lines),
        DigestSection(title="Open admin without due dates", lines=unscheduled_admin_lines),
        DigestSection(title="Blocked or stale projects", lines=project_lines),
        DigestSection(title="People overdue for contact", lines=overdue_people_lines),
    ]
    if surfacing.ideas_weekly_review:
        sections.append(
            DigestSection(
                title="Ideas updated recently",
                lines=_build_recent_idea_lines(
                    items,
                    now=now,
                    tz=tz,
                    include_ids=surfacing.include_ids,
                ),
            )
        )

    return WeeklyReview(generated_at=now, sections=sections)


def build_recent_list(
    objects_root: str | Path,
    config: dict[str, Any],
    *,
    limit: int | None = None,
) -> SurfacedList:
    tz = resolve_timezone(config.get("timezone"))
    surfacing = load_surfacing_config(config)
    effective_limit = _clamp_limit(limit, surfacing.pull_default_recent_limit)

    items = [item for item in _load_items(objects_root) if not _is_archived(item.frontmatter.get("archived"))]
    items.sort(key=lambda item: _object_updated_at(item, tz), reverse=True)
    selected = items[:effective_limit]

    lines = [
        _render_list_row(index + 1, item, include_ids=surfacing.include_ids, tz=tz)
        for index, item in enumerate(selected)
    ]
    object_ids = [item.object_id for item in selected]
    return SurfacedList(lines=lines, object_ids=object_ids)


def build_find_list(
    objects_root: str | Path,
    index_db: str | Path,
    config: dict[str, Any],
    query: str,
    *,
    limit: int | None = None,
) -> SurfacedList:
    cleaned_query = query.strip()
    if not cleaned_query:
        return SurfacedList(lines=[], object_ids=[])

    tz = resolve_timezone(config.get("timezone"))
    surfacing = load_surfacing_config(config)
    effective_limit = _clamp_limit(limit, surfacing.pull_default_find_limit)

    candidates = find_candidates(
        index_db,
        cleaned_query,
        limit=effective_limit,
        score_threshold=0.0,
    )

    rows: list[str] = []
    object_ids: list[str] = []
    for candidate in candidates:
        path = find_object_path(objects_root, candidate.object_id)
        if not path:
            continue
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        if _is_archived(frontmatter.get("archived")):
            continue
        title = frontmatter.get("title")
        object_type = frontmatter.get("type")
        if not isinstance(title, str) or not isinstance(object_type, str):
            continue
        item = CanonicalItem(
            object_id=candidate.object_id,
            object_type=object_type,
            title=title,
            frontmatter=frontmatter,
            path=path,
        )
        object_ids.append(candidate.object_id)
        rows.append(
            _render_find_row(
                len(rows) + 1,
                item,
                candidate.snippet,
                include_ids=surfacing.include_ids,
                tz=tz,
            )
        )
        if len(rows) >= effective_limit:
            break

    return SurfacedList(lines=rows, object_ids=object_ids)


def build_item_detail(
    objects_root: str | Path,
    object_id: str,
    config: dict[str, Any],
) -> str | None:
    path = find_object_path(objects_root, object_id)
    if not path:
        return None
    try:
        frontmatter = load_frontmatter(path)
    except Exception:
        return None

    surfacing = load_surfacing_config(config)
    title = frontmatter.get("title")
    object_type = frontmatter.get("type")
    if not isinstance(title, str) or not isinstance(object_type, str):
        return None

    lines = [f"Title: {title}", f"Type: {_render_type_label(object_type)}"]
    if surfacing.include_ids:
        lines.append(f"ID: {object_id}")

    field_map = [
        ("status", "Status"),
        ("priority", "Priority"),
        ("due_at", "Due at"),
        ("due_date", "Due date"),
        ("next_action", "Next action"),
        ("next_contact", "Next contact"),
        ("updated_at", "Updated"),
    ]
    for key, label in field_map:
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{label}: {value.strip()}")

    body = _load_body(path)
    if body:
        body_lines = [line.strip() for line in body.splitlines() if line.strip()]
        if body_lines:
            lines.append("")
            lines.append("Notes:")
            for line in body_lines[:6]:
                lines.append(f"- {line}")

    return "\n".join(lines)
