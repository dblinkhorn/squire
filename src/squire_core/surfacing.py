from __future__ import annotations

from dataclasses import dataclass, field
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
    object_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DailyDigest:
    generated_at: datetime
    sections: list[DigestSection]

    def render(self) -> str:
        header_date = _format_human_date(self.generated_at.date(), self.generated_at.date(), include_relative=False)
        header = f"📌 **Daily digest** · {header_date}"
        return _render_sectioned_message(header, self.sections)


@dataclass(frozen=True)
class WeeklyReview:
    generated_at: datetime
    sections: list[DigestSection]

    def render(self) -> str:
        header_date = _format_human_date(self.generated_at.date(), self.generated_at.date(), include_relative=False)
        header = f"🗓️ **Weekly review** · {header_date}"
        return _render_sectioned_message(header, self.sections)


@dataclass(frozen=True)
class SurfacingConfig:
    admin_due_soon_days: int
    projects_stale_days: int
    projects_blocked_limit: int
    ideas_weekly_review: bool
    people_next_contact_days: int
    show_ids_daily_weekly: bool
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


@dataclass(frozen=True)
class DueTimeReminderEvent:
    object_id: str
    title: str
    due_at: datetime
    offset_minutes: int
    fire_at: datetime


_DEFAULT_SURFACING_CONFIG = {
    "admin": {"due_soon_days": 1},
    "projects": {"stale_days": 14, "blocked_limit": 3},
    "ideas": {"weekly_review": True},
    "people": {"next_contact_days": 0},
    "output": {"show_ids_daily_weekly": False},
    "pull": {"default_recent_limit": 10, "default_find_limit": 5, "cursor_ttl_minutes": 45},
}

_WEEKLY_RECENT_DAYS = 7
_WEEKLY_COMPLETED_LIMIT = 10
_WEEKLY_UNSCHEDULED_LIMIT = 10
_WEEKLY_PEOPLE_OVERDUE_LIMIT = 10
_WEEKLY_IDEAS_LIMIT = 5
_RELATIVE_DAY_WINDOW = 6
_SECTION_EMOJI = {
    "Admin overdue": "🔴",
    "Admin due today": "🟠",
    "Admin due soon": "🟡",
    "Projects needing attention": "🧱",
    "People to follow up": "🤝",
    "Completed this week": "✅",
    "Admin without due dates": "📂",
    "Blocked or stale projects": "🧱",
    "People overdue for contact": "🤝",
    "Ideas updated recently": "💡",
    "Active admin": "📂",
    "Active projects": "🧱",
    "Active people": "🤝",
    "Active ideas": "💡",
}
_SECTION_DIVIDER_WIDTH = {
    "Admin overdue": 15,
    "Admin due today": 16,
    "Admin due soon": 16,
    "Projects needing attention": 24,
    "People to follow up": 18,
    "Completed this week": 19,
    "Admin without due dates": 22,
    "Blocked or stale projects": 24,
    "People overdue for contact": 25,
    "Ideas updated recently": 21,
    "Active admin": 12,
    "Active projects": 15,
    "Active people": 13,
    "Active ideas": 12,
}
_ACTIVE_SECTION_ORDER = (
    ("admin", "Active admin"),
    ("projects", "Active projects"),
    ("people", "Active people"),
    ("ideas", "Active ideas"),
)


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

    output_section = _section("output")
    show_ids_raw = output_section.get("show_ids_daily_weekly", _DEFAULT_SURFACING_CONFIG["output"]["show_ids_daily_weekly"])
    if isinstance(show_ids_raw, bool):
        show_ids_daily_weekly = show_ids_raw
    elif isinstance(show_ids_raw, str):
        show_ids_daily_weekly = show_ids_raw.strip().lower() in {"1", "true", "yes", "y"}
    else:
        show_ids_daily_weekly = bool(_DEFAULT_SURFACING_CONFIG["output"]["show_ids_daily_weekly"])

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
        show_ids_daily_weekly=show_ids_daily_weekly,
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


