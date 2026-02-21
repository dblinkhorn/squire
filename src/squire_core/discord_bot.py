from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

import discord
from dotenv import load_dotenv

from squire_core.config_utils import (
    MatchingConfig,
    NLCommandRoutingConfig,
    load_config,
    load_decision_config,
    load_matching_config,
    load_nl_command_routing_config,
    normalize_archive_config,
)
from squire_core.decision_flow import DecisionRouting, apply_decision_to_derived, evaluate_decision
from squire_core.derived_event_store import write_derived_event
from squire_core.id_utils import generate_prefixed_id
from squire_core.indexer import rebuild_index
from squire_core.interpreter import InterpretationValidationError, interpret_text_async
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.prompts import load_prompt
from squire_core.matching import build_matching_candidates_async, sync_semantic_index
from squire_core.operation_apply import apply_operations
from squire_core.pending_actions import (
    PendingAction,
    load_pending_action,
    update_pending_action_status,
    write_pending_action,
)
from squire_core.raw_event import RawEvent, Source, write_raw_event
from squire_core.schema_loader import load_json_schema, validate_json
from squire_core.canonical_store import find_object_path, load_frontmatter
from squire_core.surfacing import (
    DailyDigest,
    DigestSection,
    DueTimeReminderEvent,
    WeeklyReview,
    build_daily_digest,
    build_due_time_reminder_events,
    build_find_list,
    build_item_detail,
    build_recent_list,
    build_weekly_review,
    load_surfacing_config,
    render_due_time_reminder_message,
)
from squire_core.timezone_utils import (
    format_reference_date,
    format_reference_time,
    format_reference_weekday,
    resolve_timezone,
)
from squire_core.transport.bootstrap import (
    apply_test_archive_root_override as _apply_test_archive_root_override,
    clear_archive_contents as _clear_archive_contents,
    configure_logging as _configure_logging,
    next_daily_run as _next_daily_run,
    next_midnight_run as _next_midnight_run,
    next_weekly_run as _next_weekly_run,
    parse_daily_digest_time as _parse_daily_digest_time,
    parse_weekly_review_day as _parse_weekly_review_day,
    run_test_mode_reset_seed as _run_test_mode_reset_seed,
)
from squire_core.transport.health import (
    HealthServer as _HealthServer,
    parse_health_port as _parse_health_port,
    start_health_server as _start_health_server,
)
from squire_core.transport.reminders import (
    due_time_reminder_key as _due_time_reminder_key,
    due_time_reminder_ledger_path as _due_time_reminder_ledger_path,
    flush_due_time_reminder_ledger_entries as _flush_due_time_reminder_ledger_entries,
    load_due_time_reminder_ledger_entries as _load_due_time_reminder_ledger_entries,
    load_due_time_reminder_schedule_config as _load_due_time_reminder_schedule_config,
    notify_due_time_reminder_schedule_changed as _notify_due_time_reminder_schedule_changed,
    parse_due_time_reminder_offsets as _parse_due_time_reminder_offsets,
    serialize_due_time_reminder_ledger_entries as _serialize_due_time_reminder_ledger_entries,
)
from squire_core.transport.state import (
    DueTimeReminderScheduleConfig as _DueTimeReminderScheduleConfig,
    DueTimeReminderSentLedgerEntry as _DueTimeReminderSentLedgerEntry,
)

