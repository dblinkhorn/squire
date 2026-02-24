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


class RuntimeStateStore:
    """Mutable runtime state container scoped to one transport runtime instance."""

    def __init__(self) -> None:
        self.result_cursors: dict[InteractionKey, ResultCursor] = {}
        self.matching_affinity: dict[InteractionKey, list[AffinityTouch]] = {}
        self.archive_clear_confirmations: dict[InteractionKey, ArchiveClearConfirmation] = {}
        self.nl_clarification_contexts: dict[InteractionKey, NLClarificationContext] = {}

    def clear_runtime_state(self) -> None:
        self.result_cursors.clear()
        self.matching_affinity.clear()
        self.archive_clear_confirmations.clear()
        self.nl_clarification_contexts.clear()

    def prune_result_cursors(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        expired = [key for key, value in self.result_cursors.items() if value.expires_at <= current]
        for key in expired:
            self.result_cursors.pop(key, None)

    def store_result_cursor(
        self,
        key: InteractionKey,
        object_ids: list[str],
        *,
        ttl_minutes: int,
        source_view: str = "unknown",
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        expires_at = current + timedelta(minutes=max(1, ttl_minutes))
        self.result_cursors[key] = ResultCursor(
            object_ids=list(object_ids),
            expires_at=expires_at,
            source_view=source_view,
        )
        self.prune_result_cursors(now=current)

    def resolve_result_cursor_with_reason(
        self,
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
            candidate = self.result_cursors.get(candidate_key)
            if candidate is None:
                continue
            if candidate.expires_at <= current:
                saw_expired = True
                self.result_cursors.pop(candidate_key, None)
                continue
            cursor = candidate
            break

        self.prune_result_cursors(now=current)
        if cursor is None:
            if saw_expired:
                return None, "expired", None
            return None, "missing", None
        index = number - 1
        if index < 0 or index >= len(cursor.object_ids):
            return None, "out_of_range", cursor.source_view
        return cursor.object_ids[index], None, cursor.source_view

    def resolve_result_cursor(
        self,
        key: InteractionKey,
        number: int,
        *,
        fallback_keys: tuple[InteractionKey, ...] = (),
        now: datetime | None = None,
    ) -> str | None:
        object_id, _, _ = self.resolve_result_cursor_with_reason(
            key,
            number,
            fallback_keys=fallback_keys,
            now=now,
        )
        return object_id

    def record_affinity_touches(
        self,
        key: InteractionKey,
        object_ids: list[str],
        *,
        matching: MatchingConfig,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        ttl = timedelta(days=max(1, matching.affinity_ttl_days))
        cutoff = current - ttl
        touches = [touch for touch in self.matching_affinity.get(key, []) if touch.touched_at >= cutoff]
        for object_id in object_ids:
            if not isinstance(object_id, str):
                continue
            value = object_id.strip()
            if not value:
                continue
            touches.append(AffinityTouch(object_id=value, touched_at=current))
        if not touches:
            self.matching_affinity.pop(key, None)
            return
        touches = touches[-matching.affinity_recent_ids_per_thread :]
        self.matching_affinity[key] = touches

    def load_affinity_scores(
        self,
        key: InteractionKey,
        *,
        matching: MatchingConfig,
        now: datetime | None = None,
    ) -> dict[str, float]:
        current = now or datetime.now(timezone.utc)
        ttl = timedelta(days=max(1, matching.affinity_ttl_days))
        cutoff = current - ttl
        touches = [touch for touch in self.matching_affinity.get(key, []) if touch.touched_at >= cutoff]
        if not touches:
            self.matching_affinity.pop(key, None)
            return {}

        self.matching_affinity[key] = touches[-matching.affinity_recent_ids_per_thread :]
        scores: dict[str, float] = {}
        for touch in self.matching_affinity[key]:
            age = max(0.0, (current - touch.touched_at).total_seconds())
            ttl_seconds = max(1.0, ttl.total_seconds())
            decayed = max(0.0, 1.0 - (age / ttl_seconds))
            existing = scores.get(touch.object_id, 0.0)
            if decayed > existing:
                scores[touch.object_id] = decayed
        return scores

    def prune_archive_clear_confirmations(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        expired = [key for key, value in self.archive_clear_confirmations.items() if value.expires_at <= current]
        for key in expired:
            self.archive_clear_confirmations.pop(key, None)

    def store_archive_clear_confirmation(
        self,
        key: InteractionKey,
        *,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        expires_at = current + timedelta(seconds=max(1, ttl_seconds))
        self.archive_clear_confirmations[key] = ArchiveClearConfirmation(expires_at=expires_at)
        self.prune_archive_clear_confirmations(now=current)

    def consume_archive_clear_confirmation(
        self,
        key: InteractionKey,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        self.prune_archive_clear_confirmations(now=current)
        confirmation = self.archive_clear_confirmations.get(key)
        if confirmation is None:
            return False
        self.archive_clear_confirmations.pop(key, None)
        return True

    def prune_nl_clarification_contexts(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        expired_keys = [key for key, value in self.nl_clarification_contexts.items() if value.expires_at <= current]
        for key in expired_keys:
            self.nl_clarification_contexts.pop(key, None)

    def get_nl_clarification_context(
        self,
        key: InteractionKey,
        *,
        now: datetime | None = None,
    ) -> NLClarificationContext | None:
        current = now or datetime.now(timezone.utc)
        context = self.nl_clarification_contexts.get(key)
        if context is None:
            return None
        if context.expires_at <= current:
            self.nl_clarification_contexts.pop(key, None)
            return None
        return context

    def store_nl_clarification_context(
        self,
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
        self.nl_clarification_contexts[key] = NLClarificationContext(
            raw_event_id=raw_event_id,
            expires_at=expires_at,
            unresolved_scope=unresolved_scope,
            base_plan_input=base_plan_input,
        )
        self.prune_nl_clarification_contexts(now=current)

    def clear_nl_clarification_context(self, key: InteractionKey) -> None:
        self.nl_clarification_contexts.pop(key, None)

def clear_runtime_state(*, state_store: RuntimeStateStore) -> None:
    state_store.clear_runtime_state()


def prune_result_cursors(*, now: datetime | None = None, state_store: RuntimeStateStore) -> None:
    state_store.prune_result_cursors(now=now)


def store_result_cursor(
    key: InteractionKey,
    object_ids: list[str],
    *,
    ttl_minutes: int,
    source_view: str = "unknown",
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> None:
    state_store.store_result_cursor(
        key,
        object_ids,
        ttl_minutes=ttl_minutes,
        source_view=source_view,
        now=now,
    )


def resolve_result_cursor_with_reason(
    key: InteractionKey,
    number: int,
    *,
    fallback_keys: tuple[InteractionKey, ...] = (),
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> tuple[str | None, str | None, str | None]:
    return state_store.resolve_result_cursor_with_reason(
        key,
        number,
        fallback_keys=fallback_keys,
        now=now,
    )


def resolve_result_cursor(
    key: InteractionKey,
    number: int,
    *,
    fallback_keys: tuple[InteractionKey, ...] = (),
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> str | None:
    return state_store.resolve_result_cursor(
        key,
        number,
        fallback_keys=fallback_keys,
        now=now,
    )


def record_affinity_touches(
    key: InteractionKey,
    object_ids: list[str],
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> None:
    state_store.record_affinity_touches(
        key,
        object_ids,
        matching=matching,
        now=now,
    )


def load_affinity_scores(
    key: InteractionKey,
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> dict[str, float]:
    return state_store.load_affinity_scores(
        key,
        matching=matching,
        now=now,
    )


def prune_archive_clear_confirmations(
    *,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> None:
    state_store.prune_archive_clear_confirmations(now=now)


def store_archive_clear_confirmation(
    key: InteractionKey,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> None:
    state_store.store_archive_clear_confirmation(
        key,
        ttl_seconds=ttl_seconds,
        now=now,
    )


def consume_archive_clear_confirmation(
    key: InteractionKey,
    *,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> bool:
    return state_store.consume_archive_clear_confirmation(
        key,
        now=now,
    )


def prune_nl_clarification_contexts(
    *,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> None:
    state_store.prune_nl_clarification_contexts(now=now)


def get_nl_clarification_context(
    key: InteractionKey,
    *,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> NLClarificationContext | None:
    return state_store.get_nl_clarification_context(
        key,
        now=now,
    )


def store_nl_clarification_context(
    key: InteractionKey,
    *,
    raw_event_id: str,
    unresolved_scope: dict[str, dict[str, Any]],
    base_plan_input: dict[str, Any],
    ttl_seconds: int,
    now: datetime | None = None,
    state_store: RuntimeStateStore,
) -> None:
    state_store.store_nl_clarification_context(
        key,
        raw_event_id=raw_event_id,
        unresolved_scope=unresolved_scope,
        base_plan_input=base_plan_input,
        ttl_seconds=ttl_seconds,
        now=now,
    )


def clear_nl_clarification_context(
    key: InteractionKey,
    *,
    state_store: RuntimeStateStore,
) -> None:
    state_store.clear_nl_clarification_context(key)


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