def _render_sectioned_message(header: str, sections: list[DigestSection], *, summary: str | None = None) -> str:
    lines = [header]
    if summary:
        lines.append(summary)
    for section in sections:
        lines.append("")
        lines.append(_format_section_title(section.title))
        lines.append(_section_divider(section.title))
        if section.lines:
            for line in section.lines:
                if _is_numbered_row(line):
                    lines.append(line)
                else:
                    lines.append(f"• {line}")
        else:
            lines.append("• All clear")
    return "\n".join(lines)


def _is_numbered_row(line: str) -> bool:
    value = line.lstrip()
    if not value:
        return False
    first_token = value.split(" ", 1)[0]
    if not first_token.endswith("."):
        return False
    return first_token[:-1].isdigit()


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


def _format_section_title(title: str) -> str:
    emoji = _SECTION_EMOJI.get(title)
    if emoji:
        return f"{emoji} **{title}**"
    return f"**{title}**"


def _section_divider(title: str) -> str:
    width = _SECTION_DIVIDER_WIDTH.get(title, len(title))
    return "─" * width


def _relative_date_label(target: date, reference: date) -> str | None:
    delta = (target - reference).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if 1 < delta <= _RELATIVE_DAY_WINDOW:
        return f"in {delta} days"
    if -_RELATIVE_DAY_WINDOW <= delta < -1:
        return f"{abs(delta)} days ago"
    return None


def _format_human_date(target: date, reference: date, *, include_relative: bool = True) -> str:
    base = target.strftime("%a %b ") + str(target.day)
    if target.year != reference.year:
        base = f"{base}, {target.year}"
    if not include_relative:
        return base
    relative = _relative_date_label(target, reference)
    if relative:
        return f"{base} ({relative})"
    return base


def _format_human_time(target: datetime) -> str:
    hour = target.hour % 12
    if hour == 0:
        hour = 12
    suffix = "AM" if target.hour < 12 else "PM"
    return f"{hour}:{target.minute:02d} {suffix}"


def _format_compact_elapsed_duration(target: datetime, reference: datetime) -> str | None:
    delta_seconds = int((reference - target).total_seconds())
    if delta_seconds <= 0 or delta_seconds >= 24 * 60 * 60:
        return None
    total_minutes = delta_seconds // 60
    if total_minutes <= 0:
        return None
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def _format_human_datetime(
    target: datetime,
    reference: date,
    *,
    include_relative: bool = True,
    reference_datetime: datetime | None = None,
) -> str:
    rendered = f"{_format_human_date(target.date(), reference, include_relative=False)} at {_format_human_time(target)}"
    if not include_relative:
        return rendered
    if reference_datetime is not None:
        compact_elapsed = _format_compact_elapsed_duration(target, reference_datetime)
        if compact_elapsed:
            return f"{rendered} ({compact_elapsed})"
    relative = _relative_date_label(target.date(), reference)
    if relative:
        return f"{rendered} ({relative})"
    return rendered


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


def _format_due(
    entry: AdminEntry,
    *,
    reference_date: date,
    include_relative: bool = True,
    reference_datetime: datetime | None = None,
) -> str:
    if entry.due_at:
        return _format_human_datetime(
            entry.due_at,
            reference_date,
            include_relative=include_relative,
            reference_datetime=reference_datetime,
        )
    if entry.due_date:
        return _format_human_date(entry.due_date, reference_date, include_relative=include_relative)
    return "No due date"


def _format_admin_due_lines(
    entries: list[AdminEntry],
    include_ids: bool,
    *,
    reference_date: date,
    include_relative: bool = True,
    omit_date_for_same_day: bool = False,
    reference_datetime: datetime | None = None,
) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        title = _format_title(entry.title, entry.object_id, include_ids)
        if omit_date_for_same_day and entry.due_date == reference_date:
            if entry.due_at:
                lines.append(f"{title} - due {_format_human_time(entry.due_at)}")
            else:
                lines.append(title)
            continue
        lines.append(
            f"{title} - due {_format_due(entry, reference_date=reference_date, include_relative=include_relative, reference_datetime=reference_datetime)}"
        )
    return lines