_SCHEMA_MAP = {
    "people": Path("config/schemas/derived_event_people_v1.json"),
    "projects": Path("config/schemas/derived_event_projects_v1.json"),
    "ideas": Path("config/schemas/derived_event_ideas_v1.json"),
    "admin": Path("config/schemas/derived_event_admin_v1.json"),
}
_VIEW_TIMEOUT_SECONDS = 3600
_SELECT_OPTION_LIMIT = 25
_SELECT_LABEL_LIMIT = 100
_SELECT_DESCRIPTION_LIMIT = 100
_ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS = 120
_DEFAULT_HEALTH_HOST = "0.0.0.0"
_DEFAULT_HEALTH_PORT = 8080
_DUE_TIME_REMINDER_NOTIFY_CONFIG_KEY = "_due_time_reminder_notify"
_DUE_TIME_REMINDER_LEDGER_FILENAME = "due_time_reminder_sent_ledger_v1.json"
_DUE_TIME_REMINDER_LEDGER_RETENTION_HOURS = 48
_DUE_TIME_REMINDER_HORIZON_HOURS = 36
_DUE_TIME_REMINDER_EMPTY_QUEUE_WAIT_SECONDS = 300
_DUE_TIME_REMINDER_DEFAULT_OFFSETS_MINUTES = (90, 15)
_DUE_TIME_REMINDER_ALLOWED_STATUSES = {"open", "blocked"}
_PENDING_CONTROLS_INSTRUCTION = (
    "Use the buttons below to confirm which note should be updated, choose to create a new note, or cancel (do nothing):"
)
_NUMBERED_COMMAND_TIP = (
    "Tip: `!show <number>` · `!done <number>` · `!append <number> <text>` · `!fix <number> field=value`"
)
_NUMBERED_COMMAND_TIP_WITH_RECENT_LIMIT = (
    "Tip: `!show <number>` · `!done <number>` · `!append <number> <text>` · `!fix <number> field=value` · `!recent <number>` (up to 50)"
)
_NUMBERED_LIST_ACTION_HELP_COPY = (
    "After this command shows a numbered list, you can use those numbers to take action on items (for example: "
    "`!show 2`, `!append 2 <text>`, `!fix 2 field=value`, or `!done 2` for admin items)."
)
_HELP_COPY = (
    "Available commands:\n"
    "- `!help [command]` - show this list or command details\n"
    "- `!status` - show daily digest\n"
    "- `!weekly` - show weekly review\n"
    "- `!recent [number]` - list recent notes\n"
    "- `!find <query>` - search notes\n"
    "- `!show <number>` - open one result\n"
    "- `!append <id|number> <text>` - append note text\n"
    "- `!done <id|number>` - mark admin done\n"
    "- `!fix <id|number> <field=value> [field=value ...]` - edit note fields\n"
    "- `!confirm <pending_id>` - apply pending change\n"
    "- `!cancel <pending_id>` - cancel pending change\n"
    "- `!clear-archive` then `DELETE` - clear archive data\n"
    "Tip: run `!help <command>` for more details."
)
_HELP_DETAILS = {
    "help": (
        "`!help [command]`\n"
        "Shows the command list. Add a command name for a more detailed description of that command."
    ),
    "status": (
        "`!status`\n"
        "Shows your daily digest.\n"
        + _NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "weekly": (
        "`!weekly`\n"
        "Shows your weekly review.\n"
        + _NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "recent": (
        "`!recent [number]`\n"
        "Lists your recent notes. Use `!recent [number]` to show your most recent notes (up to 50).\n"
        + _NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "find": (
        "`!find <query>`\n"
        "Searches your notes by title and body.\n"
        + _NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "show": (
        "`!show <number>`\n"
        "Opens details for one item from your latest numbered list (for example, after `!recent`, `!find`, "
        "`!status`, or `!weekly`)."
    ),
    "append": (
        "`!append <id|number> <text>`\n"
        "Appends text to an existing note body. The target can be an ID or a row number from your latest numbered "
        "list (for example, after `!recent`, `!find`, `!status`, or `!weekly`)."
    ),
    "done": (
        "`!done <id|number>`\n"
        "Marks an admin item as done. This sets `status=done` and records `completed_at`. The target can be an ID "
        "or a row number from your latest numbered list (for example, after `!recent`, `!find`, `!status`, or "
        "`!weekly`)."
    ),
    "fix": (
        "`!fix <id|number> <field=value> [field=value ...]`\n"
        "Updates allowed fields on an existing note. Quote values containing spaces (for example "
        "`next_action=\"Call dentist\"`). The target can be an ID or a row number from your latest numbered list "
        "(for example, after `!recent`, `!find`, `!status`, or `!weekly`)."
    ),
    "confirm": (
        "`!confirm <pending_id>`\n"
        "Confirms and applies a pending note update."
    ),
    "cancel": (
        "`!cancel <pending_id>`\n"
        "Cancels a pending note update without applying changes."
    ),
    "clear-archive": (
        "`!clear-archive`\n"
        "Starts the destructive archive reset flow. You will lose all notes. Confirm with 'DELETE'."
    ),
}
_NL_ROUTE_MEDIUM_CONFIDENCE = 0.5
_NL_INTENT_SCHEMA_PATH = Path("config/schemas/nl_route_intent_v1.json")
_NL_MUTATION_NORMALIZED_SCHEMA_PATH = Path("config/schemas/nl_mutation_normalized_v1.json")
_NL_CLARIFICATION_TTL_SECONDS = 600
_NL_OUT_OF_SCOPE_CLARIFICATION_COPY = (
    "Before I can proceed with any other actions, I need clarification on the unresolved parts of the previous request. "
    "You may cancel your last action if you'd like to take a new action now."
)
_NL_ROUTES = {
    "read_command",
    "mutation_plan",
    "clarify",
    "capture_fallthrough",
    "blocked_explicit_only",
}
_NL_INTENTS = {
    "status",
    "weekly",
    "recent",
    "find",
    "show",
    "done",
    "append",
    "fix",
    "clear_archive",
    "confirm_pending",
    "cancel_pending",
    "none",
}
_NL_EXPLICIT_ONLY_INTENTS = {"clear_archive", "confirm_pending", "cancel_pending"}
_NL_READ_INTENTS = {"status", "weekly", "recent", "find", "show"}
_NL_MUTATION_INTENTS = {"done", "append", "fix"}
_NL_COMMAND_FOR_INTENT = {
    "status": "!status",
    "weekly": "!weekly",
    "recent": "!recent",
    "find": "!find",
    "show": "!show",
    "done": "!done",
    "append": "!append",
    "fix": "!fix",
    "clear_archive": "!clear-archive",
    "confirm_pending": "!confirm",
    "cancel_pending": "!cancel",
}
_NL_MUTATION_ACTIONS = {"mark_done", "append_body", "set_fields"}
_WEEKDAY_MAP = {
    "MON": 0,
    "MONDAY": 0,
    "TUE": 1,
    "TUESDAY": 1,
    "WED": 2,
    "WEDNESDAY": 2,
    "THU": 3,
    "THURSDAY": 3,
    "FRI": 4,
    "FRIDAY": 4,
    "SAT": 5,
    "SATURDAY": 5,
    "SUN": 6,
    "SUNDAY": 6,
}
_FIX_IMMUTABLE_FIELDS = {
    "id",
    "type",
    "created_at",
    "updated_at",
    "source_event_ids",
    "last_decision_id",
}
_FIX_ALLOWED_FIELDS = {
    "admin": {
        "title",
        "status",
        "next_action",
        "due_date",
        "due_at",
        "priority",
        "blocked_reason",
        "completed_at",
        "gcal_event_id",
    },
    "projects": {
        "title",
        "status",
        "next_action",
        "goal",
        "due",
        "blocked_reason",
    },
    "people": {
        "title",
        "name",
        "context",
        "follow_ups",
        "last_contacted",
        "next_contact",
    },
    "ideas": {
        "title",
        "one_liner",
        "status",
        "next_step",
    },
}
_FIX_ENUM_VALUES = {
    ("admin", "status"): {"open", "done", "blocked"},
    ("admin", "priority"): {"low", "normal", "high"},
    ("projects", "status"): {"planning", "in_progress", "blocked", "completed", "on_hold"},
    ("ideas", "status"): {"seed", "incubating", "active", "parked", "done"},
}
_FIX_DATE_FIELDS = {
    ("admin", "due_date"),
    ("people", "last_contacted"),
    ("people", "next_contact"),
}
_FIX_DATETIME_FIELDS = {
    ("admin", "due_at"),
    ("admin", "completed_at"),
}


@dataclass(frozen=True)
class _ResultCursor:
    object_ids: list[str]
    expires_at: datetime
    source_view: str = "unknown"


@dataclass(frozen=True)
class _CommandTargetResolution:
    target_id: str | None
    error: str | None
    reason: str | None
    row_number: int | None
    source_view: str | None


@dataclass(frozen=True)
class _NLRouteIntentV1:
    route: str
    intent: str
    risk_tier: str
    confidence: float
    ambiguities: list[str]
    read_command: dict[str, Any] | None
    mutation_plan: dict[str, Any] | None
    clarification: dict[str, Any] | None


_RESULT_CURSORS: dict[tuple[int, int], _ResultCursor] = {}


@dataclass(frozen=True)
class _AffinityTouch:
    object_id: str
    touched_at: datetime


_MATCHING_AFFINITY: dict[tuple[int, int], list[_AffinityTouch]] = {}


@dataclass(frozen=True)
class _ArchiveClearConfirmation:
    expires_at: datetime


_ARCHIVE_CLEAR_CONFIRMATIONS: dict[tuple[int, int], _ArchiveClearConfirmation] = {}


@dataclass(frozen=True)
class _NLClarificationContext:
    raw_event_id: str
    expires_at: datetime
    unresolved_scope: dict[str, dict[str, Any]]
    base_plan_input: dict[str, Any]


_NL_CLARIFICATION_CONTEXTS: dict[tuple[int, int], _NLClarificationContext] = {}


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            return int(trimmed)
    return None


def _parse_positive_int(value: str) -> int | None:
    trimmed = value.strip()
    if not trimmed.isdigit():
        return None
    parsed = int(trimmed)
    if parsed <= 0:
        return None
    return parsed


def _normalize_help_topic(value: str) -> str:
    topic = value.strip().lower()
    if topic.startswith("!"):
        topic = topic[1:]
    topic = topic.replace("_", "-")
    if topic == "cleararchive":
        return "clear-archive"
    return topic


def _coerce_non_empty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed


def _coerce_positive_int(value: Any) -> int | None:
    parsed = _coerce_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _looks_like_recent_request(content: str, args: dict[str, Any]) -> bool:
    normalized = " ".join(content.strip().lower().split())
    if not normalized:
        return False
    direct_phrases = {
        "show my notes",
        "show me my notes",
        "show all my notes",
        "list my notes",
        "recent notes",
    }
    if normalized in direct_phrases:
        return True
    if "my notes" in normalized or "all my notes" in normalized or "recent notes" in normalized:
        return True
    if re.search(r"\blast\s+\d+\s+notes?\b", normalized):
        return True

    query = _coerce_non_empty_str(args.get("query")) or _coerce_non_empty_str(args.get("text"))
    if query:
        query_norm = " ".join(query.strip().lower().split())
        if query_norm in {"my notes", "all my notes", "recent notes"}:
            return True
    return False


def _confidence_band(confidence: float, threshold: float) -> str:
    if confidence >= threshold:
        return "high"
    if confidence >= _NL_ROUTE_MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def _log_nl_route_evaluated(
    *,
    raw_event_id: str,
    route_result: str,
    intent: str,
    risk_tier: str,
    confidence_band: str,
    mapped_command: str | None,
) -> None:
    logging.info(
        "nl_route_evaluated raw_event_id=%s route_result=%s intent=%s risk_tier=%s confidence_band=%s mapped_command=%s",
        raw_event_id,
        route_result,
        intent,
        risk_tier,
        confidence_band,
        mapped_command or "",
    )


def _log_nl_route_clarified(*, raw_event_id: str, options: list[str]) -> None:
    logging.info(
        "nl_route_clarified raw_event_id=%s options=%s",
        raw_event_id,
        "|".join(option.strip() for option in options if option.strip()),
    )


def _log_nl_route_blocked(*, raw_event_id: str, intent: str, reason: str) -> None:
    logging.info(
        "nl_route_blocked raw_event_id=%s intent=%s reason=%s",
        raw_event_id,
        intent,
        reason,
    )


def _default_risk_tier(intent: str) -> str:
    if intent in _NL_READ_INTENTS:
        return "read"
    if intent in _NL_MUTATION_INTENTS:
        return "mutation"
    if intent == "clear_archive":
        return "destructive"
    if intent in {"confirm_pending", "cancel_pending"}:
        return "control"
    return "none"


def _normalize_nl_route_payload(payload: dict[str, Any]) -> _NLRouteIntentV1:
    route = _coerce_non_empty_str(payload.get("route")) or "capture_fallthrough"
    route = route.lower()
    if route not in _NL_ROUTES:
        route = "capture_fallthrough"

    intent = _coerce_non_empty_str(payload.get("intent")) or "none"
    intent = intent.lower()
    if intent not in _NL_INTENTS:
        intent = "none"

    risk_tier = _coerce_non_empty_str(payload.get("risk_tier")) or _default_risk_tier(intent)
    risk_tier = risk_tier.lower()
    if risk_tier not in {"read", "mutation", "destructive", "control", "none"}:
        risk_tier = _default_risk_tier(intent)

    confidence_raw = payload.get("confidence")
    confidence = 0.0
    if isinstance(confidence_raw, (int, float)):
        confidence = max(0.0, min(1.0, float(confidence_raw)))

    raw_ambiguities = payload.get("ambiguities")
    ambiguities = [item.strip() for item in raw_ambiguities if isinstance(item, str) and item.strip()] if isinstance(raw_ambiguities, list) else []

    read_command_raw = payload.get("read_command")
    read_command = read_command_raw if isinstance(read_command_raw, dict) else None

    mutation_plan_raw = payload.get("mutation_plan")
    mutation_plan = mutation_plan_raw if isinstance(mutation_plan_raw, dict) else None

    clarification_raw = payload.get("clarification")
    clarification = clarification_raw if isinstance(clarification_raw, dict) else None

    return _NLRouteIntentV1(
        route=route,
        intent=intent,
        risk_tier=risk_tier,
        confidence=confidence,
        ambiguities=ambiguities,
        read_command=read_command,
        mutation_plan=mutation_plan,
        clarification=clarification,
    )


def _extract_nl_target_token_from_target_ref(target_ref: dict[str, Any]) -> str | None:
    kind = _coerce_non_empty_str(target_ref.get("kind"))
    if kind is None:
        return None
    kind = kind.lower()
    value = target_ref.get("value")
    if kind == "row_number":
        number = _coerce_positive_int(value)
        if number is None:
            return None
        return str(number)
    if kind == "object_id":
        target = _coerce_non_empty_str(value)
        if target is None:
            return None
        return target
    return None


def _normalize_nl_raw_user_phrases(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key).strip(): value.strip()
        for key, value in payload.items()
        if str(key).strip() and isinstance(value, str) and value.strip()
    }


def _normalize_nl_mutation_plan_input(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    raw_operations = payload.get("operations")
    operation_items: list[Any]
    if isinstance(raw_operations, list):
        operation_items = raw_operations
    else:
        # Legacy single-operation payload fallback.
        operation_items = [
            {
                "operation_id": "op_1",
                "action_type": payload.get("action_type"),
                "target_refs": [payload.get("target_ref")] if isinstance(payload.get("target_ref"), dict) else [],
                "field_updates": payload.get("field_updates"),
                "append_text": payload.get("append_text"),
                "raw_user_phrases": payload.get("raw_user_phrases"),
                "confidence": payload.get("confidence"),
                "requires_clarification": payload.get("requires_clarification"),
                "clarification_reason": payload.get("clarification_reason"),
            }
        ]

    normalized_operations: list[dict[str, Any]] = []
    unresolved_reasons: list[str] = []
    for index, raw_operation in enumerate(operation_items, start=1):
        if not isinstance(raw_operation, dict):
            continue
        operation_id = _coerce_non_empty_str(raw_operation.get("operation_id")) or f"op_{index}"
        action_type = _coerce_non_empty_str(raw_operation.get("action_type"))
        if action_type is None:
            unresolved_reasons.append("I can apply that update, but I need the action type.")
            continue
        action_type = action_type.lower()
        if action_type not in _NL_MUTATION_ACTIONS:
            unresolved_reasons.append("I couldn't map that to a supported mutation action.")
            continue

        raw_target_refs = raw_operation.get("target_refs")
        if not isinstance(raw_target_refs, list):
            raw_target_ref = raw_operation.get("target_ref")
            if isinstance(raw_target_ref, dict):
                raw_target_refs = [raw_target_ref]
            else:
                raw_target_refs = []
        target_refs: list[dict[str, Any]] = []
        for raw_target_ref in raw_target_refs:
            if not isinstance(raw_target_ref, dict):
                continue
            target_token = _extract_nl_target_token_from_target_ref(raw_target_ref)
            if target_token is None:
                continue
            target_refs.append({"target_ref": raw_target_ref, "target_token": target_token})
        if not target_refs:
            unresolved_reasons.append("I can apply that update, but I need which item to target.")
            continue

        field_updates = raw_operation.get("field_updates")
        if not isinstance(field_updates, list):
            field_updates = []

        append_text = _coerce_non_empty_str(raw_operation.get("append_text"))
        operation_confidence_raw = raw_operation.get("confidence")
        operation_confidence = 0.0
        if isinstance(operation_confidence_raw, (int, float)):
            operation_confidence = max(0.0, min(1.0, float(operation_confidence_raw)))
        requires_clarification_raw = raw_operation.get("requires_clarification")
        requires_clarification = (
            bool(requires_clarification_raw) if isinstance(requires_clarification_raw, bool) else False
        )
        clarification_reason = _coerce_non_empty_str(raw_operation.get("clarification_reason"))
        raw_user_phrases = _normalize_nl_raw_user_phrases(raw_operation.get("raw_user_phrases"))

        normalized_operations.append(
            {
                "operation_id": operation_id,
                "action_type": action_type,
                "target_refs": target_refs,
                "field_updates": field_updates,
                "append_text": append_text,
                "raw_user_phrases": raw_user_phrases,
                "confidence": operation_confidence,
                "requires_clarification": requires_clarification,
                "clarification_reason": clarification_reason,
            }
        )

    if not normalized_operations:
        return None, unresolved_reasons[0] if unresolved_reasons else "I can apply that update, but I need more detail."

    plan_confidence_raw = payload.get("confidence")
    plan_confidence = 0.0
    if isinstance(plan_confidence_raw, (int, float)):
        plan_confidence = max(0.0, min(1.0, float(plan_confidence_raw)))

    object_type_hint = _coerce_non_empty_str(payload.get("object_type_hint"))
    if object_type_hint:
        object_type_hint = object_type_hint.lower()
    if object_type_hint not in {"admin", "projects", "people", "ideas"}:
        object_type_hint = None

    requires_clarification_raw = payload.get("requires_clarification")
    requires_clarification = bool(requires_clarification_raw) if isinstance(requires_clarification_raw, bool) else False
    clarification_reason = _coerce_non_empty_str(payload.get("clarification_reason"))
    if any(bool(op.get("requires_clarification")) for op in normalized_operations):
        requires_clarification = True

    return (
        {
            "operations": normalized_operations,
            "confidence": plan_confidence,
            "requires_clarification": requires_clarification,
            "clarification_reason": clarification_reason,
            "object_type_hint": object_type_hint,
            "raw_user_phrases": _normalize_nl_raw_user_phrases(payload.get("raw_user_phrases")),
        },
        None,
    )


def _build_nl_read_command(
    *,
    intent: str,
    args: dict[str, Any],
    routing: NLCommandRoutingConfig,
) -> tuple[str | None, str | None]:
    if intent == "status":
        return "!status", None
    if intent == "weekly":
        return "!weekly", None
    if intent == "recent":
        limit = _coerce_positive_int(args.get("recent_limit"))
        if limit is None:
            limit = _coerce_positive_int(args.get("number"))
        if limit is None:
            return "!recent", None
        clamped = max(1, min(routing.max_recent_limit, limit))
        return f"!recent {clamped}", None
    if intent == "find":
        query = _coerce_non_empty_str(args.get("query"))
        if query is None:
            query = _coerce_non_empty_str(args.get("text"))
        if query is None:
            return None, "I can run search, but I need what to search for."
        return f"!find {query}", None
    if intent == "show":
        number = _coerce_positive_int(args.get("number"))
        if number is None:
            target = _coerce_non_empty_str(args.get("target"))
            if target and target.isdigit():
                number = _coerce_positive_int(target)
        if number is None:
            return None, "I can open an item, but I need a list number."
        return f"!show {number}", None
    return None, "I couldn't map that to a supported read command."


def _format_nl_clarification_message(
    *,
    question: str | None,
    options: list[str],
    fallback: str,
) -> str:
    prompt = question.strip() if isinstance(question, str) and question.strip() else fallback
    cleaned_options = [option.strip() for option in options if isinstance(option, str) and option.strip()]
    if not cleaned_options:
        return prompt
    lines = [prompt, "", "Options:"]
    for option in cleaned_options[:3]:
        lines.append(f"- {option}")
    return "\n".join(lines)


def _explicit_only_guidance_for_intent(intent: str) -> str:
    if intent == "clear_archive":
        return "Clearing the archive can only be done explicitly with the command `!clear-archive`."
    if intent == "confirm_pending":
        return "Pending confirmation is explicit-only. Use `!confirm <pending_id>` or the Confirm button."
    if intent == "cancel_pending":
        return "Pending cancellation is explicit-only. Use `!cancel <pending_id>` or the Cancel button."
    return "That action requires an explicit command."


def _log_nl_plan_generated(*, raw_event_id: str, action_type: str) -> None:
    logging.info("nl_plan_generated raw_event_id=%s action_type=%s", raw_event_id, action_type)


def _log_nl_plan_normalized(*, raw_event_id: str, action_type: str, target_id: str, object_type: str) -> None:
    logging.info(
        "nl_plan_normalized raw_event_id=%s action_type=%s target_id=%s object_type=%s",
        raw_event_id,
        action_type,
        target_id,
        object_type,
    )


def _log_nl_plan_clarified(*, raw_event_id: str, reason_code: str) -> None:
    logging.info("nl_plan_clarified raw_event_id=%s reason_code=%s", raw_event_id, reason_code)


def _log_nl_plan_blocked(*, raw_event_id: str, reason_code: str) -> None:
    logging.info("nl_plan_blocked raw_event_id=%s reason_code=%s", raw_event_id, reason_code)


def _log_nl_plan_pending_created(*, raw_event_id: str, pending_id: str, action_type: str) -> None:
    logging.info(
        "nl_plan_pending_created raw_event_id=%s pending_id=%s action_type=%s",
        raw_event_id,
        pending_id,
        action_type,
    )


def _log_nl_plan_confirm_applied(*, pending_id: str) -> None:
    logging.info("nl_plan_confirm_applied pending_id=%s", pending_id)


def _log_nl_plan_unresolved_cancelled(*, raw_event_id: str, count: int) -> None:
    logging.info("nl_plan_unresolved_cancelled raw_event_id=%s count=%s", raw_event_id, count)


def _log_nl_clarification_scope_blocked(*, raw_event_id: str) -> None:
    logging.info("nl_clarification_scope_blocked raw_event_id=%s", raw_event_id)


def _normalize_enum_value(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _value_contains_time_hint(value: str) -> bool:
    text = value.strip().lower()
    if not text:
        return False
    if re.search(r"\b\d{1,2}:\d{2}\b", text):
        return True
    if re.search(r"\b\d{1,2}\s*(am|pm)\b", text):
        return True
    for keyword in ("morning", "afternoon", "evening", "tonight", "noon", "midnight"):
        if keyword in text:
            return True
    return False


def _month_number(token: str) -> int | None:
    month = token.strip().lower()
    mapping = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return mapping.get(month)


def _parse_time_text(value: str) -> time | None:
    text = value.strip().lower()
    if not text:
        return None
    if text in {"noon"}:
        return time(hour=12, minute=0)
    if text in {"midnight"}:
        return time(hour=0, minute=0)
    match = re.match(r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?$", text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute_text = match.group("minute")
    minute = int(minute_text) if minute_text is not None else 0
    ampm = match.group("ampm")
    if minute < 0 or minute > 59:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    else:
        if hour < 0 or hour > 23:
            return None
    return time(hour=hour, minute=minute)


def _parse_date_text(value: str, *, now: datetime) -> date | None:
    text = value.strip()
    if not text:
        return None
    lower = text.lower()
    if lower == "today":
        return now.date()
    if lower == "tomorrow":
        return now.date() + timedelta(days=1)

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    slash = re.match(r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{4}))?$", text)
    if slash:
        month = int(slash.group("month"))
        day = int(slash.group("day"))
        year = int(slash.group("year")) if slash.group("year") else now.year
        try:
            return date(year, month, day)
        except ValueError:
            return None

    words = re.match(r"^(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,?\s*(?P<year>\d{4}))?$", text)
    if words:
        month = _month_number(words.group("month"))
        day = int(words.group("day"))
        year = int(words.group("year")) if words.group("year") else now.year
        if month is None:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None

    weekday = _WEEKDAY_MAP.get(lower.upper())
    if weekday is not None:
        days_ahead = (weekday - now.weekday()) % 7
        return now.date() + timedelta(days=days_ahead)

    return None


def _parse_datetime_text(value: str, *, now: datetime, tz: tzinfo) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed
    except ValueError:
        pass

    split = re.match(r"^(?P<date_part>.+?)\s+at\s+(?P<time_part>.+)$", text, flags=re.IGNORECASE)
    if not split:
        split = re.match(r"^(?P<date_part>.+?)\s+(?P<time_part>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)$", text, flags=re.IGNORECASE)
    if not split:
        return None

    date_part = split.group("date_part").strip()
    time_part = split.group("time_part").strip()
    parsed_date = _parse_date_text(date_part, now=now)
    parsed_time = _parse_time_text(time_part)
    if parsed_date is None or parsed_time is None:
        return None
    return datetime.combine(parsed_date, parsed_time, tzinfo=tz)


def _normalize_value_for_field(
    *,
    object_type: str,
    field_id: str,
    value_text: str,
    now: datetime,
    tz: tzinfo,
) -> tuple[str | None, str | None]:
    raw = value_text.strip()
    if not raw:
        return None, "value_parse_failed"

    enum_values = _FIX_ENUM_VALUES.get((object_type, field_id))
    if enum_values:
        normalized = _normalize_enum_value(raw)
        if (object_type, field_id) == ("admin", "priority"):
            if normalized == "urgent":
                normalized = "high"
            if normalized == "medium":
                normalized = "normal"
        if normalized not in enum_values:
            return None, "value_parse_failed"
        return normalized, None

    if (object_type, field_id) in _FIX_DATE_FIELDS:
        parsed = _parse_date_text(raw, now=now)
        if parsed is None:
            return None, "value_parse_failed"
        return parsed.isoformat(), None

    if (object_type, field_id) in _FIX_DATETIME_FIELDS:
        parsed = _parse_datetime_text(raw, now=now, tz=tz)
        if parsed is None:
            return None, "value_parse_failed"
        return parsed.isoformat(), None

    if (object_type, field_id) == ("projects", "due"):
        if _value_contains_time_hint(raw):
            parsed_dt = _parse_datetime_text(raw, now=now, tz=tz)
            if parsed_dt is None:
                return None, "value_parse_failed"
            return parsed_dt.isoformat(), None
        parsed_date = _parse_date_text(raw, now=now)
        if parsed_date is None:
            parsed_dt = _parse_datetime_text(raw, now=now, tz=tz)
            if parsed_dt is None:
                return None, "value_parse_failed"
            return parsed_dt.isoformat(), None
        return parsed_date.isoformat(), None

    return raw, None


def _collect_field_ids_from_candidates(update: dict[str, Any]) -> list[str]:
    field_ids: list[str] = []
    candidates_raw = update.get("field_candidates")
    if not isinstance(candidates_raw, dict):
        return field_ids

    primary = candidates_raw.get("primary")
    if isinstance(primary, dict):
        primary_id = _coerce_non_empty_str(primary.get("field_id"))
        if primary_id:
            field_ids.append(primary_id.lower())

    alternates = candidates_raw.get("alternates")
    if isinstance(alternates, list):
        for item in alternates:
            if not isinstance(item, dict):
                continue
            candidate_id = _coerce_non_empty_str(item.get("field_id"))
            if candidate_id:
                field_ids.append(candidate_id.lower())
    return field_ids


def _resolve_field_for_update(
    *,
    object_type: str,
    update: dict[str, Any],
) -> tuple[str | None, str | None, list[str]]:
    allowed = _FIX_ALLOWED_FIELDS.get(object_type, set())
    notes: list[str] = []
    candidate_ids = _collect_field_ids_from_candidates(update)
    resolved: list[str] = []

    for candidate_id in candidate_ids:
        if candidate_id in allowed and candidate_id not in resolved:
            resolved.append(candidate_id)

    value_text = _coerce_non_empty_str(update.get("value_text")) or ""

    if not resolved:
        return None, "field_unknown", notes
    if len(resolved) == 1:
        return resolved[0], None, notes
    if {"due_date", "due_at"}.issubset(set(resolved)):
        due_choice = "due_at" if _value_contains_time_hint(value_text) else "due_date"
        notes.append(f"due_choice:{due_choice}")
        return due_choice, None, notes
    return None, "field_ambiguous", notes


def _normalize_set_fields(
    *,
    object_type: str,
    field_updates: list[Any],
    routing: NLCommandRoutingConfig,
    now: datetime,
    tz: tzinfo,
) -> tuple[dict[str, str] | None, str | None, list[str]]:
    normalized: dict[str, str] = {}
    notes: list[str] = []
    if not field_updates:
        return None, "field_unknown", notes
    for raw_update in field_updates:
        if not isinstance(raw_update, dict):
            continue
        field_id, field_error, field_notes = _resolve_field_for_update(
            object_type=object_type,
            update=raw_update,
        )
        notes.extend(field_notes)
        if field_error:
            return None, field_error, notes
        if field_id is None:
            return None, "field_unknown", notes

        value_text = _coerce_non_empty_str(raw_update.get("value_text"))
        if value_text is None:
            return None, "value_parse_failed", notes
        normalized_value, value_error = _normalize_value_for_field(
            object_type=object_type,
            field_id=field_id,
            value_text=value_text,
            now=now,
            tz=tz,
        )
        if value_error or normalized_value is None:
            return None, value_error or "value_parse_failed", notes
        normalized[field_id] = normalized_value

    if not normalized:
        return None, "field_unknown", notes
    validated, validation_error = _validate_fix_updates(object_type, normalized)
    if validation_error:
        notes.append(f"validation_error:{validation_error}")
        return None, "validation_failed", notes
    if validated is None:
        return None, "validation_failed", notes
    return {key: str(value) for key, value in validated.items()}, None, notes


def _clarification_for_plan_reason(reason_code: str, *, object_type: str | None = None) -> tuple[str, list[str]]:
    if reason_code in {"target_missing", "target_out_of_range"}:
        return (
            "I couldn't resolve which note to update.",
            ["Share the row number from your latest list", "Use the full note ID", "Run `!recent` first"],
        )
    if reason_code == "field_ambiguous":
        return (
            "I need one more detail before applying that update.",
            ["Use `due date` for day-only updates", "Use `due at` when a time is included", "Tell me the exact field name"],
        )
    if reason_code == "field_unknown":
        suffix = f" for {object_type}" if object_type else ""
        return (
            f"I couldn't determine a valid field{suffix}.",
            ["Tell me the exact field name", "Use a due/date phrasing", "Use `!fix <id|number> <field=value>`"],
        )
    if reason_code == "value_parse_failed":
        return (
            "I couldn't parse that value safely.",
            ["Use `YYYY-MM-DD` for date fields", "Use `YYYY-MM-DDTHH:MM:SS+00:00` for datetime fields", "Try simpler wording"],
        )
    if reason_code == "validation_failed":
        return (
            "That update doesn't match allowed values for this note.",
            ["Use one of the allowed enum values", "Use ISO date/time formats", "Update a different field"],
        )
    if reason_code == "append_text_missing":
        return (
            "I can append to that note, but I need the text to append.",
            ["Include the text to add", "Target a specific note ID", "Use `!append <id|number> <text>`"],
        )
    return (
        "I need a bit more detail before applying that update.",
        ["Specify the target note", "Specify the field/value", "Use an explicit mutation command"],
    )


def _write_nl_mutation_normalized_trace(*, config: dict[str, Any], raw_event_id: str, payload: dict[str, Any]) -> None:
    derived_root = Path(config.get("paths", {}).get("events_derived", "events/derived"))
    try:
        schema = load_json_schema(_NL_MUTATION_NORMALIZED_SCHEMA_PATH)
        validate_json(schema, payload)
        write_derived_event(
            derived=payload,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="nl_mutation_normalized",
        )
    except Exception as exc:
        logging.warning("nl_mutation_normalized_write_failed id=%s error=%s", raw_event_id, exc)


def _is_iso_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def _is_iso_datetime(value: str, *, require_timezone: bool) -> bool:
    if "T" not in value:
        return False
    candidate = value
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    if require_timezone and parsed.tzinfo is None:
        return False
    return True


def _validate_fix_updates(object_type: str, updates: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    allowed_fields = _FIX_ALLOWED_FIELDS.get(object_type)
    if not allowed_fields:
        return None, f"Unsupported object type for !fix: {object_type}"

    validated: dict[str, Any] = {}
    for raw_key, raw_value in updates.items():
        key = str(raw_key).strip()
        if not key:
            return None, "Field name cannot be empty."
        if key in _FIX_IMMUTABLE_FIELDS:
            return None, f"Field `{key}` is not editable."
        if key not in allowed_fields:
            allowed = ", ".join(sorted(allowed_fields))
            return None, f"Field `{key}` is not allowed for {object_type}. Allowed fields: {allowed}"
        if not isinstance(raw_value, str):
            return None, f"Field `{key}` must be provided as text."
        value = raw_value.strip()
        if not value:
            return None, f"Field `{key}` cannot be empty."

        enum_values = _FIX_ENUM_VALUES.get((object_type, key))
        if enum_values and value not in enum_values:
            allowed = ", ".join(sorted(enum_values))
            return None, f"Invalid value for `{key}`. Allowed values: {allowed}"

        if (object_type, key) in _FIX_DATE_FIELDS and not _is_iso_date(value):
            return None, f"Invalid value for `{key}`. Use YYYY-MM-DD."
        if (object_type, key) in _FIX_DATETIME_FIELDS and not _is_iso_datetime(value, require_timezone=True):
            return None, f"Invalid value for `{key}`. Use ISO datetime with timezone offset."
        if (object_type, key) == ("projects", "due"):
            if not _is_iso_date(value) and not _is_iso_datetime(value, require_timezone=False):
                return None, "Invalid value for `due`. Use YYYY-MM-DD or ISO datetime."

        validated[key] = value

    if not validated:
        return None, "No valid fields provided."
    return validated, None


def _cursor_key(message: discord.Message) -> tuple[int, int]:
    return (message.author.id, message.channel.id)


def _parent_cursor_key(message: discord.Message) -> tuple[int, int] | None:
    parent_id = getattr(message.channel, "parent_id", None)
    if isinstance(parent_id, int):
        return (message.author.id, parent_id)
    return None


def _archive_clear_key(message: discord.Message) -> tuple[int, int]:
    return (message.author.id, message.channel.id)


def _prune_archive_clear_confirmations(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [key for key, value in _ARCHIVE_CLEAR_CONFIRMATIONS.items() if value.expires_at <= current]
    for key in expired:
        _ARCHIVE_CLEAR_CONFIRMATIONS.pop(key, None)


def _start_archive_clear_confirmation(message: discord.Message) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS)
    _ARCHIVE_CLEAR_CONFIRMATIONS[_archive_clear_key(message)] = _ArchiveClearConfirmation(expires_at=expires_at)
    _prune_archive_clear_confirmations()


def _consume_archive_clear_confirmation(message: discord.Message) -> bool:
    _prune_archive_clear_confirmations()
    key = _archive_clear_key(message)
    confirmation = _ARCHIVE_CLEAR_CONFIRMATIONS.get(key)
    if confirmation is None:
        return False
    _ARCHIVE_CLEAR_CONFIRMATIONS.pop(key, None)
    return True


def _prune_result_cursors(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [key for key, value in _RESULT_CURSORS.items() if value.expires_at <= current]
    for key in expired:
        _RESULT_CURSORS.pop(key, None)


def _record_affinity_touches(
    key: tuple[int, int],
    object_ids: list[str],
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    ttl = timedelta(days=max(1, matching.affinity_ttl_days))
    cutoff = current - ttl
    touches = [touch for touch in _MATCHING_AFFINITY.get(key, []) if touch.touched_at >= cutoff]
    for object_id in object_ids:
        if not isinstance(object_id, str):
            continue
        value = object_id.strip()
        if not value:
            continue
        touches.append(_AffinityTouch(object_id=value, touched_at=current))
    if not touches:
        _MATCHING_AFFINITY.pop(key, None)
        return
    touches = touches[-matching.affinity_recent_ids_per_thread :]
    _MATCHING_AFFINITY[key] = touches


def _load_affinity_scores(
    key: tuple[int, int],
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
) -> dict[str, float]:
    current = now or datetime.now(timezone.utc)
    ttl = timedelta(days=max(1, matching.affinity_ttl_days))
    cutoff = current - ttl
    touches = [touch for touch in _MATCHING_AFFINITY.get(key, []) if touch.touched_at >= cutoff]
    if not touches:
        _MATCHING_AFFINITY.pop(key, None)
        return {}
    _MATCHING_AFFINITY[key] = touches[-matching.affinity_recent_ids_per_thread :]
    scores: dict[str, float] = {}
    for touch in _MATCHING_AFFINITY[key]:
        age = max(0.0, (current - touch.touched_at).total_seconds())
        ttl_seconds = max(1.0, ttl.total_seconds())
        decayed = max(0.0, 1.0 - (age / ttl_seconds))
        existing = scores.get(touch.object_id, 0.0)
        if decayed > existing:
            scores[touch.object_id] = decayed
    return scores


def _store_result_cursor(
    message: discord.Message,
    config: dict[str, Any],
    object_ids: list[str],
    *,
    source_view: str = "unknown",
) -> None:
    surfacing = load_surfacing_config(config)
    ttl_minutes = max(1, surfacing.pull_cursor_ttl_minutes)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    _RESULT_CURSORS[_cursor_key(message)] = _ResultCursor(
        object_ids=list(object_ids),
        expires_at=expires_at,
        source_view=source_view,
    )
    _prune_result_cursors()


def _resolve_result_cursor(message: discord.Message, number: int) -> str | None:
    object_id, _, _ = _resolve_result_cursor_with_reason(message, number)
    return object_id


def _resolve_result_cursor_with_reason(message: discord.Message, number: int) -> tuple[str | None, str | None, str | None]:
    current = datetime.now(timezone.utc)
    keys = [_cursor_key(message)]
    parent_key = _parent_cursor_key(message)
    if parent_key is not None:
        keys.append(parent_key)

    cursor: _ResultCursor | None = None
    saw_expired = False
    for key in keys:
        candidate = _RESULT_CURSORS.get(key)
        if candidate is None:
            continue
        if candidate.expires_at <= current:
            saw_expired = True
            _RESULT_CURSORS.pop(key, None)
            continue
        cursor = candidate
        break

    _prune_result_cursors(current)
    if cursor is None:
        if saw_expired:
            return None, "expired", None
        return None, "missing", None
    index = number - 1
    if index < 0 or index >= len(cursor.object_ids):
        return None, "out_of_range", cursor.source_view
    return cursor.object_ids[index], None, cursor.source_view


def _log_numbered_mutation_resolved(
    *,
    raw_event_id: str,
    command: str,
    source_view: str | None,
    row_number: int,
    object_id: str,
) -> None:
    logging.info(
        "numbered_mutation_resolved raw_event_id=%s command=%s source_view=%s row_number=%s object_id=%s",
        raw_event_id,
        command,
        source_view or "unknown",
        row_number,
        object_id,
    )


def _log_numbered_mutation_resolution_failed(
    *,
    raw_event_id: str,
    command: str,
    reason: str,
    row_number: int,
    source_view: str | None = None,
) -> None:
    logging.info(
        "numbered_mutation_resolution_failed raw_event_id=%s command=%s reason=%s source_view=%s row_number=%s",
        raw_event_id,
        command,
        reason,
        source_view or "unknown",
        row_number,
    )


def _resolve_command_target(message: discord.Message, target_token: str) -> _CommandTargetResolution:
    number = _parse_positive_int(target_token)
    if number is None:
        return _CommandTargetResolution(
            target_id=target_token,
            error=None,
            reason=None,
            row_number=None,
            source_view=None,
        )

    target_id, reason, source_view = _resolve_result_cursor_with_reason(message, number)
    if target_id is not None:
        return _CommandTargetResolution(
            target_id=target_id,
            error=None,
            reason=None,
            row_number=number,
            source_view=source_view,
        )
    if reason == "out_of_range":
        return _CommandTargetResolution(
            target_id=None,
            error="That number is out of range for your last list.",
            reason="out_of_range",
            row_number=number,
            source_view=source_view,
        )
    if reason == "expired":
        return _CommandTargetResolution(
            target_id=None,
            error="Your last numbered list expired. Run `!recent`, `!find`, `!status`, or `!weekly` first.",
            reason="expired",
            row_number=number,
            source_view=None,
        )
    return _CommandTargetResolution(
        target_id=None,
        error="No active numbered list for that command. Run `!recent`, `!find`, `!status`, or `!weekly` first.",
        reason="no_cursor",
        row_number=number,
        source_view=None,
    )


def _prune_nl_clarification_contexts(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired_keys = [key for key, value in _NL_CLARIFICATION_CONTEXTS.items() if value.expires_at <= current]
    for key in expired_keys:
        _NL_CLARIFICATION_CONTEXTS.pop(key, None)


def _load_nl_clarification_context(message: discord.Message) -> _NLClarificationContext | None:
    key = _cursor_key(message)
    context = _NL_CLARIFICATION_CONTEXTS.get(key)
    if context is None:
        return None
    if context.expires_at <= datetime.now(timezone.utc):
        _NL_CLARIFICATION_CONTEXTS.pop(key, None)
        return None
    return context


def _store_nl_clarification_context(
    *,
    message: discord.Message,
    raw_event_id: str,
    unresolved_scope: dict[str, dict[str, Any]],
    base_plan_input: dict[str, Any],
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_NL_CLARIFICATION_TTL_SECONDS)
    _NL_CLARIFICATION_CONTEXTS[_cursor_key(message)] = _NLClarificationContext(
        raw_event_id=raw_event_id,
        expires_at=expires_at,
        unresolved_scope=unresolved_scope,
        base_plan_input=base_plan_input,
    )
    _prune_nl_clarification_contexts()


def _clear_nl_clarification_context(message: discord.Message) -> None:
    _NL_CLARIFICATION_CONTEXTS.pop(_cursor_key(message), None)


def _map_target_resolution_reason_to_plan_reason(reason: str | None) -> str:
    if reason == "out_of_range":
        return "target_out_of_range"
    if reason == "expired":
        return "target_expired"
    if reason == "no_cursor":
        return "target_no_cursor"
    return "target_missing"


def _summarize_unresolved_scope(unresolved_scope: dict[str, dict[str, Any]]) -> str:
    if not unresolved_scope:
        return "Unresolved operations: none."
    parts: list[str] = []
    for operation_id, detail in unresolved_scope.items():
        reason_code = _coerce_non_empty_str(detail.get("reason_code")) or "clarification_insufficient"
        parts.append(f"{operation_id} ({reason_code})")
    return "Unresolved operations: " + ", ".join(parts)


def _number_sections_for_cursor(sections: list[DigestSection]) -> tuple[list[DigestSection], list[str]]:
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
            numbered_lines.append(_format_numbered_row(section.title, line, next_number))
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


def _format_numbered_row(section_title: str, line: str, number: int) -> str:
    parsed = _split_section_row_metadata(section_title, line)
    if parsed is None:
        return f"{number}. {line}"
    title, metadata = parsed
    if not metadata:
        return f"{number}. {title}"
    bullet_lines = "\n".join(f"   • {item}" for item in metadata)
    return f"{number}. {title}\n{bullet_lines}"


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


def _render_numbered_daily_digest_for_command(digest: Any) -> tuple[str, list[str]]:
    if not isinstance(digest, DailyDigest):
        render = getattr(digest, "render", None)
        if callable(render):
            return str(render()), []
        return "", []
    sections, object_ids = _number_sections_for_cursor(digest.sections)
    rendered = DailyDigest(generated_at=digest.generated_at, sections=sections).render()
    if object_ids:
        rendered = f"{rendered}\n\n{_NUMBERED_COMMAND_TIP}"
    return rendered, object_ids


def _render_numbered_weekly_review_for_command(review: Any) -> tuple[str, list[str]]:
    if not isinstance(review, WeeklyReview):
        render = getattr(review, "render", None)
        if callable(render):
            return str(render()), []
        return "", []
    sections, object_ids = _number_sections_for_cursor(review.sections)
    rendered = WeeklyReview(generated_at=review.generated_at, sections=sections).render()
    if object_ids:
        rendered = f"{rendered}\n\n{_NUMBERED_COMMAND_TIP}"
    return rendered, object_ids


def _truncate_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _refresh_index(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
) -> None:
    try:
        rebuild_index(objects_root, index_db)
        logging.info("index_rebuilt path=%s", index_db)
    except Exception as exc:
        logging.exception("index_rebuild_failed error=%s", exc)
        return

    if not matching or matching.semantic_weight <= 0:
        return
    if matching.semantic_provider != "openai":
        logging.warning("semantic_sync_skipped reason=unsupported_provider provider=%s", matching.semantic_provider)
        return
    try:
        provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception as exc:
        logging.warning("semantic_sync_skipped reason=provider_init_failed error=%s", exc)
        return
    try:
        stats = sync_semantic_index(
            objects_root=objects_root,
            db_path=index_db,
            matching_config=matching,
            embedding_provider=provider,
        )
        logging.info(
            "semantic_sync_ok path=%s indexed=%s unchanged=%s removed=%s metadata_reset=%s duration_ms=%s",
            index_db,
            stats.indexed_count,
            stats.unchanged_count,
            stats.removed_count,
            stats.metadata_reset,
            stats.duration_ms,
        )
    except Exception as exc:
        logging.exception("semantic_sync_failed error=%s", exc)


async def _refresh_index_async(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
) -> None:
    await asyncio.to_thread(
        _refresh_index,
        objects_root,
        index_db,
        matching=matching,
    )


async def _candidate_queries_from_llm(
    *,
    provider: OpenAIProvider,
    model: str,
    prompt: str,
    message: str,
) -> list[str]:
    schema_path = Path("config/schemas/candidate_query_v1.json")
    try:
        result = await interpret_text_async(
            provider=provider,
            text=message,
            model=model,
            system_prompt=prompt,
            schema_path=schema_path,
        )
    except Exception as exc:
        logging.warning("candidate_query_failed error=%s", exc)
        return []
    payload = result.derived if isinstance(result.derived, dict) else {}
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return []
    cleaned = []
    for query in queries:
        if not isinstance(query, str):
            continue
        value = query.strip()
        if value:
            cleaned.append(value)
    return cleaned


class _CandidateSelect(discord.ui.Select):
    def __init__(self, view: "PendingActionView", options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose a different target (optional)",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not self._view_ref.is_author(interaction):
            await interaction.response.send_message("This selection is not for you.")
            return
        if not self.values:
            await interaction.response.defer()
            return
        self._view_ref.selected_target_id = self.values[0]
        await interaction.response.defer()


class PendingActionView(discord.ui.View):
    def __init__(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        schema_path: Path,
        author_id: int,
        candidates: list[dict[str, Any]],
        default_target_id: str | None,
        matching: MatchingConfig | None,
        affinity_key: tuple[int, int],
        on_canonical_change: Callable[[], None] | None = None,
        confirm_action: str | None = None,
        selected_target_id: str | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.pending_id = pending_id
        self.pending_root = pending_root
        self.objects_root = objects_root
        self.index_db = index_db
        self.schema_path = schema_path
        self.author_id = author_id
        self.selected_target_id = selected_target_id if selected_target_id else default_target_id
        self._default_target_id = default_target_id
        self._matching = matching
        self._affinity_key = affinity_key
        self._on_canonical_change = on_canonical_change
        self._candidates = [candidate for candidate in candidates if isinstance(candidate, dict)]
        self._confirm_action = confirm_action

        if confirm_action:
            self._render_confirmation()
        else:
            self._render_primary()

    def _render_primary(self) -> None:
        self.clear_items()
        if self._default_target_id and len(self._candidates) > 1:
            options = []
            selected = self.selected_target_id or self._default_target_id
            for candidate in self._candidates[:_SELECT_OPTION_LIMIT]:
                candidate_id = candidate.get("id")
                if not isinstance(candidate_id, str):
                    continue
                title = candidate.get("title")
                label = _truncate_text(str(title or candidate_id), _SELECT_LABEL_LIMIT) or candidate_id
                snippet = _truncate_text(str(candidate.get("snippet") or ""), _SELECT_DESCRIPTION_LIMIT)
                options.append(
                    discord.SelectOption(
                        label=label,
                        value=candidate_id,
                        description=snippet if snippet else None,
                        default=candidate_id == selected,
                    )
                )
            if options:
                self.add_item(_CandidateSelect(self, options))
        self._add_button("Confirm", discord.ButtonStyle.green, self._begin_confirm_update, row=1)
        self._add_button("Create New", discord.ButtonStyle.primary, self._begin_confirm_create_new, row=1)
        self._add_button("Cancel", discord.ButtonStyle.gray, self._begin_confirm_cancel, row=1)

    def _render_confirmation(self) -> None:
        self.clear_items()
        action = self._confirm_action or ""
        if action == "confirm":
            label = "Yes, apply update"
            style = discord.ButtonStyle.green
        elif action == "create_new":
            label = "Yes, create new"
            style = discord.ButtonStyle.primary
        else:
            label = "Yes, cancel (do nothing)"
            style = discord.ButtonStyle.gray
        self._add_button(label, style, self._confirm_selected_action, row=1)
        self._add_button("No, go back", discord.ButtonStyle.secondary, self._restore_primary_actions, row=1)

    def _add_button(
        self,
        label: str,
        style: discord.ButtonStyle,
        handler: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int | None = None,
    ) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)

        async def _callback(interaction: discord.Interaction) -> None:
            await handler(interaction)

        button.callback = _callback  # type: ignore[assignment]
        self.add_item(button)

    def _spawn_view(self, *, confirm_action: str | None) -> "PendingActionView":
        return PendingActionView(
            pending_id=self.pending_id,
            pending_root=self.pending_root,
            objects_root=self.objects_root,
            index_db=self.index_db,
            schema_path=self.schema_path,
            author_id=self.author_id,
            candidates=self._candidates,
            default_target_id=self._default_target_id,
            matching=self._matching,
            affinity_key=self._affinity_key,
            on_canonical_change=self._on_canonical_change,
            confirm_action=confirm_action,
            selected_target_id=self.selected_target_id,
        )

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    async def _begin_confirm_update(self, interaction: discord.Interaction) -> None:
        await self._show_confirmation(interaction, "confirm")

    async def _begin_confirm_create_new(self, interaction: discord.Interaction) -> None:
        await self._show_confirmation(interaction, "create_new")

    async def _begin_confirm_cancel(self, interaction: discord.Interaction) -> None:
        await self._show_confirmation(interaction, "cancel")

    async def _show_confirmation(self, interaction: discord.Interaction, action: str) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This action is not for you.")
            return
        confirm_view = self._spawn_view(confirm_action=action)
        await interaction.response.edit_message(view=confirm_view)

    async def _restore_primary_actions(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This action is not for you.")
            return
        original_view = self._spawn_view(confirm_action=None)
        await interaction.response.edit_message(view=original_view)

    async def _confirm_selected_action(self, interaction: discord.Interaction) -> None:
        action = self._confirm_action
        if action == "confirm":
            await self._apply_pending(interaction)
            return
        if action == "create_new":
            await self._create_new_pending(interaction)
            return
        await self._cancel_pending(interaction)

    async def _apply_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This confirmation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        derived = pending.derived
        ops = derived.get("proposed_operations") or []
        if (
            self.selected_target_id
            and self._default_target_id
            and self.selected_target_id != self._default_target_id
            and isinstance(ops, list)
            and len(ops) == 1
            and isinstance(ops[0], dict)
        ):
            updated_op = dict(ops[0])
            updated_op["target_id"] = self.selected_target_id
            derived = dict(derived)
            derived["proposed_operations"] = [updated_op]
        try:
            result = apply_operations(
                derived,
                objects_root=self.objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=self.schema_path,
                last_decision_id=pending.last_decision_id,
            )
        except Exception as exc:
            logging.exception("pending_apply_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed", derived=derived)
            await interaction.response.send_message("Failed to apply pending action. Check logs for details.")
            return
        await _refresh_index_async(self.objects_root, self.index_db, matching=self._matching)
        if self._on_canonical_change:
            self._on_canonical_change()
        if self._matching:
            touched_ids = _extract_target_ids_from_derived(derived)
            _record_affinity_touches(self._affinity_key, touched_ids, matching=self._matching)
        _write_pending_with_status(self.pending_root, pending, "confirmed", derived=derived)
        fallback_title = _candidate_title(self._candidates, self.selected_target_id)
        await interaction.response.send_message(
            _format_apply_success_message(written_paths=result.written_paths, fallback_title=fallback_title)
        )
        await _disable_view(interaction, clear_pending_instructions=True)

    async def _create_new_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This action is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        derived = _force_create_derived(pending.derived)
        try:
            result = apply_operations(
                derived,
                objects_root=self.objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=self.schema_path,
                last_decision_id=pending.last_decision_id,
            )
        except Exception:
            logging.exception("pending_create_new_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed", derived=derived)
            await interaction.response.send_message("Failed to create a new item. Check logs for details.")
            return
        await _refresh_index_async(self.objects_root, self.index_db, matching=self._matching)
        if self._on_canonical_change:
            self._on_canonical_change()
        if self._matching:
            touched_ids = _extract_ids_from_written_paths(result.written_paths)
            _record_affinity_touches(self._affinity_key, touched_ids, matching=self._matching)
        _write_pending_with_status(self.pending_root, pending, "confirmed", derived=derived)
        title = _first_title_from_paths(result.written_paths)
        if title:
            await interaction.response.send_message(f'Created a new note "{title}".')
        else:
            await interaction.response.send_message(f"Created a new note. ({len(result.written_paths)} item(s) updated.)")
        await _disable_view(interaction, clear_pending_instructions=True)

    async def _cancel_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This cancellation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        _write_pending_with_status(self.pending_root, pending, "cancelled")
        await interaction.response.send_message("Cancelled. No changes made.")
        await _disable_view(interaction, clear_pending_instructions=True)


class MutationPendingView(discord.ui.View):
    def __init__(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        author_id: int,
        matching: MatchingConfig | None,
        affinity_key: tuple[int, int],
        on_canonical_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.pending_id = pending_id
        self.pending_root = pending_root
        self.objects_root = objects_root
        self.index_db = index_db
        self.author_id = author_id
        self._matching = matching
        self._affinity_key = affinity_key
        self._on_canonical_change = on_canonical_change

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This confirmation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        try:
            result = apply_operations(
                pending.derived,
                objects_root=self.objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=None,
                last_decision_id=pending.last_decision_id,
            )
        except Exception:
            logging.exception("nl_mutation_pending_apply_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed")
            await interaction.response.send_message("Failed to apply pending action. Check logs for details.")
            return
        await _refresh_index_async(self.objects_root, self.index_db, matching=self._matching)
        if self._on_canonical_change:
            self._on_canonical_change()
        if self._matching:
            touched_ids = _extract_target_ids_from_derived(pending.derived)
            touched_ids.extend(_extract_ids_from_written_paths(result.written_paths))
            _record_affinity_touches(self._affinity_key, touched_ids, matching=self._matching)
        _write_pending_with_status(self.pending_root, pending, "confirmed")
        _log_nl_plan_confirm_applied(pending_id=self.pending_id)
        await interaction.response.send_message(_format_apply_success_message(written_paths=result.written_paths))
        await _disable_view(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This cancellation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        _write_pending_with_status(self.pending_root, pending, "cancelled")
        await interaction.response.send_message("Cancelled. No changes made.")
        await _disable_view(interaction)


class AutoApplyFeedbackView(discord.ui.View):
    def __init__(self, *, author_id: int, target_id: str) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.target_id = target_id

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    @discord.ui.button(label="Was this incorrect?", style=discord.ButtonStyle.secondary)
    async def incorrect_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This feedback is not for you.")
            return
        await interaction.response.send_message(
            "Sorry about that. Reply with `!fix {id} field=value` or `!append {id} <text>` to correct it.".format(
                id=self.target_id
            )
        )
        await _disable_view(interaction)


def _command_name_for_action_type(action_type: str) -> str:
    mapping = {
        "mark_done": "done",
        "append_body": "append",
        "set_fields": "fix",
    }
    return mapping.get(action_type, "fix")


def _action_phrase_for_entry(entry: dict[str, Any]) -> str:
    action_type = _coerce_non_empty_str(entry.get("action_type")) or "set_fields"
    title = _coerce_non_empty_str(entry.get("target_title")) or _coerce_non_empty_str(entry.get("target_resolved_id")) or "item"
    if action_type == "mark_done":
        return f'Mark "{title}" done'
    if action_type == "append_body":
        return f'Append to "{title}"'
    return f'Update "{title}"'


def _build_nl_trace_operation(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": entry.get("operation_id"),
        "action_type": entry.get("action_type"),
        "target_ref": entry.get("target_ref"),
        "target_token": entry.get("target_token"),
        "target_resolved_id": entry.get("target_resolved_id"),
        "target_object_type": entry.get("target_object_type"),
        "op_status": entry.get("op_status"),
        "reason_code": entry.get("reason_code"),
        "normalization_notes": entry.get("normalization_notes", []),
        "normalized_fields": entry.get("normalized_fields", {}),
        "proposed_operation": entry.get("proposed_operation"),
    }


def _build_unresolved_scope_from_entries(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    scope: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("op_status") != "unresolved":
            continue
        operation_id = _coerce_non_empty_str(entry.get("operation_id"))
        if operation_id is None:
            continue
        target_token = _coerce_non_empty_str(entry.get("target_token"))
        target_tokens: list[str] = []
        if target_token:
            target_tokens.append(target_token)
        existing = scope.get(operation_id)
        if existing is None:
            scope[operation_id] = {
                "action_type": _coerce_non_empty_str(entry.get("action_type")),
                "target_tokens": target_tokens,
                "reason_code": _coerce_non_empty_str(entry.get("reason_code")) or "clarification_insufficient",
            }
            continue
        for token in target_tokens:
            if token not in existing["target_tokens"]:
                existing["target_tokens"].append(token)
    return scope


def _is_clarification_plan_in_scope(
    *,
    clarification_plan_input: dict[str, Any],
    unresolved_scope: dict[str, dict[str, Any]],
) -> bool:
    operations = clarification_plan_input.get("operations")
    if not isinstance(operations, list) or not operations:
        return False
    unresolved_ids = set(unresolved_scope.keys())
    for operation in operations:
        if not isinstance(operation, dict):
            return False
        operation_id = _coerce_non_empty_str(operation.get("operation_id"))
        if operation_id is None or operation_id not in unresolved_ids:
            return False
        scope_entry = unresolved_scope.get(operation_id) or {}
        scope_action = _coerce_non_empty_str(scope_entry.get("action_type"))
        operation_action = _coerce_non_empty_str(operation.get("action_type"))
        if scope_action and operation_action and scope_action != operation_action:
            return False
        scoped_targets = {token for token in scope_entry.get("target_tokens", []) if isinstance(token, str)}
        op_tokens = {
            _coerce_non_empty_str(target_ref.get("target_token"))
            for target_ref in operation.get("target_refs", [])
            if isinstance(target_ref, dict)
        }
        op_tokens.discard(None)
        if scoped_targets and op_tokens and scoped_targets != op_tokens:
            return False
    return True


def _merge_clarification_plan_input(
    *,
    base_plan_input: dict[str, Any],
    clarification_plan_input: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base_plan_input)
    base_operations = base_plan_input.get("operations")
    clarification_operations = clarification_plan_input.get("operations")
    if not isinstance(base_operations, list) or not isinstance(clarification_operations, list):
        return merged
    by_id: dict[str, dict[str, Any]] = {}
    for operation in clarification_operations:
        if not isinstance(operation, dict):
            continue
        operation_id = _coerce_non_empty_str(operation.get("operation_id"))
        if operation_id is None:
            continue
        by_id[operation_id] = operation
    updated_ops: list[dict[str, Any]] = []
    for operation in base_operations:
        if not isinstance(operation, dict):
            continue
        operation_id = _coerce_non_empty_str(operation.get("operation_id"))
        if operation_id and operation_id in by_id:
            updated_ops.append(by_id[operation_id])
        else:
            updated_ops.append(operation)
    merged["operations"] = updated_ops
    return merged


async def _queue_nl_mutation_confirmation(
    *,
    message: discord.Message,
    raw_id: str,
    config: dict[str, Any],
    plan_input: dict[str, Any],
    confidence: float,
    routing: NLCommandRoutingConfig,
    source_view: str | None = None,
    allow_clarification: bool = True,
) -> bool:
    operations = plan_input.get("operations")
    if not isinstance(operations, list) or not operations:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "I can apply that update, but I need which actions to run.")
        return True

    _log_nl_plan_generated(raw_event_id=raw_id, action_type="multi_operation")
    matching_config = load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    tz = resolve_timezone(config.get("timezone"))
    now_local = datetime.now(tz)
    now_iso = _now_iso()
    normalized_entries: list[dict[str, Any]] = []

    for operation in operations:
        if not isinstance(operation, dict):
            continue
        action_type = _coerce_non_empty_str(operation.get("action_type")) or ""
        operation_id = _coerce_non_empty_str(operation.get("operation_id")) or generate_prefixed_id("OP_")
        command_name = _command_name_for_action_type(action_type)
        target_refs = operation.get("target_refs")
        if not isinstance(target_refs, list):
            target_refs = []
        if not target_refs:
            normalized_entries.append(
                {
                    "operation_id": operation_id,
                    "action_type": action_type,
                    "target_ref": None,
                    "target_token": None,
                    "target_resolved_id": None,
                    "target_object_type": None,
                    "op_status": "unresolved",
                    "reason_code": "target_missing",
                    "normalization_notes": ["target_missing"],
                    "normalized_fields": {},
                    "proposed_operation": None,
                    "_command_name": command_name,
                    "_row_number": None,
                    "_source_view": source_view,
                    "target_title": None,
                }
            )
            continue
        for target_ref in target_refs:
            target_token = None
            if isinstance(target_ref, dict):
                target_token = _coerce_non_empty_str(target_ref.get("target_token"))
            entry: dict[str, Any] = {
                "operation_id": operation_id,
                "action_type": action_type,
                "target_ref": target_ref if isinstance(target_ref, dict) else None,
                "target_token": target_token,
                "target_resolved_id": None,
                "target_object_type": None,
                "op_status": "unresolved",
                "reason_code": None,
                "normalization_notes": [],
                "normalized_fields": {},
                "proposed_operation": None,
                "_command_name": command_name,
                "_row_number": None,
                "_source_view": source_view,
                "target_title": None,
            }
            if target_token is None:
                entry["reason_code"] = "target_missing"
                entry["normalization_notes"] = ["target_missing"]
                normalized_entries.append(entry)
                continue

            target_resolution = _resolve_command_target(message, target_token)
            entry["_row_number"] = target_resolution.row_number
            entry["_source_view"] = target_resolution.source_view or source_view
            if target_resolution.reason and target_resolution.row_number is not None:
                _log_numbered_mutation_resolution_failed(
                    raw_event_id=raw_id,
                    command=command_name,
                    reason=target_resolution.reason,
                    source_view=target_resolution.source_view or source_view,
                    row_number=target_resolution.row_number,
                )
            if target_resolution.error:
                entry["reason_code"] = _map_target_resolution_reason_to_plan_reason(target_resolution.reason)
                entry["normalization_notes"] = [target_resolution.error]
                normalized_entries.append(entry)
                continue

            target_id = target_resolution.target_id
            if not target_id:
                entry["reason_code"] = "target_missing"
                entry["normalization_notes"] = ["target_missing"]
                normalized_entries.append(entry)
                continue
            entry["target_resolved_id"] = target_id

            target_path = find_object_path(objects_root, target_id)
            if not target_path:
                entry["reason_code"] = "target_unknown_id"
                entry["normalization_notes"] = [f"unknown_id:{target_id}"]
                normalized_entries.append(entry)
                continue
            frontmatter = load_frontmatter(target_path)
            object_type_value = frontmatter.get("type")
            if not isinstance(object_type_value, str) or not object_type_value.strip():
                entry["reason_code"] = "validation_failed"
                entry["normalization_notes"] = [f"type_missing:{target_id}"]
                normalized_entries.append(entry)
                continue
            object_type = object_type_value.strip()
            entry["target_object_type"] = object_type
            entry["target_title"] = _coerce_non_empty_str(frontmatter.get("title")) or target_id

            normalized_fields: dict[str, str] = {}
            notes: list[str] = []
            reason_code: str | None = None
            proposed_operation: dict[str, Any] | None = None

            if action_type == "mark_done":
                notes.append("action:mark_done")
                if object_type != "admin":
                    reason_code = "target_wrong_type"
                else:
                    normalized_fields = {"status": "done", "completed_at": now_iso}
                    proposed_operation = {
                        "op": "update",
                        "target_id": target_id,
                        "fields": dict(normalized_fields),
                        "object_type": object_type,
                    }
            elif action_type == "append_body":
                append_text = _coerce_non_empty_str(operation.get("append_text"))
                notes.append("action:append_body")
                if append_text is None:
                    reason_code = "value_parse_failed"
                else:
                    normalized_fields = {"body": append_text}
                    proposed_operation = {
                        "op": "append",
                        "target_id": target_id,
                        "fields": dict(normalized_fields),
                        "object_type": object_type,
                    }
            elif action_type == "set_fields":
                field_updates = operation.get("field_updates")
                if not isinstance(field_updates, list):
                    field_updates = []
                normalized_fields, reason_code, notes = _normalize_set_fields(
                    object_type=object_type,
                    field_updates=field_updates,
                    routing=routing,
                    now=now_local,
                    tz=tz,
                )
                if normalized_fields is None:
                    normalized_fields = {}
                if reason_code is None:
                    proposed_operation = {
                        "op": "update",
                        "target_id": target_id,
                        "fields": dict(normalized_fields),
                        "object_type": object_type,
                    }
            else:
                reason_code = "validation_failed"
                notes.append("unsupported_action")

            if reason_code is None and proposed_operation is not None:
                entry["op_status"] = "resolved"
                entry["reason_code"] = None
                entry["normalization_notes"] = notes
                entry["normalized_fields"] = {key: str(value) for key, value in normalized_fields.items()}
                entry["proposed_operation"] = proposed_operation
            else:
                entry["op_status"] = "unresolved"
                entry["reason_code"] = reason_code or "validation_failed"
                entry["normalization_notes"] = notes
                entry["normalized_fields"] = {key: str(value) for key, value in normalized_fields.items()}
            normalized_entries.append(entry)

    # Detect conflicting updates to the same target+field with different values.
    seen_updates: dict[tuple[str, str], tuple[str, int]] = {}
    conflict_indexes: set[int] = set()
    for index, entry in enumerate(normalized_entries):
        if entry.get("op_status") != "resolved":
            continue
        proposed = entry.get("proposed_operation")
        if not isinstance(proposed, dict):
            continue
        if proposed.get("op") != "update":
            continue
        target_id = _coerce_non_empty_str(entry.get("target_resolved_id"))
        fields = proposed.get("fields")
        if target_id is None or not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            key = (target_id, str(field))
            value_text = str(value)
            existing = seen_updates.get(key)
            if existing is None:
                seen_updates[key] = (value_text, index)
                continue
            if existing[0] != value_text:
                conflict_indexes.add(existing[1])
                conflict_indexes.add(index)
    for index in conflict_indexes:
        entry = normalized_entries[index]
        entry["op_status"] = "unresolved"
        entry["reason_code"] = "operation_conflict"
        entry["proposed_operation"] = None

    resolved_entries = [entry for entry in normalized_entries if entry.get("op_status") == "resolved"]
    unresolved_entries = [entry for entry in normalized_entries if entry.get("op_status") == "unresolved"]
    unresolved_scope = _build_unresolved_scope_from_entries(unresolved_entries)

    if unresolved_entries and allow_clarification:
        first_reason = _coerce_non_empty_str(unresolved_entries[0].get("reason_code")) or "clarification_insufficient"
        _log_nl_plan_clarified(raw_event_id=raw_id, reason_code=first_reason)
        if routing.plan_trace_enabled:
            _write_nl_mutation_normalized_trace(
                config=config,
                raw_event_id=raw_id,
                payload={
                    "schema_version": 1,
                    "raw_event_id": raw_id,
                    "plan_input": plan_input,
                    "operations": [_build_nl_trace_operation(entry) for entry in normalized_entries],
                    "summary": {
                        "total_operations": len(normalized_entries),
                        "resolved_count": len(resolved_entries),
                        "unresolved_count": len(unresolved_entries),
                        "cancelled_unresolved_count": 0,
                    },
                    "validation_outcome": "clarify",
                },
            )
        _store_nl_clarification_context(
            message=message,
            raw_event_id=raw_id,
            unresolved_scope=unresolved_scope,
            base_plan_input=plan_input,
        )
        question, options = _clarification_for_plan_reason(first_reason)
        clarification = _format_nl_clarification_message(
            question=question,
            options=options,
            fallback="I need one clarification before I can continue with that request.",
        )
        await _swap_reaction(message, "⏳", "❓")
        await _send_response(
            message,
            "\n".join([clarification, "", _summarize_unresolved_scope(unresolved_scope)]),
        )
        return True

    cancelled_entries: list[dict[str, Any]] = []
    if unresolved_entries:
        for entry in unresolved_entries:
            entry["op_status"] = "cancelled_unresolved"
        cancelled_entries = list(unresolved_entries)
        unresolved_entries = []
        _log_nl_plan_unresolved_cancelled(raw_event_id=raw_id, count=len(cancelled_entries))

    if not resolved_entries:
        _log_nl_plan_blocked(raw_event_id=raw_id, reason_code="clarification_insufficient")
        if routing.plan_trace_enabled:
            _write_nl_mutation_normalized_trace(
                config=config,
                raw_event_id=raw_id,
                payload={
                    "schema_version": 1,
                    "raw_event_id": raw_id,
                    "plan_input": plan_input,
                    "operations": [_build_nl_trace_operation(entry) for entry in normalized_entries],
                    "summary": {
                        "total_operations": len(normalized_entries),
                        "resolved_count": 0,
                        "unresolved_count": 0,
                        "cancelled_unresolved_count": len(cancelled_entries),
                    },
                    "validation_outcome": "blocked",
                },
            )
        lines = ["I couldn't safely apply this request, so no changes were made."]
        if cancelled_entries:
            lines.append("")
            lines.append(_summarize_unresolved_scope(_build_unresolved_scope_from_entries(cancelled_entries)))
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "\n".join(lines))
        return True

    for entry in resolved_entries:
        command_name = _coerce_non_empty_str(entry.get("_command_name")) or "fix"
        row_number = entry.get("_row_number")
        if not isinstance(row_number, int):
            continue
        target_id = _coerce_non_empty_str(entry.get("target_resolved_id"))
        if target_id is None:
            continue
        _log_numbered_mutation_resolved(
            raw_event_id=raw_id,
            command=command_name,
            source_view=_coerce_non_empty_str(entry.get("_source_view")) or source_view,
            row_number=row_number,
            object_id=target_id,
        )

    primary = resolved_entries[0]
    _log_nl_plan_normalized(
        raw_event_id=raw_id,
        action_type=_coerce_non_empty_str(primary.get("action_type")) or "multi_operation",
        target_id=_coerce_non_empty_str(primary.get("target_resolved_id")) or "",
        object_type=_coerce_non_empty_str(primary.get("target_object_type")) or "unknown",
    )

    object_types = {
        _coerce_non_empty_str(entry.get("target_object_type"))
        for entry in resolved_entries
        if _coerce_non_empty_str(entry.get("target_object_type"))
    }
    pending_object_type = "mixed" if len(object_types) > 1 else (next(iter(object_types)) if object_types else "admin")
    proposed_operations = []
    for entry in resolved_entries:
        proposed = entry.get("proposed_operation")
        if isinstance(proposed, dict):
            proposed_operations.append(proposed)

    derived = {
        "object_type": pending_object_type,
        "raw_event_id": raw_id,
        "extracted_fields": {},
        "proposed_operations": proposed_operations,
    }
    pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
    pending_id = generate_prefixed_id("PA_")
    pending = PendingAction(
        schema_version=1,
        pending_action_id=pending_id,
        raw_event_id=raw_id,
        object_type=pending_object_type,
        status="pending",
        created_at=now_iso,
        last_updated=now_iso,
        derived=derived,
        decision={
            "source": "nl_command_routing",
            "intent": "multi_operation",
            "action_type": "multi_operation",
            "confidence": confidence,
        },
        decision_confidence=confidence,
        last_decision_id=None,
    )
    write_pending_action(pending, pending_root)
    _log_nl_plan_pending_created(raw_event_id=raw_id, pending_id=pending_id, action_type="multi_operation")

    outcome = "partial" if cancelled_entries else "ok"
    if routing.plan_trace_enabled:
        _write_nl_mutation_normalized_trace(
            config=config,
            raw_event_id=raw_id,
            payload={
                "schema_version": 1,
                "raw_event_id": raw_id,
                "plan_input": plan_input,
                "operations": [_build_nl_trace_operation(entry) for entry in normalized_entries],
                "summary": {
                    "total_operations": len(normalized_entries),
                    "resolved_count": len(resolved_entries),
                    "unresolved_count": 0,
                    "cancelled_unresolved_count": len(cancelled_entries),
                },
                "validation_outcome": outcome,
            },
        )

    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    view = MutationPendingView(
        pending_id=pending_id,
        pending_root=pending_root,
        objects_root=objects_root,
        index_db=index_db,
        author_id=message.author.id,
        matching=matching_config,
        affinity_key=_cursor_key(message),
        on_canonical_change=lambda: _notify_due_time_reminder_schedule_changed(config),
    )
    lines: list[str] = []
    if len(resolved_entries) == 1:
        lines.append(f'I think you meant to {_action_phrase_for_entry(resolved_entries[0]).lower()}?')
    else:
        lines.append("Please confirm you would like to:")
        for entry in resolved_entries:
            lines.append(f"- {_action_phrase_for_entry(entry)}")
    if cancelled_entries:
        lines.append("")
        lines.append("Cancelled unresolved operations:")
        for operation_id, detail in _build_unresolved_scope_from_entries(cancelled_entries).items():
            reason_code = _coerce_non_empty_str(detail.get("reason_code")) or "clarification_insufficient"
            lines.append(f"- {operation_id} ({reason_code})")
    lines.append("")
    lines.append("Confirm action or cancel to take no action.")
    thread_title = _coerce_non_empty_str(resolved_entries[0].get("target_title")) or _coerce_non_empty_str(
        resolved_entries[0].get("target_resolved_id")
    )
    await _swap_reaction(message, "⏳", "❓")
    await _send_response(
        message,
        "\n".join(lines),
        thread_title=thread_title,
        view=view,
    )
    return True


async def _maybe_route_nl_command(
    *,
    message: discord.Message,
    content: str,
    raw_id: str,
    config: dict[str, Any],
    provider: OpenAIProvider,
    model: str,
) -> bool:
    routing = load_nl_command_routing_config(config)
    if not routing.enabled:
        return False

    llm_config = config.get("llm", {})
    prompt_path = "config/prompts/nl_command_routing_v1.txt"
    if isinstance(llm_config, dict):
        configured = llm_config.get("nl_command_routing_prompt_path")
        if isinstance(configured, str) and configured.strip():
            prompt_path = configured
    try:
        prompt = load_prompt(prompt_path)
    except OSError as exc:
        logging.warning("nl_route_prompt_load_failed path=%s error=%s", prompt_path, exc)
        return False

    try:
        interpretation = await interpret_text_async(
            provider=provider,
            text=content,
            model=model,
            system_prompt=prompt,
            schema_path=_NL_INTENT_SCHEMA_PATH,
        )
    except InterpretationValidationError as exc:
        logging.warning("nl_route_invalid id=%s error=%s", raw_id, exc)
        return False
    except Exception as exc:
        logging.warning("nl_route_failed id=%s error=%s", raw_id, exc)
        return False

    payload = interpretation.derived if isinstance(interpretation.derived, dict) else {}
    route = _normalize_nl_route_payload(payload)
    intent = route.intent
    risk_tier = route.risk_tier
    confidence = route.confidence
    mapped_command = _NL_COMMAND_FOR_INTENT.get(intent)
    clarification_payload = route.clarification or {}
    clarification_question = _coerce_non_empty_str(clarification_payload.get("question"))
    clarification_options_raw = clarification_payload.get("options")
    clarification_options = (
        [item.strip() for item in clarification_options_raw if isinstance(item, str) and item.strip()]
        if isinstance(clarification_options_raw, list)
        else []
    )
    if not clarification_options:
        clarification_options = route.ambiguities[:3]

    if route.route == "read_command":
        threshold = routing.read_auto_min_confidence
    elif route.route == "mutation_plan":
        threshold = routing.mutation_confirm_min_confidence
    else:
        threshold = routing.read_auto_min_confidence if risk_tier == "read" else routing.mutation_confirm_min_confidence
    band = _confidence_band(confidence, threshold)

    clarification_context = _load_nl_clarification_context(message)
    if clarification_context is not None:
        _clear_nl_clarification_context(message)
        if route.route != "mutation_plan":
            _log_nl_clarification_scope_blocked(raw_event_id=raw_id)
            _log_nl_plan_unresolved_cancelled(raw_event_id=raw_id, count=len(clarification_context.unresolved_scope))
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(
                message,
                "\n".join(
                    [
                        _NL_OUT_OF_SCOPE_CLARIFICATION_COPY,
                        "",
                        _summarize_unresolved_scope(clarification_context.unresolved_scope),
                    ]
                ),
            )
            return True

        clarification_raw_plan = route.mutation_plan or {}
        clarification_plan_input, clarification_error = _normalize_nl_mutation_plan_input(clarification_raw_plan)
        in_scope = (
            clarification_plan_input is not None
            and clarification_error is None
            and _is_clarification_plan_in_scope(
                clarification_plan_input=clarification_plan_input,
                unresolved_scope=clarification_context.unresolved_scope,
            )
        )
        if not in_scope:
            _log_nl_clarification_scope_blocked(raw_event_id=raw_id)
            _log_nl_plan_unresolved_cancelled(raw_event_id=raw_id, count=len(clarification_context.unresolved_scope))
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(
                message,
                "\n".join(
                    [
                        _NL_OUT_OF_SCOPE_CLARIFICATION_COPY,
                        "",
                        _summarize_unresolved_scope(clarification_context.unresolved_scope),
                    ]
                ),
            )
            return True

        merged_plan_input = _merge_clarification_plan_input(
            base_plan_input=clarification_context.base_plan_input,
            clarification_plan_input=cast(dict[str, Any], clarification_plan_input),
        )
        _log_nl_route_evaluated(
            raw_event_id=raw_id,
            route_result="clarified",
            intent=intent,
            risk_tier="mutation",
            confidence_band=band,
            mapped_command="nl_mutation_clarification",
        )
        return await _queue_nl_mutation_confirmation(
            message=message,
            raw_id=raw_id,
            config=config,
            plan_input=merged_plan_input,
            confidence=confidence,
            routing=routing,
            source_view="nl_clarification",
            allow_clarification=False,
        )

    if route.route == "capture_fallthrough" or intent == "none":
        _log_nl_route_evaluated(
            raw_event_id=raw_id,
            route_result="fallthrough",
            intent=intent,
            risk_tier=risk_tier,
            confidence_band=band,
            mapped_command=mapped_command,
        )
        return False

    if route.route == "blocked_explicit_only" or intent in _NL_EXPLICIT_ONLY_INTENTS:
        if band == "low":
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="fallthrough",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            return False
        _log_nl_route_blocked(raw_event_id=raw_id, intent=intent, reason="explicit_only")
        _log_nl_route_evaluated(
            raw_event_id=raw_id,
            route_result="blocked_explicit_only",
            intent=intent,
            risk_tier=risk_tier,
            confidence_band=band,
            mapped_command=mapped_command,
        )
        await _swap_reaction(message, "⏳", "❓")
        await _send_response(message, _explicit_only_guidance_for_intent(intent))
        return True

    if route.route == "clarify":
        if routing.clarify_on_ambiguous and band != "low":
            clarification = _format_nl_clarification_message(
                question=clarification_question,
                options=clarification_options,
                fallback="Did you want me to run a command or capture this as a note?",
            )
            _log_nl_route_clarified(raw_event_id=raw_id, options=clarification_options)
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="clarified",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            await _swap_reaction(message, "⏳", "❓")
            await _send_response(message, clarification)
            return True
        _log_nl_route_evaluated(
            raw_event_id=raw_id,
            route_result="fallthrough",
            intent=intent,
            risk_tier=risk_tier,
            confidence_band=band,
            mapped_command=mapped_command,
        )
        return False

    if route.route == "read_command":
        read_command = route.read_command or {}
        read_intent = _coerce_non_empty_str(read_command.get("intent"))
        if read_intent:
            read_intent = read_intent.lower()
        if read_intent not in _NL_READ_INTENTS:
            read_intent = intent if intent in _NL_READ_INTENTS else "none"
        args = read_command.get("args")
        if not isinstance(args, dict):
            args = {}

        if read_intent == "show":
            show_number = _coerce_positive_int(args.get("number"))
            show_target = _coerce_non_empty_str(args.get("target"))
            if show_number is None and (show_target is None or not show_target.isdigit()) and _looks_like_recent_request(content, args):
                read_intent = "recent"
                mapped_command = _NL_COMMAND_FOR_INTENT["recent"]

        command, command_error = _build_nl_read_command(intent=read_intent, args=args, routing=routing)
        if command_error:
            if routing.clarify_on_ambiguous and band != "low":
                clarification = _format_nl_clarification_message(
                    question=clarification_question,
                    options=clarification_options,
                    fallback=command_error or "Did you want me to run a command, or save this as a note?",
                )
                _log_nl_route_clarified(raw_event_id=raw_id, options=clarification_options)
                _log_nl_route_evaluated(
                    raw_event_id=raw_id,
                    route_result="clarified",
                    intent=intent,
                    risk_tier=risk_tier,
                    confidence_band=band,
                    mapped_command=mapped_command,
                )
                await _swap_reaction(message, "⏳", "❓")
                await _send_response(message, clarification)
                return True
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="fallthrough",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            return False
        if band == "high" and command is not None:
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="executed",
                intent=read_intent,
                risk_tier="read",
                confidence_band=band,
                mapped_command=command,
            )
            return await _handle_command(message, command, raw_id, config)
        if routing.clarify_on_ambiguous and band == "medium":
            clarification = _format_nl_clarification_message(
                question=clarification_question,
                options=clarification_options or [f"Run `{command}`", "Save this as a note"],
                fallback="Did you want me to run this command or capture a note?",
            )
            _log_nl_route_clarified(raw_event_id=raw_id, options=clarification_options or [f"Run `{command}`", "Save this as a note"])
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="clarified",
                intent=read_intent,
                risk_tier="read",
                confidence_band=band,
                mapped_command=command,
            )
            await _swap_reaction(message, "⏳", "❓")
            await _send_response(message, clarification)
            return True
        _log_nl_route_evaluated(
            raw_event_id=raw_id,
            route_result="fallthrough",
            intent=read_intent,
            risk_tier="read",
            confidence_band=band,
            mapped_command=command,
        )
        return False

    if route.route == "mutation_plan":
        if not routing.allow_nl_mutations:
            if band == "low":
                _log_nl_route_evaluated(
                    raw_event_id=raw_id,
                    route_result="fallthrough",
                    intent=intent,
                    risk_tier=risk_tier,
                    confidence_band=band,
                    mapped_command=mapped_command,
                )
                return False
            _log_nl_route_blocked(raw_event_id=raw_id, intent=intent, reason="mutations_disabled")
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="blocked_explicit_only",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            await _swap_reaction(message, "⏳", "❓")
            await _send_response(message, "Natural-language mutations are disabled. Use explicit commands like `!done`, `!append`, or `!fix`.")
            return True

        raw_plan = route.mutation_plan or {}
        plan_input, plan_error = _normalize_nl_mutation_plan_input(raw_plan)
        if plan_error:
            if routing.clarify_on_ambiguous and band != "low":
                fallback = "I need more detail before applying that update."
                if isinstance(plan_input, dict):
                    reason_hint = _coerce_non_empty_str(plan_input.get("clarification_reason"))
                    if reason_hint:
                        fallback = reason_hint
                clarification = _format_nl_clarification_message(
                    question=clarification_question,
                    options=clarification_options,
                    fallback=plan_error or fallback,
                )
                _log_nl_route_clarified(raw_event_id=raw_id, options=clarification_options)
                _log_nl_route_evaluated(
                    raw_event_id=raw_id,
                    route_result="clarified",
                    intent=intent,
                    risk_tier=risk_tier,
                    confidence_band=band,
                    mapped_command=mapped_command,
                )
                await _swap_reaction(message, "⏳", "❓")
                await _send_response(message, clarification)
                return True
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="fallthrough",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            return False
        if band == "high" and plan_input is not None:
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="needs_confirmation",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            return await _queue_nl_mutation_confirmation(
                message=message,
                raw_id=raw_id,
                config=config,
                plan_input=plan_input,
                confidence=confidence,
                routing=routing,
            )
        if routing.clarify_on_ambiguous and band == "medium":
            clarification = _format_nl_clarification_message(
                question=clarification_question,
                options=clarification_options,
                fallback="I can apply that update, but I need a bit more detail.",
            )
            _log_nl_route_clarified(raw_event_id=raw_id, options=clarification_options)
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="clarified",
                intent=intent,
                risk_tier=risk_tier,
                confidence_band=band,
                mapped_command=mapped_command,
            )
            await _swap_reaction(message, "⏳", "❓")
            await _send_response(message, clarification)
            return True
        _log_nl_route_evaluated(
            raw_event_id=raw_id,
            route_result="fallthrough",
            intent=intent,
            risk_tier=risk_tier,
            confidence_band=band,
            mapped_command=mapped_command,
        )
        return False

    _log_nl_route_evaluated(
        raw_event_id=raw_id,
        route_result="fallthrough",
        intent=intent,
        risk_tier=risk_tier,
        confidence_band=band,
        mapped_command=mapped_command,
    )
    return False


async def _handle_message(message: discord.Message, config: dict[str, Any]) -> None:
    if message.author.bot:
        return

    content = (message.content or "").strip()
    if not content:
        return
    _prune_nl_clarification_contexts()
    if content == "DELETE":
        handled = await _handle_archive_clear_confirmation(message, config)
        if handled:
            return
    await _safe_add_reaction(message, "⏳")

    raw_dir = Path(config.get("paths", {}).get("events_raw", "events/raw"))
    derived_root = Path(config.get("paths", {}).get("events_derived", "events/derived"))
    raw_id = _generate_raw_id()
    raw_event = RawEvent(
        raw_event_id=raw_id,
        source=Source.discord,
        source_message_id=str(message.id),
        timestamp=message.created_at.isoformat(),
        text=content,
    )
    write_raw_event(raw_event, raw_dir)
    logging.info(
        "raw_event_written id=%s source=discord source_message_id=%s",
        raw_id,
        message.id,
    )

    if content.startswith("!"):
        handled = await _handle_command(message, content, raw_id, config)
        if handled:
            return

    model = config.get("llm", {}).get("interpreter_model")
    if not model:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "No interpreter model configured.")
        return

    provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))

    nl_routed = await _maybe_route_nl_command(
        message=message,
        content=content,
        raw_id=raw_id,
        config=config,
        provider=provider,
        model=model,
    )
    if nl_routed:
        return

    classify_schema = Path("config/schemas/derived_event_classify_v1.json")

    classify_prompt_path = config.get("llm", {}).get("classify_prompt_path")
    extract_prompt_path = config.get("llm", {}).get("interpreter_prompt_path")
    decision_prompt_path = config.get("llm", {}).get("decision_prompt_path")
    candidate_query_prompt_path = config.get("llm", {}).get(
        "candidate_query_prompt_path",
        "config/prompts/candidate_query_v1.txt",
    )
    if not classify_prompt_path or not extract_prompt_path:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Prompt paths are missing. Set llm.classify_prompt_path and llm.interpreter_prompt_path.")
        return

    try:
        classify_prompt = load_prompt(classify_prompt_path)
        extract_prompt = load_prompt(extract_prompt_path)
    except OSError as exc:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Failed to load prompt files: {exc}")
        return
    decision_prompt = None
    candidate_query_prompt = None
    if decision_prompt_path:
        try:
            decision_prompt = load_prompt(decision_prompt_path)
        except OSError as exc:
            logging.warning("decision_prompt_load_failed path=%s error=%s", decision_prompt_path, exc)
    if candidate_query_prompt_path:
        try:
            candidate_query_prompt = load_prompt(candidate_query_prompt_path)
        except OSError as exc:
            logging.warning("candidate_query_prompt_load_failed path=%s error=%s", candidate_query_prompt_path, exc)
    decision_config = load_decision_config(config) if decision_prompt else None
    matching_config = load_matching_config(config)
    decision_payload: dict[str, Any] | None = None
    decision_artifact_id: str | None = None
    matching_trace: dict[str, Any] | None = None

    tz_name = config.get("timezone")
    tz = resolve_timezone(tz_name)
    reference = (
        f"Reference date: {format_reference_date(tz)}. "
        f"Reference weekday: {format_reference_weekday(tz)}. "
        f"Reference time: {format_reference_time(tz)}."
    )
    extract_prompt = f"{extract_prompt} {reference}"

    try:
        classification = await interpret_text_async(
            provider=provider,
            text=content,
            model=model,
            system_prompt=classify_prompt,
            schema_path=classify_schema,
        )
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.warning("classification_invalid id=%s error=%s", raw_id, exc)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "I couldn't parse that reliably. Please rephrase or use a prefix.")
        return
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.exception("classification_failed id=%s", raw_id)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Interpretation failed. Please try again.")
        return

    write_derived_event(
        derived=classification.derived,
        raw_text=classification.raw_text,
        derived_root=derived_root,
        raw_event_id=raw_id,
        label="classify",
    )
    logging.info(
        "classification_ok id=%s object_type=%s confidence=%.2f",
        raw_id,
        classification.derived.get("object_type"),
        classification.derived.get("confidence", 0),
    )

    object_type = classification.derived.get("object_type")
    confidence = classification.derived.get("confidence", 0)
    threshold = config.get("confidence", {}).get("create_threshold", 0.6)

    if not isinstance(object_type, str) or object_type == "unknown" or confidence < threshold:
        logging.info(
            "classification_low_confidence id=%s object_type=%s confidence=%.2f threshold=%.2f",
            raw_id,
            object_type,
            confidence,
            threshold,
        )
        await _swap_reaction(message, "⏳", "❓")
        await _send_response(
            message,
            "I couldn't confidently classify that. Please clarify or use a prefix (admin:, project:, idea:, person:)."
        )
        return

    schema_path = _SCHEMA_MAP.get(object_type)
    if not schema_path:
        await message.channel.send("Unrecognized category. Please use a prefix.")
        return

    if decision_prompt and decision_config:
        index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
        queries = [content]
        if candidate_query_prompt:
            llm_queries = await _candidate_queries_from_llm(
                provider=provider,
                model=model,
                prompt=candidate_query_prompt,
                message=content,
            )
            if llm_queries:
                queries = llm_queries
        semantic_provider = provider if matching_config.semantic_weight > 0 and matching_config.semantic_provider == "openai" else None
        affinity_key = _cursor_key(message)
        affinity_scores = _load_affinity_scores(affinity_key, matching=matching_config)
        retrieval = await build_matching_candidates_async(
            db_path=index_db,
            queries=queries,
            object_type=object_type,
            matching_config=matching_config,
            score_threshold=decision_config.candidate_score_threshold,
            affinity_scores=affinity_scores,
            embedding_provider=semantic_provider,
        )
        candidates = retrieval.candidates[: decision_config.candidate_limit]
        ranking_rows = [
            {"id": candidate.object_id, "score": candidate.score}
            for candidate in candidates
        ]
        matching_trace = {
            "schema_version": 1,
            "raw_event_id": raw_id,
            "object_type": object_type,
            "timestamp": _now_iso(),
            "queries": queries,
            "retrieval_mode": retrieval.retrieval_mode,
            "fallback_reason": retrieval.fallback_reason,
            "candidate_pool": {
                "before_dedupe": retrieval.candidate_pool_before_dedupe,
                "after_dedupe": retrieval.candidate_pool_after_dedupe,
                "returned_k": len(candidates),
            },
            "weights": retrieval.weights,
            "candidates": retrieval.trace_candidates[: decision_config.candidate_limit],
            "ranking": {
                "ordered": ranking_rows,
                "top_score": retrieval.top_score,
                "second_score": retrieval.second_score,
                "margin": retrieval.margin,
            },
            "gate": {
                "decision_confidence": 0.0,
                "auto_min_score": decision_config.auto_min_score,
                "auto_min_margin": decision_config.auto_min_margin,
                "outcome": "create",
            },
        }
        logging.info(
            "matching_retrieval_ok id=%s mode=%s fallback=%s queries=%s before=%s after=%s returned=%s top=%.3f margin=%s",
            raw_id,
            retrieval.retrieval_mode,
            retrieval.fallback_reason or "",
            len(queries),
            retrieval.candidate_pool_before_dedupe,
            retrieval.candidate_pool_after_dedupe,
            len(candidates),
            retrieval.top_score,
            f"{retrieval.margin:.3f}" if retrieval.margin is not None else "none",
        )
        if retrieval.retrieval_mode != "none":
            decision_input = _build_decision_input(
                raw_event_id=raw_id,
                object_type=object_type,
                message=content,
                candidates=candidates,
            )
            decision_schema = Path("config/schemas/decision_v1.json")
            try:
                decision = await interpret_text_async(
                    provider=provider,
                    text=decision_input,
                    model=model,
                    system_prompt=decision_prompt,
                    schema_path=decision_schema,
                )
            except InterpretationValidationError as exc:
                write_derived_event(
                    derived=exc.payload,
                    raw_text=exc.raw_text,
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="decision_invalid",
                    error=exc,
                )
                logging.warning("decision_invalid id=%s error=%s", raw_id, exc)
            except Exception as exc:
                write_derived_event(
                    derived=None,
                    raw_text="",
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="decision_invalid",
                    error=exc,
                )
                logging.exception("decision_failed id=%s", raw_id)
            else:
                decision_result = write_derived_event(
                    derived=decision.derived,
                    raw_text=decision.raw_text,
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="decision",
                )
                decision_payload = decision.derived
                if decision_result.derived_path:
                    try:
                        decision_artifact_id = str(decision_result.derived_path.relative_to(Path(derived_root)))
                    except ValueError:
                        decision_artifact_id = str(decision_result.derived_path)
                logging.info(
                    "decision_ok id=%s object_type=%s confidence=%.2f candidates=%s",
                    raw_id,
                    object_type,
                    decision.derived.get("confidence", 0),
                    len(candidates),
                )
        else:
            logging.warning("matching_decision_skipped id=%s reason=retrieval_unavailable", raw_id)
    elif decision_prompt:
        logging.warning("decision_config_missing id=%s decision_prompt_path=%s", raw_id, decision_prompt_path)

    try:
        interpretation = await interpret_text_async(
            provider=provider,
            text=content,
            model=model,
            system_prompt=extract_prompt,
            schema_path=schema_path,
        )
        interpretation.derived["raw_event_id"] = raw_id
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.warning("interpretation_invalid id=%s error=%s", raw_id, exc)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "I couldn't parse that reliably. Please rephrase or use a prefix.")
        return
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.exception("interpretation_failed id=%s", raw_id)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Interpretation failed. Please try again.")
        return

    write_derived_event(
        derived=interpretation.derived,
        raw_text=interpretation.raw_text,
        derived_root=derived_root,
        raw_event_id=raw_id,
        label="derived",
    )
    logging.info(
        "interpretation_ok id=%s object_type=%s confidence=%.2f",
        raw_id,
        interpretation.derived.get("object_type"),
        interpretation.derived.get("confidence", 0),
    )

    effective_derived = interpretation.derived
    decision_routing = None
    trace_top_score = None
    trace_second_score = None
    if isinstance(matching_trace, dict):
        ranking = matching_trace.get("ranking")
        if isinstance(ranking, dict):
            top_value = ranking.get("top_score")
            second_value = ranking.get("second_score")
            if isinstance(top_value, (int, float)):
                trace_top_score = float(top_value)
            if isinstance(second_value, (int, float)):
                trace_second_score = float(second_value)
    if decision_payload and decision_config:
        decision_routing = evaluate_decision(
            decision_payload,
            decision_config,
            top_score=trace_top_score,
            second_score=trace_second_score,
        )
        effective_derived = apply_decision_to_derived(interpretation.derived, decision_routing)
    if matching_trace:
        gate = matching_trace.get("gate")
        if not isinstance(gate, dict):
            gate = {}
            matching_trace["gate"] = gate
        gate["decision_confidence"] = decision_routing.confidence if decision_routing else 0.0
        gate["auto_min_score"] = decision_config.auto_min_score if decision_config else matching_config.auto_min_score
        gate["auto_min_margin"] = decision_config.auto_min_margin if decision_config else matching_config.auto_min_margin
        gate["outcome"] = decision_routing.action if decision_routing else "create"
        ranking = matching_trace.get("ranking")
        if isinstance(ranking, dict):
            ranking["top_score"] = decision_routing.top_score if decision_routing else ranking.get("top_score", 0.0)
            ranking["second_score"] = decision_routing.second_score if decision_routing else ranking.get("second_score")
            ranking["margin"] = decision_routing.margin if decision_routing else ranking.get("margin")
        _write_matching_trace(derived_root=derived_root, raw_event_id=raw_id, trace_payload=matching_trace)

    if decision_routing and decision_routing.action == "needs_confirmation":
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending_id = generate_prefixed_id("PA_")
        now_iso = _now_iso()
        pending = PendingAction(
            schema_version=1,
            pending_action_id=pending_id,
            raw_event_id=raw_id,
            object_type=object_type,
            status="pending",
            created_at=now_iso,
            last_updated=now_iso,
            derived=effective_derived,
            decision=decision_payload,
            decision_confidence=decision_routing.confidence,
            last_decision_id=decision_artifact_id,
        )
        write_pending_action(pending, pending_root)
        candidates = decision_payload.get("candidates") if isinstance(decision_payload, dict) else []
        candidate_list = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
        default_target_id = None
        proposed_ops = effective_derived.get("proposed_operations") or []
        if isinstance(proposed_ops, list) and proposed_ops:
            target_id = proposed_ops[0].get("target_id")
            if isinstance(target_id, str):
                default_target_id = target_id
        schema_path = _SCHEMA_MAP.get(object_type)
        objects_root = config.get("paths", {}).get("objects_root", "objects")
        view = None
        if schema_path:
            view = PendingActionView(
                pending_id=pending_id,
                pending_root=pending_root,
                objects_root=objects_root,
                index_db=config.get("paths", {}).get("index_db", "index/sb.sqlite"),
                schema_path=schema_path,
                author_id=message.author.id,
                candidates=candidate_list,
                default_target_id=default_target_id,
                matching=matching_config,
                affinity_key=_cursor_key(message),
                on_canonical_change=lambda: _notify_due_time_reminder_schedule_changed(config),
            )
        await _swap_reaction(message, "⏳", "❓")
        await _send_response(
            message,
            _format_pending_message(pending_id, decision_payload),
            view=view,
        )
        return

    objects_root = config.get("paths", {}).get("objects_root", "objects")
    try:
        result = apply_operations(
            effective_derived,
            objects_root=objects_root,
            canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
            derived_schema_path=schema_path,
            last_decision_id=decision_artifact_id,
        )
    except Exception as exc:
        logging.exception("apply_failed id=%s object_type=%s", raw_id, object_type)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Failed to save item. Please try again.")
        return
    logging.info(
        "apply_ok id=%s object_type=%s written=%s",
        raw_id,
        object_type,
        ",".join(str(path) for path in result.written_paths),
    )
    await _refresh_index_async(
        objects_root,
        config.get("paths", {}).get("index_db", "index/sb.sqlite"),
        matching=matching_config,
    )
    _notify_due_time_reminder_schedule_changed(config)
    touched_ids = _extract_target_ids_from_derived(effective_derived)
    touched_ids.extend(_extract_ids_from_written_paths(result.written_paths))
    _record_affinity_touches(_cursor_key(message), touched_ids, matching=matching_config)

    title = effective_derived.get("extracted_fields", {}).get("title") or content
    op = None
    ops = effective_derived.get("proposed_operations") or []
    if ops:
        op = ops[0].get("op")
    await _swap_reaction(message, "⏳", "✅")
    verb = "Saved"
    if op in {"update", "append"}:
        verb = "Updated"
    feedback_view = None
    auto_apply_target_id = None
    if decision_routing and decision_routing.action == "auto_apply":
        decision_ops = decision_routing.decision_ops
        if decision_ops and isinstance(decision_ops[0], dict):
            target_id = decision_ops[0].get("target_id")
            if isinstance(target_id, str):
                auto_apply_target_id = target_id
                feedback_view = AutoApplyFeedbackView(author_id=message.author.id, target_id=target_id)
    response = f"{verb} \"{title}\" in {object_type.capitalize()}."
    if auto_apply_target_id:
        response = f"{response} (Auto-applied.)"
    await _send_response(message, response, thread_title=title, view=feedback_view)


def _generate_raw_id() -> str:
    return generate_prefixed_id("R_")


def _build_decision_input(
    *,
    raw_event_id: str,
    object_type: str,
    message: str,
    candidates: list[Any],
) -> str:
    payload = {
        "raw_event_id": raw_event_id,
        "object_type": object_type,
        "message": message,
        "candidates": [
            {
                "id": candidate.object_id,
                "title": candidate.title,
                "snippet": candidate.snippet,
                "score": candidate.score,
            }
            for candidate in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=True)


def _extract_target_ids_from_derived(derived: dict[str, Any]) -> list[str]:
    ops = derived.get("proposed_operations") or []
    if not isinstance(ops, list):
        return []
    object_ids: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        target_id = op.get("target_id")
        if isinstance(target_id, str) and target_id.strip():
            object_ids.append(target_id.strip())
    return object_ids


def _extract_ids_from_written_paths(paths: list[Path]) -> list[str]:
    object_ids: list[str] = []
    for path in paths:
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        object_id = frontmatter.get("id")
        if isinstance(object_id, str) and object_id.strip():
            object_ids.append(object_id.strip())
    return object_ids


def _first_title_from_paths(paths: list[Path]) -> str | None:
    for path in paths:
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        title = frontmatter.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def _titles_from_paths(paths: list[Path]) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        title = frontmatter.get("title")
        if not isinstance(title, str):
            continue
        value = title.strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        titles.append(value)
    return titles


def _format_apply_success_message(*, written_paths: list[Path], fallback_title: str | None = None) -> str:
    titles = _titles_from_paths(written_paths)
    if not titles and fallback_title:
        titles = [fallback_title]
    if len(titles) >= 1:
        if len(titles) == 1:
            header = "✅ Applied update to 1 note:"
        else:
            header = f"✅ Applied updates to {len(titles)} notes:"
        lines = [header]
        for title in titles[:5]:
            lines.append(f'- "{title}"')
        more_count = len(titles) - 5
        if more_count > 0:
            lines.append(f"- and {more_count} more")
        return "\n".join(lines)
    return f"✅ Applied update. ({len(written_paths)} item(s) updated.)"


def _candidate_title(candidates: list[dict[str, Any]], candidate_id: str | None) -> str | None:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("id") != candidate_id:
            continue
        title = candidate.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def _candidate_display_title(candidate: dict[str, Any]) -> str:
    title = candidate.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Untitled note"


def _write_matching_trace(
    *,
    derived_root: str | Path,
    raw_event_id: str,
    trace_payload: dict[str, Any],
) -> None:
    try:
        schema = load_json_schema(Path("config/schemas/matching_trace_v1.json"))
        validate_json(schema, trace_payload)
        write_derived_event(
            derived=trace_payload,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="matching_trace",
        )
        logging.info("matching_trace_written id=%s mode=%s", raw_event_id, trace_payload.get("retrieval_mode"))
    except Exception as exc:
        logging.warning("matching_trace_write_failed id=%s error=%s", raw_event_id, exc)


def _write_pending_with_status(
    root: str | Path,
    pending: PendingAction,
    status: str,
    *,
    derived: dict[str, Any] | None = None,
) -> PendingAction:
    updated = PendingAction(
        schema_version=pending.schema_version,
        pending_action_id=pending.pending_action_id,
        raw_event_id=pending.raw_event_id,
        object_type=pending.object_type,
        status=status,
        created_at=pending.created_at,
        last_updated=_now_iso(),
        derived=derived or pending.derived,
        decision=pending.decision,
        decision_confidence=pending.decision_confidence,
        last_decision_id=pending.last_decision_id,
    )
    write_pending_action(updated, root)
    return updated


def _strip_pending_controls_from_message(content: str) -> str:
    lines = content.split("\n")
    cleaned = [
        line
        for line in lines
        if line != _PENDING_CONTROLS_INSTRUCTION and line != "\u200b"
    ]
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


async def _disable_view(
    interaction: discord.Interaction,
    *,
    clear_pending_instructions: bool = False,
) -> None:
    try:
        if interaction.message:
            edit_kwargs: dict[str, Any] = {"view": None}
            if clear_pending_instructions:
                message_content = getattr(interaction.message, "content", None)
                if isinstance(message_content, str):
                    edit_kwargs["content"] = _strip_pending_controls_from_message(message_content)
            await interaction.message.edit(**edit_kwargs)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


async def _safe_add_reaction(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except (discord.HTTPException, discord.Forbidden):
        return


async def _swap_reaction(message: discord.Message, remove_emoji: str, add_emoji: str) -> None:
    await _safe_add_reaction(message, add_emoji)
    try:
        bot_user = message.guild.me if message.guild else message._state.user
        if bot_user is None:
            return
        await message.remove_reaction(remove_emoji, cast(discord.abc.Snowflake, bot_user))
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


async def _send_response(
    message: discord.Message,
    content: str,
    thread_title: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    if isinstance(message.channel, discord.Thread):
        try:
            await message.channel.send(content=content, view=view)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("response_send_failed channel=thread error=%s", exc)
        return
    try:
        name = "Squire"
        if thread_title:
            trimmed = thread_title.strip()
            if len(trimmed) > 60:
                trimmed = trimmed[:57].rstrip() + "..."
            name = f"Squire: {trimmed}"
        else:
            name = f"Squire: {message.author.display_name}"
        thread = await message.create_thread(
            name=name,
            auto_archive_duration=1440,
        )
        await thread.send(content=content, view=view)
        logging.info("response_sent thread=%s", thread.id)
        return
    except (discord.HTTPException, discord.Forbidden) as exc:
        logging.warning("thread_create_failed channel=%s error=%s", message.channel.id, exc)
        try:
            await message.channel.send(content=content, view=view)
            logging.info("response_sent channel=%s", message.channel.id)
        except (discord.HTTPException, discord.Forbidden) as send_exc:
            logging.warning("response_send_failed channel=%s error=%s", message.channel.id, send_exc)


async def _handle_command(
    message: discord.Message,
    content: str,
    raw_id: str,
    config: dict[str, Any],
) -> bool:
    parts = content.split()
    command = parts[0].lower()
    matching_config = load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    if command == "!status":
        try:
            digest = build_daily_digest(objects_root, config)
        except Exception:
            logging.exception("status_digest_failed id=%s", raw_id)
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Failed to build status digest. Check logs for details.")
            return True
        rendered, cursor_object_ids = _render_numbered_daily_digest_for_command(digest)
        if cursor_object_ids:
            _store_result_cursor(message, config, cursor_object_ids, source_view="status")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, rendered)
        return True
    if command == "!weekly":
        try:
            review = build_weekly_review(objects_root, config)
        except Exception:
            logging.exception("weekly_review_build_failed id=%s", raw_id)
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Failed to build weekly review. Check logs for details.")
            return True
        rendered, cursor_object_ids = _render_numbered_weekly_review_for_command(review)
        if cursor_object_ids:
            _store_result_cursor(message, config, cursor_object_ids, source_view="weekly")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, rendered)
        return True
    if command == "!help":
        if len(parts) > 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !help [command]")
            return True
        if len(parts) == 2:
            topic = _normalize_help_topic(parts[1])
            help_detail = _HELP_DETAILS.get(topic)
            if help_detail is None:
                await _swap_reaction(message, "⏳", "⚠️")
                await _send_response(message, f"Unknown command `{parts[1]}`. Run `!help` for a command list.")
                return True
            await _swap_reaction(message, "⏳", "✅")
            await _send_response(message, help_detail)
            return True
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, _HELP_COPY)
        return True
    if command == "!recent":
        limit: int | None = None
        if len(parts) > 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !recent [number]")
            return True
        if len(parts) == 2:
            parsed = _parse_positive_int(parts[1])
            if parsed is None:
                await _swap_reaction(message, "⏳", "⚠️")
                await _send_response(message, "Usage: !recent [number]")
                return True
            limit = parsed
        surfaced = build_recent_list(objects_root, config, limit=limit)
        if not surfaced.lines:
            await _swap_reaction(message, "⏳", "✅")
            await _send_response(message, "No recent notes found.")
            return True
        _store_result_cursor(message, config, surfaced.object_ids, source_view="recent")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(
            message,
            "Recent notes:\n"
            + "\n".join(surfaced.lines)
            + "\n\n"
            + _NUMBERED_COMMAND_TIP_WITH_RECENT_LIMIT,
        )
        return True
    if command == "!find":
        if len(parts) < 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !find <query>")
            return True
        query = content.split(None, 1)[1].strip()
        if not query:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !find <query>")
            return True
        surfaced = build_find_list(objects_root, index_db, config, query)
        if not surfaced.lines:
            await _swap_reaction(message, "⏳", "✅")
            await _send_response(message, f'No matches found for \"{query}\".')
            return True
        _store_result_cursor(message, config, surfaced.object_ids, source_view="find")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(
            message,
            "Matches:\n" + "\n".join(surfaced.lines) + "\n\n" + _NUMBERED_COMMAND_TIP,
        )
        return True
    if command == "!show":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !show <number>")
            return True
        number = _parse_positive_int(parts[1])
        if number is None:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !show <number>")
            return True
        object_id = _resolve_result_cursor(message, number)
        if object_id is None:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(
                message,
                "No active result list for that number. Run !recent, !find, !status, or !weekly first.",
            )
            return True
        detail = build_item_detail(objects_root, object_id, config)
        if not detail:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "That note is no longer available.")
            return True
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, detail)
        return True
    if command == "!append":
        if len(parts) < 3:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !append <id|number> <text>")
            return True
        target_resolution = _resolve_command_target(message, parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            _log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="append",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !append <id|number> <text>")
            return True
        text = content.split(None, 2)[2].strip()
        if not text:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !append <id|number> <text>")
            return True
        return await _apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="append",
            fields={"body": text},
            command_name="append",
            row_number=target_resolution.row_number,
            source_view=target_resolution.source_view,
        )
    if command == "!done":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !done <id|number>")
            return True
        target_resolution = _resolve_command_target(message, parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            _log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="done",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !done <id|number>")
            return True
        return await _apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="update",
            fields={"status": "done", "completed_at": _now_iso()},
            command_name="done",
            row_number=target_resolution.row_number,
            source_view=target_resolution.source_view,
        )
    if command == "!fix":
        try:
            fix_parts = shlex.split(content)
        except ValueError:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Invalid !fix syntax. Quote values containing spaces.")
            return True
        if len(fix_parts) < 3:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !fix <id|number> <field=value> [field=value ...]")
            return True
        target_resolution = _resolve_command_target(message, fix_parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            _log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="fix",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !fix <id|number> <field=value> [field=value ...]")
            return True
        updates: dict[str, Any] = {}
        for token in fix_parts[2:]:
            if "=" not in token:
                await _swap_reaction(message, "⏳", "⚠️")
                await _send_response(message, "Invalid !fix syntax. Use field=value and quote values containing spaces.")
                return True
            key, value = token.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                await _swap_reaction(message, "⏳", "⚠️")
                await _send_response(message, "Field name cannot be empty.")
                return True
            updates[key] = value
        if not updates:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "No valid fields provided.")
            return True
        return await _apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="update",
            fields=updates,
            validate_fix=True,
            command_name="fix",
            row_number=target_resolution.row_number,
            source_view=target_resolution.source_view,
        )
    if command == "!clear-archive":
        if len(parts) != 1:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !clear-archive")
            return True
        _start_archive_clear_confirmation(message)
        await _swap_reaction(message, "⏳", "❓")
        await _send_response(
            message,
            "This will permanently clear all archive data (except `.git`). Reply with `DELETE` within 2 minutes to confirm.",
        )
        return True
    if command == "!confirm":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !confirm <pending_id>")
            return True
        pending_id = parts[1]
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = load_pending_action(pending_root, pending_id)
        if not pending:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Pending action {pending_id} is {pending.status}.")
            return True
        object_type = pending.object_type
        schema_path = _SCHEMA_MAP.get(object_type)
        if not schema_path and object_type != "mixed":
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Pending action has an unsupported object type.")
            return True
        try:
            result = apply_operations(
                pending.derived,
                objects_root=objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=schema_path if schema_path else None,
                last_decision_id=pending.last_decision_id,
            )
        except Exception as exc:
            logging.exception("pending_apply_failed id=%s", pending_id)
            update_pending_action_status(pending_root, pending_id, "failed")
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Failed to apply pending action. Check logs for details.")
            return True
        await _refresh_index_async(objects_root, index_db, matching=matching_config)
        _notify_due_time_reminder_schedule_changed(config)
        touched_ids = _extract_target_ids_from_derived(pending.derived)
        touched_ids.extend(_extract_ids_from_written_paths(result.written_paths))
        _record_affinity_touches(_cursor_key(message), touched_ids, matching=matching_config)
        update_pending_action_status(pending_root, pending_id, "confirmed")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(
            message,
            f"Applied pending action {pending_id}. ({len(result.written_paths)} item(s) updated.)",
        )
        return True
    if command == "!cancel":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !cancel <pending_id>")
            return True
        pending_id = parts[1]
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = load_pending_action(pending_root, pending_id)
        if not pending:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Pending action {pending_id} is {pending.status}.")
            return True
        update_pending_action_status(pending_root, pending_id, "cancelled")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, f"Cancelled pending action {pending_id}.")
        return True
    return False


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _format_pending_message(_pending_id: str, decision_payload: dict[str, Any]) -> str:
    operations = decision_payload.get("proposed_operations") or []
    candidates = decision_payload.get("candidates") or []
    targets = []
    for op in operations:
        target_id = op.get("target_id")
        if isinstance(target_id, str):
            targets.append(target_id)
    candidate_lookup = {candidate.get("id"): candidate for candidate in candidates if isinstance(candidate, dict)}
    lines = ["I found a possible match and want to confirm before updating."]
    if targets:
        lines.append("")
        lines.append("Proposed updates:")
        for target_id in targets:
            candidate = candidate_lookup.get(target_id, {})
            lines.append(f"- {_candidate_display_title(candidate)}")
    candidate_ids = [
        candidate.get("id")
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("id"), str)
    ]
    alternates = [candidate_id for candidate_id in candidate_ids if isinstance(candidate_id, str) and candidate_id not in targets]
    if alternates:
        lines.append("")
        lines.append("Other close matches:")
        for candidate_id in alternates:
            candidate = candidate_lookup.get(candidate_id, {})
            lines.append(f"- {_candidate_display_title(candidate)}")
    lines.append("")
    lines.append(_PENDING_CONTROLS_INSTRUCTION)
    lines.append("\u200b")
    return "\n".join(lines)


