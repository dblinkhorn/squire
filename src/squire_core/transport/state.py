"""Shared runtime state containers for transport adapters.

Stage 0 mirrors existing state shapes from ``discord_bot.py`` so later
extraction can move behavior without changing data contracts.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


InteractionKey = tuple[str, str]


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


# Stage 0 shared state containers (not yet wired into runtime behavior).
RESULT_CURSORS: dict[InteractionKey, ResultCursor] = {}
MATCHING_AFFINITY: dict[InteractionKey, list[AffinityTouch]] = {}
ARCHIVE_CLEAR_CONFIRMATIONS: dict[InteractionKey, ArchiveClearConfirmation] = {}
NL_CLARIFICATION_CONTEXTS: dict[InteractionKey, NLClarificationContext] = {}