def _format_people_lines(
    entries: list[PeopleEntry],
    cutoff: date,
    tz: tzinfo,
    include_ids: bool,
    *,
    reference_date: date,
) -> tuple[list[str], list[str]]:
    due_entries = [entry for entry in entries if entry.next_contact and entry.next_contact <= cutoff]
    min_dt = _min_datetime(tz)
    due_entries.sort(key=lambda entry: (entry.next_contact, entry.updated_at or min_dt))
    lines: list[str] = []
    object_ids: list[str] = []
    for entry in due_entries:
        title = _format_title(entry.name, entry.object_id, include_ids)
        lines.append(f"{title} - next contact {_format_human_date(entry.next_contact, reference_date)}")
        object_ids.append(entry.object_id)
    return lines, object_ids


def _build_project_attention_lines(
    projects: list[ProjectEntry],
    *,
    now: datetime,
    stale_days: int,
    limit: int,
    include_ids: bool,
) -> tuple[list[str], list[str]]:
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
    object_ids: list[str] = []
    seen: set[str] = set()

    def _add(line: str, object_id: str) -> None:
        if object_id in seen:
            return
        if len(lines) >= limit:
            return
        seen.add(object_id)
        lines.append(line)
        object_ids.append(object_id)

    for item in blocked:
        title = _format_title(item.title, item.object_id, include_ids)
        reason = f": {item.blocked_reason}" if item.blocked_reason else ""
        _add(f"{title} - blocked{reason}", item.object_id)

    for item in stale:
        if len(lines) >= limit:
            break
        title = _format_title(item.title, item.object_id, include_ids)
        updated = _format_human_date(item.updated_at.date(), now.date(), include_relative=False) if item.updated_at else "unknown"
        _add(f"{title} - stale (last updated {updated})", item.object_id)

    return lines, object_ids


def _build_completed_this_week_lines(
    items: list[CanonicalItem],
    *,
    now: datetime,
    tz: tzinfo,
    include_ids: bool,
    days: int = _WEEKLY_RECENT_DAYS,
    limit: int = _WEEKLY_COMPLETED_LIMIT,
) -> tuple[list[str], list[str]]:
    cutoff = now - timedelta(days=max(0, days))
    rows: list[tuple[datetime, str, str]] = []
    for item in items:
        frontmatter = item.frontmatter
        status = str(frontmatter.get("status") or "").strip().lower()
        archived = _is_archived(frontmatter.get("archived"))
        completed_at: datetime | None = None
        state_label: str | None = None

        if archived:
            completed_at = _object_updated_at(item, tz)
            state_label = "archived"
        elif item.object_type == "admin" and status == "done":
            completed_at = _coerce_datetime(frontmatter.get("completed_at"), tz) or _object_updated_at(item, tz)
            state_label = "done"
        elif item.object_type == "projects" and status == "completed":
            completed_at = _object_updated_at(item, tz)
            state_label = "completed"
        elif item.object_type == "ideas" and status == "done":
            completed_at = _object_updated_at(item, tz)
            state_label = "done"

        if completed_at is None or state_label is None:
            continue
        if completed_at < cutoff:
            continue
        title = _format_title(item.title, item.object_id, include_ids)
        line = (
            f"{title} - {_render_type_label(item.object_type)}, "
            f"{state_label} {_format_human_date(completed_at.date(), now.date())}"
        )
        rows.append((completed_at, line, item.object_id))

    rows.sort(key=lambda row: row[0], reverse=True)

    lines: list[str] = []
    object_ids: list[str] = []
    for _, line, object_id in rows[:limit]:
        lines.append(line)
        object_ids.append(object_id)
    return lines, object_ids