def _force_create_derived(derived: dict[str, Any]) -> dict[str, Any]:
    routing = DecisionRouting(
        action="create",
        confidence=0.0,
        decision_ops=[],
        top_score=0.0,
        second_score=None,
        margin=None,
    )
    return apply_decision_to_derived(derived, routing)


async def _apply_command_operation(
    message: discord.Message,
    raw_id: str,
    config: dict[str, Any],
    target_id: str,
    op: str,
    fields: dict[str, Any],
    *,
    validate_fix: bool = False,
    command_name: str | None = None,
    row_number: int | None = None,
    source_view: str | None = None,
) -> bool:
    matching_config = load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    target_path = find_object_path(objects_root, target_id)
    if not target_path:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Unknown ID: {target_id}")
        return True
    frontmatter = load_frontmatter(target_path)
    object_type = frontmatter.get("type")
    if not object_type:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Unable to determine object type for {target_id}")
        return True
    if not isinstance(object_type, str):
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Unable to determine object type for {target_id}")
        return True
    if validate_fix:
        fields, validation_error = _validate_fix_updates(object_type, fields)
        if validation_error:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, validation_error)
            return True
    if op == "update" and object_type != "admin" and fields.get("status") == "done":
        if command_name and row_number is not None:
            _log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command=command_name,
                reason="wrong_type",
                source_view=source_view,
                row_number=row_number,
            )
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Only admin items can be marked done.")
        return True
    if command_name and row_number is not None:
        _log_numbered_mutation_resolved(
            raw_event_id=raw_id,
            command=command_name,
            source_view=source_view,
            row_number=row_number,
            object_id=target_id,
        )
    derived = {
        "object_type": object_type,
        "raw_event_id": raw_id,
        "extracted_fields": {},
        "proposed_operations": [
            {
                "op": op,
                "target_id": target_id,
                "fields": fields,
            }
        ],
    }
    try:
        result = apply_operations(
            derived,
            objects_root=objects_root,
            canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
            derived_schema_path=None,
        )
    except Exception as exc:
        logging.exception("command_apply_failed id=%s op=%s", raw_id, op)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Command failed. Check logs for details.")
        return True
    await _refresh_index_async(
        objects_root,
        config.get("paths", {}).get("index_db", "index/sb.sqlite"),
        matching=matching_config,
    )
    _notify_due_time_reminder_schedule_changed(config)
    touched_ids = _extract_target_ids_from_derived(derived)
    touched_ids.extend(_extract_ids_from_written_paths(result.written_paths))
    _record_affinity_touches(_cursor_key(message), touched_ids, matching=matching_config)
    await _swap_reaction(message, "⏳", "✅")
    title = frontmatter.get("title") or target_id
    await _send_response(
        message,
        f"Updated {object_type} \"{title}\".",
        thread_title=title,
    )
    return True


