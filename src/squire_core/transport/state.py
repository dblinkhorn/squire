"""Shared runtime state and helper logic for transport adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from squire_core.config_utils import MatchingConfig
from squire_core.surfacing import DailyDigest, DigestSection, WeeklyReview


InteractionToken = str | int
InteractionKey = tuple[InteractionToken, InteractionToken]


@dataclass(frozen=True)
class ResultCursor:
    object_ids: list[str]
    expires_at: datetime
    source_view: str = "unknown"


@dataclass(frozen=True)
class CommandTargetResolution:
    target_id: str | None
    error: str | None
    reason: str | None
    row_number: int | None
    source_view: str | None


@dataclass(frozen=True)
class NLRouteIntentV1:
    route: str
    intent: str
    risk_tier: str
    confidence: float
    ambiguities: list[str]
    read_command: dict[str, Any] | None
    mutation_plan: dict[str, Any] | None
    clarification: dict[str, Any] | None


@dataclass(frozen=True)
class AffinityTouch:
    object_id: str
    touched_at: datetime


@dataclass(frozen=True)
class ArchiveClearConfirmation:
    expires_at: datetime


@dataclass(frozen=True)
class NLClarificationContext:
    raw_event_id: str
    expires_at: datetime
    unresolved_scope: dict[str, dict[str, Any]]
    base_plan_input: dict[str, Any]


@dataclass(frozen=True)
class DueTimeReminderScheduleConfig:
    offsets_minutes: tuple[int, ...]
    late_grace_minutes: int
    reconcile_minutes: int
    channel_id: int | None
    user_id: int | None

    @property
    def enabled(self) -> bool:
        return bool(self.offsets_minutes)


@dataclass(frozen=True)
class DueTimeReminderSentLedgerEntry:
    key: str
    object_id: str
    due_at: datetime
    offset_minutes: int
    fire_at: datetime
    sent_at: datetime
    expires_at: datetime


# Shared state containers used by transport adapters and compatibility shims.
RESULT_CURSORS: dict[InteractionKey, ResultCursor] = {}
MATCHING_AFFINITY: dict[InteractionKey, list[AffinityTouch]] = {}
ARCHIVE_CLEAR_CONFIRMATIONS: dict[InteractionKey, ArchiveClearConfirmation] = {}
NL_CLARIFICATION_CONTEXTS: dict[InteractionKey, NLClarificationContext] = {}


def prune_result_cursors(*, now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [key for key, value in RESULT_CURSORS.items() if value.expires_at <= current]
    for key in expired:
        RESULT_CURSORS.pop(key, None)


def get_result_cursor(key: InteractionKey, *, now: datetime | None = None) -> ResultCursor | None:
    current = now or datetime.now(timezone.utc)
    cursor = RESULT_CURSORS.get(key)
    if cursor is None:
        return None
    if cursor.expires_at <= current:
        RESULT_CURSORS.pop(key, None)
        return None
    return cursor


def store_result_cursor(
    key: InteractionKey,
    object_ids: list[str],
    *,
    ttl_minutes: int,
    source_view: str = "unknown",
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    expires_at = current + timedelta(minutes=max(1, ttl_minutes))
    RESULT_CURSORS[key] = ResultCursor(
        object_ids=list(object_ids),
        expires_at=expires_at,
        source_view=source_view,
    )
    prune_result_cursors(now=current)


def resolve_result_cursor_with_reason(
    key: InteractionKey,
    number: int,
    *,
    fallback_keys: tuple[InteractionKey, ...] = (),
    now: datetime | None = None,
) -> tuple[str | None, str | None, str | None]:
    current = now or datetime.now(timezone.utc)
    saw_expired = False
    cursor: ResultCursor | None = None

    for candidate_key in (key, *fallback_keys):
        candidate = RESULT_CURSORS.get(candidate_key)
        if candidate is None:
            continue
        if candidate.expires_at <= current:
            saw_expired = True
            RESULT_CURSORS.pop(candidate_key, None)
            continue
        cursor = candidate
        break

    prune_result_cursors(now=current)
    if cursor is None:
        if saw_expired:
            return None, "expired", None
        return None, "missing", None
    index = number - 1
    if index < 0 or index >= len(cursor.object_ids):
        return None, "out_of_range", cursor.source_view
    return cursor.object_ids[index], None, cursor.source_view


def resolve_result_cursor(
    key: InteractionKey,
    number: int,
    *,
    fallback_keys: tuple[InteractionKey, ...] = (),
    now: datetime | None = None,
) -> str | None:
    object_id, _, _ = resolve_result_cursor_with_reason(
        key,
        number,
        fallback_keys=fallback_keys,
        now=now,
    )
    return object_id


def record_affinity_touches(
    key: InteractionKey,
    object_ids: list[str],
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    ttl = timedelta(days=max(1, matching.affinity_ttl_days))
    cutoff = current - ttl
    touches = [touch for touch in MATCHING_AFFINITY.get(key, []) if touch.touched_at >= cutoff]
    for object_id in object_ids:
        if not isinstance(object_id, str):
            continue
        value = object_id.strip()
        if not value:
            continue
        touches.append(AffinityTouch(object_id=value, touched_at=current))
    if not touches:
        MATCHING_AFFINITY.pop(key, None)
        return
    touches = touches[-matching.affinity_recent_ids_per_thread :]
    MATCHING_AFFINITY[key] = touches


def load_affinity_scores(
    key: InteractionKey,
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
) -> dict[str, float]:
    current = now or datetime.now(timezone.utc)
    ttl = timedelta(days=max(1, matching.affinity_ttl_days))
    cutoff = current - ttl
    touches = [touch for touch in MATCHING_AFFINITY.get(key, []) if touch.touched_at >= cutoff]
    if not touches:
        MATCHING_AFFINITY.pop(key, None)
        return {}

    MATCHING_AFFINITY[key] = touches[-matching.affinity_recent_ids_per_thread :]
    scores: dict[str, float] = {}
    for touch in MATCHING_AFFINITY[key]:
        age = max(0.0, (current - touch.touched_at).total_seconds())
        ttl_seconds = max(1.0, ttl.total_seconds())
        decayed = max(0.0, 1.0 - (age / ttl_seconds))
        existing = scores.get(touch.object_id, 0.0)
        if decayed > existing:
            scores[touch.object_id] = decayed
    return scores


def prune_archive_clear_confirmations(*, now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [key for key, value in ARCHIVE_CLEAR_CONFIRMATIONS.items() if value.expires_at <= current]
    for key in expired:
        ARCHIVE_CLEAR_CONFIRMATIONS.pop(key, None)


def get_archive_clear_confirmation(
    key: InteractionKey,
    *,
    now: datetime | None = None,
) -> ArchiveClearConfirmation | None:
    current = now or datetime.now(timezone.utc)
    confirmation = ARCHIVE_CLEAR_CONFIRMATIONS.get(key)
    if confirmation is None:
        return None
    if confirmation.expires_at <= current:
        ARCHIVE_CLEAR_CONFIRMATIONS.pop(key, None)
        return None
    return confirmation


def store_archive_clear_confirmation(
    key: InteractionKey,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    expires_at = current + timedelta(seconds=max(1, ttl_seconds))
    ARCHIVE_CLEAR_CONFIRMATIONS[key] = ArchiveClearConfirmation(expires_at=expires_at)
    prune_archive_clear_confirmations(now=current)


def consume_archive_clear_confirmation(
    key: InteractionKey,
    *,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    prune_archive_clear_confirmations(now=current)
    confirmation = ARCHIVE_CLEAR_CONFIRMATIONS.get(key)
    if confirmation is None:
        return False
    ARCHIVE_CLEAR_CONFIRMATIONS.pop(key, None)
    return True


def prune_nl_clarification_contexts(*, now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired_keys = [key for key, value in NL_CLARIFICATION_CONTEXTS.items() if value.expires_at <= current]
    for key in expired_keys:
        NL_CLARIFICATION_CONTEXTS.pop(key, None)


def get_nl_clarification_context(
    key: InteractionKey,
    *,
    now: datetime | None = None,
) -> NLClarificationContext | None:
    current = now or datetime.now(timezone.utc)
    context = NL_CLARIFICATION_CONTEXTS.get(key)
    if context is None:
        return None
    if context.expires_at <= current:
        NL_CLARIFICATION_CONTEXTS.pop(key, None)
        return None
    return context


def store_nl_clarification_context(
    key: InteractionKey,
    *,
    raw_event_id: str,
    unresolved_scope: dict[str, dict[str, Any]],
    base_plan_input: dict[str, Any],
    ttl_seconds: int,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    expires_at = current + timedelta(seconds=max(1, ttl_seconds))
    NL_CLARIFICATION_CONTEXTS[key] = NLClarificationContext(
        raw_event_id=raw_event_id,
        expires_at=expires_at,
        unresolved_scope=unresolved_scope,
        base_plan_input=base_plan_input,
    )
    prune_nl_clarification_contexts(now=current)


def clear_nl_clarification_context(key: InteractionKey) -> None:
    NL_CLARIFICATION_CONTEXTS.pop(key, None)


def number_sections_for_cursor(sections: list[DigestSection]) -> tuple[list[DigestSection], list[str]]:
    next_number = 1
    cursor_object_ids: list[str] = []
    numbered_sections: list[DigestSection] = []

    for section in sections:
        numbered_lines: list[str] = []
        numbered_object_ids: list[str] = []
        for index, line in enumerate(section.lines):
            object_id: str | None = None
            if index < len(section.object_ids):
                candidate = section.object_ids[index]
                if isinstance(candidate, str) and candidate.strip():
                    object_id = candidate
            if object_id is None:
                numbered_lines.append(line)
                continue
            numbered_lines.append(format_numbered_row(section.title, line, next_number))
            numbered_object_ids.append(object_id)
            cursor_object_ids.append(object_id)
            next_number += 1
        numbered_sections.append(
            DigestSection(
                title=section.title,
                lines=numbered_lines,
                object_ids=numbered_object_ids,
            )
        )

    return numbered_sections, cursor_object_ids


def format_numbered_row(section_title: str, line: str, number: int) -> str:
    parsed = _split_section_row_metadata(section_title, line)
    if parsed is None:
        return f"{number}. {line}"
    title, metadata = parsed
    if not metadata:
        return f"{number}. {title}"
    bullet_lines = "\n".join(f"   • {item}" for item in metadata)
    return f"{number}. {title}\n{bullet_lines}"


def render_numbered_daily_digest_for_command(
    digest: Any,
    *,
    numbered_command_tip: str,
) -> tuple[str, list[str]]:
    if not isinstance(digest, DailyDigest):
        render = getattr(digest, "render", None)
        if callable(render):
            return str(render()), []
        return "", []

    sections, object_ids = number_sections_for_cursor(digest.sections)
    rendered = DailyDigest(generated_at=digest.generated_at, sections=sections).render()
    if object_ids:
        rendered = f"{rendered}\n\n{numbered_command_tip}"
    return rendered, object_ids


def render_numbered_weekly_review_for_command(
    review: Any,
    *,
    numbered_command_tip: str,
) -> tuple[str, list[str]]:
    if not isinstance(review, WeeklyReview):
        render = getattr(review, "render", None)
        if callable(render):
            return str(render()), []
        return "", []

    sections, object_ids = number_sections_for_cursor(review.sections)
    rendered = WeeklyReview(generated_at=review.generated_at, sections=sections).render()
    if object_ids:
        rendered = f"{rendered}\n\n{numbered_command_tip}"
    return rendered, object_ids


def _split_section_row_metadata(section_title: str, line: str) -> tuple[str, list[str]] | None:
    value = line.strip()
    if not value:
        return None

    section_markers: dict[str, list[str]] = {
        "Admin overdue": [" - due "],
        "Admin due today": [" - due "],
        "Admin due soon": [" - due "],
        "Projects needing attention": [" - blocked", " - stale "],
        "People to follow up": [" - next contact "],
        "Completed this week": [" - "],
        "Open admin without due dates": [" - "],
        "Blocked or stale projects": [" - blocked", " - stale "],
        "People overdue for contact": [" - next contact "],
        "Ideas updated recently": [" - updated "],
    }

    markers = section_markers.get(section_title, [" - "])
    for marker in markers:
        index = value.find(marker)
        if index <= 0:
            continue
        title = value[:index].strip()
        metadata_value = value[index + 3 :].strip()
        if not title or not metadata_value:
            continue
        metadata = _split_metadata_items(section_title, metadata_value)
        return title, metadata

    return None


def _split_metadata_items(section_title: str, metadata_value: str) -> list[str]:
    multi_item_sections = {"Completed this week", "Open admin without due dates"}
    if section_title not in multi_item_sections:
        return [metadata_value]
    return [part.strip() for part in metadata_value.split(",") if part.strip()]