def _build_admin_without_due_lines(
    entries: list[AdminEntry],
    *,
    tz: tzinfo,
    include_ids: bool,
    limit: int = _WEEKLY_UNSCHEDULED_LIMIT,
) -> tuple[list[str], list[str]]:
    min_dt = _min_datetime(tz)
    unscheduled = [
        entry
        for entry in entries
        if entry.status in {"open", "blocked"} and entry.due_at is None and entry.due_date is None
    ]
    unscheduled.sort(key=lambda entry: entry.created_at or entry.updated_at or min_dt)

    lines: list[str] = []
    object_ids: list[str] = []
    for entry in unscheduled[:limit]:
        title = _format_title(entry.title, entry.object_id, include_ids)
        if entry.status == "blocked":
            if entry.blocked_reason:
                lines.append(f"{title} - blocked: {entry.blocked_reason}")
            else:
                lines.append(f"{title} - blocked")
        else:
            lines.append(title)
        object_ids.append(entry.object_id)
    return lines, object_ids


def _build_overdue_people_lines(
    entries: list[PeopleEntry],
    *,
    today: date,
    tz: tzinfo,
    include_ids: bool,
    limit: int = _WEEKLY_PEOPLE_OVERDUE_LIMIT,
) -> tuple[list[str], list[str]]:
    min_dt = _min_datetime(tz)
    overdue = [entry for entry in entries if entry.next_contact and entry.next_contact < today]
    overdue.sort(key=lambda entry: (entry.next_contact, entry.updated_at or min_dt))

    lines: list[str] = []
    object_ids: list[str] = []
    for entry in overdue[:limit]:
        title = _format_title(entry.name, entry.object_id, include_ids)
        lines.append(f"{title} - next contact {_format_human_date(entry.next_contact, today)}")
        object_ids.append(entry.object_id)
    return lines, object_ids


def _build_recent_idea_lines(
    items: list[CanonicalItem],
    *,
    now: datetime,
    tz: tzinfo,
    include_ids: bool,
    days: int = _WEEKLY_RECENT_DAYS,
    limit: int = _WEEKLY_IDEAS_LIMIT,
) -> tuple[list[str], list[str]]:
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
    object_ids: list[str] = []
    for item in ideas[:limit]:
        title = _format_title(item.title, item.object_id, include_ids)
        updated = _object_updated_at(item, tz)
        lines.append(f"{title} - updated {_format_human_date(updated.date(), now.date())}")
        object_ids.append(item.object_id)
    return lines, object_ids


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


def _humanize_machine_value(value: str) -> str:
    normalized = value.strip().replace("_", " ").replace("-", " ").lower()
    if not normalized:
        return value.strip()
    return normalized[0].upper() + normalized[1:]


def _list_row_metadata(item: CanonicalItem, *, tz: tzinfo, reference_date: date) -> list[str]:
    parts: list[str] = [_render_type_label(item.object_type)]

    status = item.frontmatter.get("status")
    if isinstance(status, str) and status.strip():
        parts.append(status.strip())

    due_at = _coerce_datetime(item.frontmatter.get("due_at"), tz)
    due_date = _coerce_date(item.frontmatter.get("due_date"))
    if due_at:
        parts.append(f"due {_format_human_datetime(due_at, reference_date)}")
    elif due_date:
        parts.append(f"due {_format_human_date(due_date, reference_date)}")
    else:
        due_at_raw = item.frontmatter.get("due_at")
        due_date_raw = item.frontmatter.get("due_date")
        if isinstance(due_at_raw, str) and due_at_raw.strip():
            parts.append(f"due {due_at_raw.strip()}")
        elif isinstance(due_date_raw, str) and due_date_raw.strip():
            parts.append(f"due {due_date_raw.strip()}")

    updated = _object_updated_at(item, tz)
    if updated > _min_datetime(tz):
        parts.append(f"updated {_format_human_date(updated.date(), reference_date, include_relative=False)}")

    return parts


def _render_list_row(index: int, item: CanonicalItem, *, include_ids: bool, tz: tzinfo, reference_date: date) -> str:
    title = _format_title(item.title, item.object_id, include_ids)
    parts = _list_row_metadata(item, tz=tz, reference_date=reference_date)
    if not parts:
        return f"{index}. {title}"
    bullets = "\n".join(f"   • {part}" for part in parts)
    return f"{index}. {title}\n{bullets}"