async def _handle_archive_clear_confirmation(message: discord.Message, config: dict[str, Any]) -> bool:
    if not _consume_archive_clear_confirmation(message):
        await _safe_add_reaction(message, "⚠️")
        await _send_response(message, "No pending archive clear request. Run `!clear-archive` first.")
        return True
    archive_root = config.get("archive_root")
    if not isinstance(archive_root, str) or not archive_root.strip():
        await _safe_add_reaction(message, "⚠️")
        await _send_response(message, "archive_root is not configured.")
        return True
    try:
        removed = _clear_archive_contents(archive_root)
    except Exception as exc:
        logging.exception("archive_clear_failed error=%s", exc)
        await _safe_add_reaction(message, "⚠️")
        await _send_response(message, f"Failed to clear archive: {exc}")
        return True
    _RESULT_CURSORS.clear()
    _MATCHING_AFFINITY.clear()
    _NL_CLARIFICATION_CONTEXTS.clear()
    _notify_due_time_reminder_schedule_changed(config, clear_state=True)
    await _safe_add_reaction(message, "✅")
    await _send_response(message, f"Archive cleared. Removed {removed} top-level entries from `{archive_root}`.")
    return True


class SquireBot(discord.Client):
    def __init__(self, config: dict[str, Any]) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._config = config
        schedule = config.get("schedule", {}) if isinstance(config.get("schedule"), dict) else {}
        self._digest_time = _parse_daily_digest_time(schedule.get("daily_digest_time"))
        self._weekly_review_day = _parse_weekly_review_day(schedule.get("weekly_review_day"))
        self._weekly_review_time = _parse_daily_digest_time(schedule.get("weekly_review_time"))
        self._digest_channel_id = _coerce_int(schedule.get("daily_digest_channel_id"))
        self._digest_user_id = _coerce_int(schedule.get("daily_digest_user_id"))
        self._due_time_reminder_schedule = _load_due_time_reminder_schedule_config(schedule)
        self._due_time_reminder_ledger_path = _due_time_reminder_ledger_path(config)
        self._last_dm_channel_id: int | None = None
        self._last_dm_user_id: int | None = None
        self._timezone = resolve_timezone(config.get("timezone"))
        self._digest_task: asyncio.Task | None = None
        self._weekly_review_task: asyncio.Task | None = None
        self._due_time_reminder_task: asyncio.Task | None = None
        self._due_time_reminder_midnight_task: asyncio.Task | None = None
        self._due_time_reminder_reconcile_task: asyncio.Task | None = None
        self._due_time_reminder_schedule_changed = asyncio.Event()
        self._due_time_reminder_state_lock = asyncio.Lock()
        self._due_time_reminder_heap: list[tuple[datetime, datetime, str, int, DueTimeReminderEvent]] = []
        self._due_time_reminder_sent_ledger: dict[str, _DueTimeReminderSentLedgerEntry] = {}
        self._due_time_reminder_reset_requested = False
        self._config[_DUE_TIME_REMINDER_NOTIFY_CONFIG_KEY] = self._on_due_time_reminder_schedule_changed

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")
        if self._digest_time and self._digest_task is None:
            self._digest_task = asyncio.create_task(self._daily_digest_loop())
        if self._weekly_review_day is not None and self._weekly_review_time and self._weekly_review_task is None:
            self._weekly_review_task = asyncio.create_task(self._weekly_review_loop())
        if self._due_time_reminder_schedule.enabled and self._due_time_reminder_task is None:
            self._due_time_reminder_task = asyncio.create_task(self._due_time_reminder_loop())
            self._due_time_reminder_midnight_task = asyncio.create_task(self._due_time_reminder_midnight_loop())
            self._due_time_reminder_reconcile_task = asyncio.create_task(self._due_time_reminder_reconcile_loop())

    async def on_message(self, message: discord.Message) -> None:
        if not message.author.bot and isinstance(message.channel, discord.DMChannel):
            self._last_dm_channel_id = message.channel.id
            self._last_dm_user_id = message.author.id
        await _handle_message(message, self._config)

    def _on_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        if not self._due_time_reminder_schedule.enabled:
            return
        if clear_state:
            self._due_time_reminder_reset_requested = True
        self._due_time_reminder_schedule_changed.set()

    @staticmethod
    def _due_time_reminder_heap_item(
        event: DueTimeReminderEvent,
    ) -> tuple[datetime, datetime, str, int, DueTimeReminderEvent]:
        return (event.fire_at, event.due_at, event.object_id, event.offset_minutes, event)

    def _prune_due_time_reminder_sent_ledger(self, *, now: datetime) -> bool:
        expired_keys = [key for key, entry in self._due_time_reminder_sent_ledger.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._due_time_reminder_sent_ledger.pop(key, None)
        return bool(expired_keys)

    async def _load_due_time_reminder_sent_ledger(self) -> None:
        now = datetime.now(timezone.utc)
        loaded = await asyncio.to_thread(
            _load_due_time_reminder_ledger_entries,
            self._due_time_reminder_ledger_path,
            now=now,
        )
        async with self._due_time_reminder_state_lock:
            self._due_time_reminder_sent_ledger = loaded

    async def _flush_due_time_reminder_sent_ledger(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._due_time_reminder_state_lock:
            entries = dict(self._due_time_reminder_sent_ledger)
        try:
            await asyncio.to_thread(
                _flush_due_time_reminder_ledger_entries,
                self._due_time_reminder_ledger_path,
                entries=entries,
                now=now,
            )
        except Exception as exc:
            logging.warning("due_time_reminder_ledger_flush_failed error=%s", exc)

    async def _rebuild_due_time_reminder_schedule(self, *, source: str) -> int:
        now = datetime.now(self._timezone)
        events = await asyncio.to_thread(
            build_due_time_reminder_events,
            self._config.get("paths", {}).get("objects_root", "objects"),
            self._config,
            offsets_minutes=list(self._due_time_reminder_schedule.offsets_minutes),
            now=now,
            late_grace_minutes=self._due_time_reminder_schedule.late_grace_minutes,
            horizon_hours=_DUE_TIME_REMINDER_HORIZON_HOURS,
        )
        heap_items = [self._due_time_reminder_heap_item(event) for event in events]
        heapq.heapify(heap_items)
        async with self._due_time_reminder_state_lock:
            self._due_time_reminder_heap = heap_items
        logging.info(
            "due_time_reminder_schedule_built count=%s horizon_hours=%s source=%s",
            len(events),
            _DUE_TIME_REMINDER_HORIZON_HOURS,
            source,
        )
        return len(events)

    async def _peek_due_time_reminder_fire_at(self) -> datetime | None:
        async with self._due_time_reminder_state_lock:
            if not self._due_time_reminder_heap:
                return None
            return self._due_time_reminder_heap[0][0]

    async def _push_due_time_reminder_events(self, events: list[DueTimeReminderEvent]) -> None:
        if not events:
            return
        async with self._due_time_reminder_state_lock:
            for event in events:
                heapq.heappush(self._due_time_reminder_heap, self._due_time_reminder_heap_item(event))

    async def _pop_due_time_reminder_due_events(self, *, now: datetime) -> list[DueTimeReminderEvent]:
        events: list[DueTimeReminderEvent] = []
        async with self._due_time_reminder_state_lock:
            while self._due_time_reminder_heap and self._due_time_reminder_heap[0][0] <= now:
                events.append(heapq.heappop(self._due_time_reminder_heap)[4])
            self._prune_due_time_reminder_sent_ledger(now=now.astimezone(timezone.utc))
        return events

    async def _resolve_due_time_reminder_channel(self) -> discord.abc.Messageable | None:
        channel_id = self._due_time_reminder_schedule.channel_id
        if channel_id:
            channel = self.get_channel(channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("due_time_reminder_channel_unavailable id=%s", channel_id)
                return None
        user_id = self._due_time_reminder_schedule.user_id
        if user_id:
            user = self.get_user(user_id)
            if not user:
                try:
                    user = await self.fetch_user(user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logging.warning("due_time_reminder_user_unavailable id=%s", user_id)
                    user = None
            if user:
                if user.dm_channel:
                    return user.dm_channel
                try:
                    return await user.create_dm()
                except (discord.HTTPException, discord.Forbidden):
                    logging.warning("due_time_reminder_dm_create_failed user=%s", user_id)
                    return None
        return await self._resolve_digest_channel()

    def _due_time_reminder_is_stale(self, event: DueTimeReminderEvent, *, now: datetime) -> bool:
        grace = timedelta(minutes=self._due_time_reminder_schedule.late_grace_minutes)
        return now - event.fire_at > grace

    def _due_time_reminder_recheck_event(self, event: DueTimeReminderEvent) -> tuple[bool, str | None]:
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        path = find_object_path(objects_root, event.object_id)
        if not path:
            return False, "missing_object"
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            return False, "ineligible"
        status_value = str(frontmatter.get("status") or "").strip().lower()
        if status_value not in _DUE_TIME_REMINDER_ALLOWED_STATUSES:
            return False, "ineligible"
        archived_value = frontmatter.get("archived")
        archived = False
        if isinstance(archived_value, bool):
            archived = archived_value
        elif isinstance(archived_value, str):
            archived = archived_value.strip().lower() in {"1", "true", "yes", "y"}
        if archived:
            return False, "ineligible"
        due_at = _coerce_timezone_datetime(frontmatter.get("due_at"), self._timezone)
        if due_at is None:
            return False, "ineligible"
        if due_at != event.due_at:
            return False, "ineligible"
        return True, None

    async def _dispatch_due_time_reminders(self) -> None:
        now = datetime.now(self._timezone)
        due_events = await self._pop_due_time_reminder_due_events(now=now)
        if not due_events:
            return

        async with self._due_time_reminder_state_lock:
            active_sent_keys = set(self._due_time_reminder_sent_ledger.keys())

        sendable: list[DueTimeReminderEvent] = []
        for event in due_events:
            if self._due_time_reminder_is_stale(event, now=now):
                logging.info("due_time_reminder_event_skipped reason=stale")
                continue
            key = _due_time_reminder_key(event)
            if key in active_sent_keys:
                logging.info("due_time_reminder_event_skipped reason=duplicate")
                continue
            eligible, reason = self._due_time_reminder_recheck_event(event)
            if not eligible:
                logging.info("due_time_reminder_event_skipped reason=%s", reason or "ineligible")
                continue
            sendable.append(event)

        if not sendable:
            return

        channel = await self._resolve_due_time_reminder_channel()
        if not channel:
            logging.warning("due_time_reminder_skipped reason=no_channel")
            await self._push_due_time_reminder_events(sendable)
            return

        content = render_due_time_reminder_message(sendable, self._config, now=now)
        if not content:
            return

        try:
            await channel.send(content=content)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("due_time_reminder_send_failed error=%s", exc)
            await self._push_due_time_reminder_events(sendable)
            return

        sent_at = datetime.now(timezone.utc)
        expires_at = sent_at + timedelta(hours=_DUE_TIME_REMINDER_LEDGER_RETENTION_HOURS)
        async with self._due_time_reminder_state_lock:
            for event in sendable:
                key = _due_time_reminder_key(event)
                self._due_time_reminder_sent_ledger[key] = _DueTimeReminderSentLedgerEntry(
                    key=key,
                    object_id=event.object_id,
                    due_at=event.due_at.astimezone(timezone.utc),
                    offset_minutes=event.offset_minutes,
                    fire_at=event.fire_at.astimezone(timezone.utc),
                    sent_at=sent_at,
                    expires_at=expires_at,
                )
                logging.info(
                    "due_time_reminder_event_dispatched object_id=%s due_at=%s offset=%s",
                    event.object_id,
                    event.due_at.isoformat(),
                    event.offset_minutes,
                )
            self._prune_due_time_reminder_sent_ledger(now=sent_at)
        await self._flush_due_time_reminder_sent_ledger()

    async def _due_time_reminder_loop(self) -> None:
        try:
            await self._load_due_time_reminder_sent_ledger()
            await self._rebuild_due_time_reminder_schedule(source="startup")
        except Exception:
            logging.exception("due_time_reminder_startup_failed")
        while not self.is_closed():
            if self._due_time_reminder_schedule_changed.is_set():
                self._due_time_reminder_schedule_changed.clear()
                if self._due_time_reminder_reset_requested:
                    async with self._due_time_reminder_state_lock:
                        self._due_time_reminder_heap.clear()
                        self._due_time_reminder_sent_ledger.clear()
                    self._due_time_reminder_reset_requested = False
                    await self._flush_due_time_reminder_sent_ledger()
                try:
                    await self._rebuild_due_time_reminder_schedule(source="event")
                except Exception:
                    logging.exception("due_time_reminder_rebuild_failed source=event")
                continue

            next_fire = await self._peek_due_time_reminder_fire_at()
            if next_fire is None:
                delay = float(_DUE_TIME_REMINDER_EMPTY_QUEUE_WAIT_SECONDS)
            else:
                delay = max(0.0, (next_fire - datetime.now(self._timezone)).total_seconds())
            try:
                await asyncio.wait_for(self._due_time_reminder_schedule_changed.wait(), timeout=delay)
                self._due_time_reminder_schedule_changed.clear()
                logging.info("due_time_reminder_wake reason=schedule_changed")
                if self._due_time_reminder_reset_requested:
                    async with self._due_time_reminder_state_lock:
                        self._due_time_reminder_heap.clear()
                        self._due_time_reminder_sent_ledger.clear()
                    self._due_time_reminder_reset_requested = False
                    await self._flush_due_time_reminder_sent_ledger()
                try:
                    await self._rebuild_due_time_reminder_schedule(source="event")
                except Exception:
                    logging.exception("due_time_reminder_rebuild_failed source=event")
                continue
            except asyncio.TimeoutError:
                logging.info("due_time_reminder_wake reason=timeout")
            try:
                await self._dispatch_due_time_reminders()
            except Exception:
                logging.exception("due_time_reminder_dispatch_failed")

    async def _due_time_reminder_midnight_loop(self) -> None:
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = _next_midnight_run(now)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._rebuild_due_time_reminder_schedule(source="midnight")
            except Exception:
                logging.exception("due_time_reminder_rebuild_failed source=midnight")

    async def _due_time_reminder_reconcile_loop(self) -> None:
        interval_seconds = max(1, self._due_time_reminder_schedule.reconcile_minutes) * 60
        while not self.is_closed():
            await asyncio.sleep(interval_seconds)
            try:
                count = await self._rebuild_due_time_reminder_schedule(source="reconcile")
            except Exception:
                logging.exception("due_time_reminder_rebuild_failed source=reconcile")
                continue
            now_utc = datetime.now(timezone.utc)
            async with self._due_time_reminder_state_lock:
                pruned = self._prune_due_time_reminder_sent_ledger(now=now_utc)
            if pruned:
                await self._flush_due_time_reminder_sent_ledger()
            logging.info("due_time_reminder_reconcile_completed count=%s", count)

    async def _resolve_digest_channel(self) -> discord.abc.Messageable | None:
        if self._digest_channel_id:
            channel = self.get_channel(self._digest_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(self._digest_channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("daily_digest_channel_unavailable id=%s", self._digest_channel_id)
                return None
        if self._digest_user_id:
            user = self.get_user(self._digest_user_id)
            if not user:
                try:
                    user = await self.fetch_user(self._digest_user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logging.warning("daily_digest_user_unavailable id=%s", self._digest_user_id)
                    user = None
            if user:
                if user.dm_channel:
                    return user.dm_channel
                try:
                    return await user.create_dm()
                except (discord.HTTPException, discord.Forbidden):
                    logging.warning("daily_digest_dm_create_failed user=%s", self._digest_user_id)
                    return None
        if self._last_dm_channel_id:
            channel = self.get_channel(self._last_dm_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(self._last_dm_channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("daily_digest_last_dm_unavailable id=%s", self._last_dm_channel_id)
                return None
        return None

    async def _send_daily_digest(self) -> None:
        channel = await self._resolve_digest_channel()
        if not channel:
            logging.warning("daily_digest_skipped reason=no_channel")
            return
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        digest = build_daily_digest(objects_root, self._config)
        try:
            await channel.send(content=digest.render())
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("daily_digest_send_failed error=%s", exc)

    async def _daily_digest_loop(self) -> None:
        if not self._digest_time:
            return
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = _next_daily_run(now, self._digest_time)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._send_daily_digest()
            except Exception:
                logging.exception("daily_digest_failed")

    async def _send_weekly_review(self) -> None:
        channel = await self._resolve_digest_channel()
        if not channel:
            logging.warning("weekly_review_skipped reason=no_channel")
            return
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        review = build_weekly_review(objects_root, self._config)
        try:
            await channel.send(content=review.render())
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("weekly_review_send_failed error=%s", exc)

    async def _weekly_review_loop(self) -> None:
        if self._weekly_review_day is None or not self._weekly_review_time:
            return
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = _next_weekly_run(now, self._weekly_review_day, self._weekly_review_time)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._send_weekly_review()
            except Exception:
                logging.exception("weekly_review_failed")


def main() -> None:
    load_dotenv()
    _configure_logging()
    config_path = Path("config.yaml")
    config = load_config(config_path)
    config = _apply_test_archive_root_override(config)
    try:
        config = normalize_archive_config(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        _run_test_mode_reset_seed(config)
    except Exception as exc:
        logging.exception("test_mode_startup_failed reason=%s", exc)
        raise SystemExit(str(exc)) from exc
    matching_config = load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    index_path = Path(index_db)
    if not index_path.exists():
        logging.info("index_missing rebuilding index at %s", index_db)
        try:
            rebuild_index(objects_root, index_db)
        except Exception as exc:
            logging.exception("index_rebuild_failed error=%s", exc)
    if matching_config.semantic_weight > 0:
        if matching_config.semantic_provider != "openai":
            logging.warning(
                "semantic_startup_sync_skipped reason=unsupported_provider provider=%s",
                matching_config.semantic_provider,
            )
        else:
            try:
                provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))
                stats = sync_semantic_index(
                    objects_root=objects_root,
                    db_path=index_db,
                    matching_config=matching_config,
                    embedding_provider=provider,
                )
                logging.info(
                    "semantic_startup_sync_ok indexed=%s unchanged=%s removed=%s metadata_reset=%s duration_ms=%s",
                    stats.indexed_count,
                    stats.unchanged_count,
                    stats.removed_count,
                    stats.metadata_reset,
                    stats.duration_ms,
                )
            except Exception as exc:
                logging.warning("semantic_startup_sync_failed error=%s", exc)
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is required")

    health_server = _start_health_server()
    try:
        bot = SquireBot(config=config)
        bot.run(token)
    finally:
        if health_server:
            health_server.stop()
            logging.info("health_server_stopped")


if __name__ == "__main__":
    main()