def _render_find_row(
    index: int,
    item: CanonicalItem,
    snippet: str,
    *,
    include_ids: bool,
    tz: tzinfo,
    reference_date: date,
) -> str:
    base = _render_list_row(index, item, include_ids=include_ids, tz=tz, reference_date=reference_date)
    cleaned_snippet = " ".join(snippet.split())
    if not cleaned_snippet:
        return base
    if len(cleaned_snippet) > 120:
        cleaned_snippet = f"{cleaned_snippet[:117].rstrip()}..."
    return f"{base}\n   • {cleaned_snippet}"


def _normalize_positive_int_list(values: Iterable[Any]) -> list[int]:
    normalized: set[int] = set()
    for value in values:
        parsed: int | None = None
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, float) and value.is_integer():
            parsed = int(value)
        elif isinstance(value, str):
            trimmed = value.strip()
            if trimmed.isdigit():
                parsed = int(trimmed)
        if parsed is None or parsed <= 0:
            continue
        normalized.add(parsed)
    return sorted(normalized, reverse=True)


def _format_relative_duration(target: datetime, reference: datetime) -> str:
    delta_seconds = int((target - reference).total_seconds())
    if delta_seconds <= 0:
        return "now"
    total_minutes = (delta_seconds + 59) // 60
    if total_minutes < 60:
        unit = "minute" if total_minutes == 1 else "minutes"
        return f"in {total_minutes} {unit}"
    hours, minutes = divmod(total_minutes, 60)
    hour_unit = "hour" if hours == 1 else "hours"
    if minutes == 0:
        return f"in {hours} {hour_unit}"
    minute_unit = "minute" if minutes == 1 else "minutes"
    return f"in {hours} {hour_unit} {minutes} {minute_unit}"


def _load_body(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) == 3:
        return parts[2].strip()
    return content.strip()


def build_due_time_reminder_events(
    objects_root: str | Path,
    config: dict[str, Any],
    *,
    offsets_minutes: list[int] | tuple[int, ...],
    now: datetime | None = None,
    late_grace_minutes: int = 10,
    horizon_hours: int = 36,
) -> list[DueTimeReminderEvent]:
    tz = resolve_timezone(config.get("timezone"))
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    offsets = _normalize_positive_int_list(offsets_minutes)
    if not offsets:
        return []

    grace_minutes = max(0, int(late_grace_minutes))
    horizon = max(1, int(horizon_hours))
    window_start = now - timedelta(minutes=grace_minutes)
    window_end = now + timedelta(hours=horizon)

    events: list[DueTimeReminderEvent] = []
    items = _load_items(objects_root)
    admin_items = _admin_entries(items, tz)
    for entry in admin_items:
        status = entry.status.strip().lower()
        if status not in {"open", "blocked"}:
            continue
        if entry.due_at is None:
            continue
        for offset in offsets:
            fire_at = entry.due_at - timedelta(minutes=offset)
            if fire_at < window_start or fire_at > window_end:
                continue
            events.append(
                DueTimeReminderEvent(
                    object_id=entry.object_id,
                    title=entry.title,
                    due_at=entry.due_at,
                    offset_minutes=offset,
                    fire_at=fire_at,
                )
            )
    events.sort(key=lambda item: (item.fire_at, item.due_at, item.object_id, item.offset_minutes))
    return events


def render_due_time_reminder_message(
    events: list[DueTimeReminderEvent],
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    if not events:
        return ""
    tz = resolve_timezone(config.get("timezone"))
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    sorted_events = sorted(events, key=lambda item: (item.due_at, item.object_id, item.offset_minutes))
    header_date = _format_human_date(now.date(), now.date(), include_relative=False)
    lines = [f"⏰ **Upcoming due reminders** · {header_date}"]
    for event in sorted_events:
        due_rendered = _format_human_datetime(event.due_at, now.date(), include_relative=False)
        relative = _format_relative_duration(event.due_at, now)
        lines.append(f"• {event.title} - due {due_rendered} ({relative})")
    return "\n".join(lines)


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

    overdue_sorted = _sort_due(overdue)
    overdue_lines = _format_admin_due_lines(
        overdue_sorted,
        surfacing.show_ids_daily_weekly,
        reference_date=now.date(),
        reference_datetime=now,
    )
    today_sorted = _sort_due(today)
    today_lines = _format_admin_due_lines(
        today_sorted,
        surfacing.show_ids_daily_weekly,
        reference_date=now.date(),
        include_relative=False,
        omit_date_for_same_day=True,
    )
    soon_sorted = _sort_due(soon)
    soon_lines = _format_admin_due_lines(
        soon_sorted,
        surfacing.show_ids_daily_weekly,
        reference_date=now.date(),
    )
    unscheduled_admin_lines, unscheduled_admin_ids = _build_admin_without_due_lines(
        admin_items,
        tz=tz,
        include_ids=surfacing.show_ids_daily_weekly,
    )
    project_lines, project_ids = _build_project_attention_lines(
        project_items,
        now=now,
        stale_days=surfacing.projects_stale_days,
        limit=surfacing.projects_blocked_limit,
        include_ids=surfacing.show_ids_daily_weekly,
    )

    people_cutoff = now.date() + timedelta(days=surfacing.people_next_contact_days)
    people_lines, people_ids = _format_people_lines(
        people_items,
        people_cutoff,
        tz,
        surfacing.show_ids_daily_weekly,
        reference_date=now.date(),
    )

    sections = [
        DigestSection(title="Admin overdue", lines=overdue_lines, object_ids=[entry.object_id for entry in overdue_sorted]),
        DigestSection(title="Admin due today", lines=today_lines, object_ids=[entry.object_id for entry in today_sorted]),
        DigestSection(title="Admin due soon", lines=soon_lines, object_ids=[entry.object_id for entry in soon_sorted]),
        DigestSection(
            title="Admin without due dates",
            lines=unscheduled_admin_lines,
            object_ids=unscheduled_admin_ids,
        ),
        DigestSection(title="Projects needing attention", lines=project_lines, object_ids=project_ids),
        DigestSection(title="People to follow up", lines=people_lines, object_ids=people_ids),
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

    completed_lines, completed_ids = _build_completed_this_week_lines(
        items,
        now=now,
        tz=tz,
        include_ids=surfacing.show_ids_daily_weekly,
    )
    unscheduled_admin_lines, unscheduled_admin_ids = _build_admin_without_due_lines(
        admin_items,
        tz=tz,
        include_ids=surfacing.show_ids_daily_weekly,
    )
    project_lines, project_ids = _build_project_attention_lines(
        project_items,
        now=now,
        stale_days=surfacing.projects_stale_days,
        limit=surfacing.projects_blocked_limit,
        include_ids=surfacing.show_ids_daily_weekly,
    )
    overdue_people_lines, overdue_people_ids = _build_overdue_people_lines(
        people_items,
        today=now.date(),
        tz=tz,
        include_ids=surfacing.show_ids_daily_weekly,
    )

    sections: list[DigestSection] = []
    if completed_lines:
        sections.append(
            DigestSection(
                title="Completed this week",
                lines=completed_lines,
                object_ids=completed_ids,
            )
        )
    sections.extend(
        [
            DigestSection(
                title="Admin without due dates",
                lines=unscheduled_admin_lines,
                object_ids=unscheduled_admin_ids,
            ),
            DigestSection(title="Blocked or stale projects", lines=project_lines, object_ids=project_ids),
            DigestSection(
                title="People overdue for contact",
                lines=overdue_people_lines,
                object_ids=overdue_people_ids,
            ),
        ]
    )
    if surfacing.ideas_weekly_review:
        idea_lines, idea_ids = _build_recent_idea_lines(
            items,
            now=now,
            tz=tz,
            include_ids=surfacing.show_ids_daily_weekly,
        )
        sections.append(
            DigestSection(
                title="Ideas updated recently",
                lines=idea_lines,
                object_ids=idea_ids,
            )
        )

    return WeeklyReview(generated_at=now, sections=sections)


def build_recent_list(
    objects_root: str | Path,
    config: dict[str, Any],
    *,
    limit: int | None = None,
    object_type: str | None = None,
) -> SurfacedList:
    tz = resolve_timezone(config.get("timezone"))
    surfacing = load_surfacing_config(config)
    effective_limit = _clamp_limit(limit, surfacing.pull_default_recent_limit)

    items = [item for item in _load_items(objects_root) if not _is_archived(item.frontmatter.get("archived"))]
    if object_type:
        items = [item for item in items if item.object_type == object_type]
    items.sort(key=lambda item: _object_updated_at(item, tz), reverse=True)
    selected = items[:effective_limit]
    reference_date = datetime.now(tz).date()

    lines = [
        _render_list_row(index + 1, item, include_ids=False, tz=tz, reference_date=reference_date)
        for index, item in enumerate(selected)
    ]
    object_ids = [item.object_id for item in selected]
    return SurfacedList(lines=lines, object_ids=object_ids)


def _is_active_item(item: CanonicalItem) -> bool:
    if _is_archived(item.frontmatter.get("archived")):
        return False
    status = str(item.frontmatter.get("status") or "").strip().lower()
    if item.object_type == "admin":
        return status != "done"
    if item.object_type == "projects":
        return status != "completed"
    if item.object_type == "ideas":
        return status != "done"
    if item.object_type == "people":
        return True
    return True


def build_active_list(
    objects_root: str | Path,
    config: dict[str, Any],
    *,
    limit: int | None = None,
    object_type: str | None = None,
) -> SurfacedList:
    tz = resolve_timezone(config.get("timezone"))
    items = [item for item in _load_items(objects_root) if _is_active_item(item)]
    if object_type:
        items = [item for item in items if item.object_type == object_type]
    items.sort(key=lambda item: _object_updated_at(item, tz), reverse=True)
    if limit is not None:
        items = items[:_clamp_limit(limit, limit)]

    reference_date = datetime.now(tz).date()
    lines: list[str] = []
    object_ids: list[str] = []
    row_number = 1

    for section_object_type, section_title in _ACTIVE_SECTION_ORDER:
        section_items = [item for item in items if item.object_type == section_object_type]
        if not section_items:
            continue
        if lines:
            lines.append("")
        lines.append(_format_section_title(section_title))
        lines.append(_section_divider(section_title))
        for item in section_items:
            lines.append(
                _render_list_row(
                    row_number,
                    item,
                    include_ids=False,
                    tz=tz,
                    reference_date=reference_date,
                )
            )
            object_ids.append(item.object_id)
            row_number += 1

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
    reference_date = datetime.now(tz).date()
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
                include_ids=False,
                tz=tz,
                reference_date=reference_date,
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

    title = frontmatter.get("title")
    object_type = frontmatter.get("type")
    if not isinstance(title, str) or not isinstance(object_type, str):
        return None

    lines = [f"**Title:** {title}", f"**Type:** {_humanize_machine_value(_render_type_label(object_type))}"]

    field_map = [
        ("status", "Status"),
        ("priority", "Priority"),
        ("due_at", "Due at"),
        ("due_date", "Due date"),
        ("next_action", "Next action"),
        ("next_contact", "Next contact"),
    ]
    for key, label in field_map:
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            rendered = value.strip()
            if key in {"status", "priority"}:
                rendered = _humanize_machine_value(rendered)
            lines.append(f"**{label}:** {rendered}")

    body = _load_body(path)
    if body:
        body_lines = [line.strip() for line in body.splitlines() if line.strip()]
        if body_lines:
            lines.append("")
            lines.append("**Notes:**")
            for line in body_lines[:6]:
                lines.append(f"- {line}")

    return "\n".join(lines)
