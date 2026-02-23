from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable

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
from squire_core.decision_flow import apply_decision_to_derived, evaluate_decision
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
from squire_core.transport import commands as _transport_commands
from squire_core.transport import routing as _transport_routing
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord.adapter import (
    DiscordSquireBot as _DiscordAdapterBot,
    safe_add_reaction as _discord_safe_add_reaction,
    send_response as _discord_send_response,
    swap_reaction as _discord_swap_reaction,
)
from squire_core.transport.discord.views import (
    AutoApplyFeedbackView,
    MutationPendingView,
    PendingActionView,
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
    AffinityTouch as _AffinityTouch,
    ARCHIVE_CLEAR_CONFIRMATIONS as _ARCHIVE_CLEAR_CONFIRMATIONS,
    ArchiveClearConfirmation as _ArchiveClearConfirmation,
    clear_nl_clarification_context as _state_clear_nl_clarification_context,
    CommandTargetResolution as _CommandTargetResolution,
    consume_archive_clear_confirmation as _state_consume_archive_clear_confirmation,
    DueTimeReminderScheduleConfig as _DueTimeReminderScheduleConfig,
    DueTimeReminderSentLedgerEntry as _DueTimeReminderSentLedgerEntry,
    get_nl_clarification_context as _state_get_nl_clarification_context,
    load_affinity_scores as _state_load_affinity_scores,
    MATCHING_AFFINITY as _MATCHING_AFFINITY,
    NLClarificationContext as _NLClarificationContext,
    NL_CLARIFICATION_CONTEXTS as _NL_CLARIFICATION_CONTEXTS,
    prune_archive_clear_confirmations as _state_prune_archive_clear_confirmations,
    prune_nl_clarification_contexts as _state_prune_nl_clarification_contexts,
    prune_result_cursors as _state_prune_result_cursors,
    record_affinity_touches as _state_record_affinity_touches,
    render_numbered_daily_digest_for_command as _state_render_numbered_daily_digest_for_command,
    render_numbered_weekly_review_for_command as _state_render_numbered_weekly_review_for_command,
    resolve_result_cursor as _state_resolve_result_cursor,
    resolve_result_cursor_with_reason as _state_resolve_result_cursor_with_reason,
    RESULT_CURSORS as _RESULT_CURSORS,
    ResultCursor as _ResultCursor,
    store_archive_clear_confirmation as _state_store_archive_clear_confirmation,
    store_nl_clarification_context as _state_store_nl_clarification_context,
    store_result_cursor as _state_store_result_cursor,
)

_SCHEMA_MAP = {
    "people": Path("config/schemas/derived_event_people_v1.json"),
    "projects": Path("config/schemas/derived_event_projects_v1.json"),
    "ideas": Path("config/schemas/derived_event_ideas_v1.json"),
    "admin": Path("config/schemas/derived_event_admin_v1.json"),
}
_ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS = 120
_DEFAULT_HEALTH_HOST = "0.0.0.0"
_DEFAULT_HEALTH_PORT = 8080
_DUE_TIME_REMINDER_NOTIFY_CONFIG_KEY = "_due_time_reminder_notify"
_DUE_TIME_REMINDER_DEFAULT_OFFSETS_MINUTES = (90, 15)
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
class _NLRouteIntentV1:
    route: str
    intent: str
    risk_tier: str
    confidence: float
    ambiguities: list[str]
    read_command: dict[str, Any] | None
    mutation_plan: dict[str, Any] | None
    clarification: dict[str, Any] | None


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
    return _transport_routing.normalize_nl_mutation_plan_input(payload)


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
    return _transport_routing.normalize_set_fields(
        object_type=object_type,
        field_updates=field_updates,
        routing=routing,
        now=now,
        tz=tz,
    )


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


def _coerce_context_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            return int(trimmed)
    return None


def _build_transport_context(value: TransportMessageContext | Any) -> TransportMessageContext:
    if isinstance(value, TransportMessageContext):
        return value
    channel = getattr(value, "channel", None)
    parent_id = getattr(channel, "parent_id", None)
    created_at = getattr(value, "created_at", None)
    if not isinstance(created_at, datetime):
        created_at = datetime.now(timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return TransportMessageContext(
        source="discord",
        user_id=str(getattr(getattr(value, "author", object()), "id", 0)),
        channel_id=str(getattr(channel, "id", 0)),
        thread_id=str(parent_id) if isinstance(parent_id, int) else None,
        message_id=str(getattr(value, "id", 0)),
        content=str(getattr(value, "content", "") or ""),
        is_dm=isinstance(channel, discord.DMChannel),
        created_at=created_at,
    )


def _cursor_key(context: TransportMessageContext | Any) -> tuple[int, int]:
    if isinstance(context, TransportMessageContext):
        user_id = _coerce_context_id(context.user_id) or 0
        channel_id = _coerce_context_id(context.channel_id) or 0
        return (user_id, channel_id)
    return (int(getattr(context.author, "id", 0)), int(getattr(context.channel, "id", 0)))


def _parent_cursor_key(context: TransportMessageContext | Any) -> tuple[int, int] | None:
    if isinstance(context, TransportMessageContext):
        parent_id = _coerce_context_id(context.thread_id)
        if parent_id is None:
            return None
        user_id = _coerce_context_id(context.user_id) or 0
        return (user_id, parent_id)
    parent_id = getattr(context.channel, "parent_id", None)
    if isinstance(parent_id, int):
        return (int(getattr(context.author, "id", 0)), parent_id)
    return None


def _archive_clear_key(context: TransportMessageContext | Any) -> tuple[int, int]:
    return _cursor_key(context)


def _prune_archive_clear_confirmations(now: datetime | None = None) -> None:
    _state_prune_archive_clear_confirmations(now=now)


def _start_archive_clear_confirmation(context: TransportMessageContext | Any) -> None:
    _state_store_archive_clear_confirmation(
        _archive_clear_key(context),
        ttl_seconds=_ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS,
    )


def _consume_archive_clear_confirmation(context: TransportMessageContext | Any) -> bool:
    return _state_consume_archive_clear_confirmation(_archive_clear_key(context))


def _prune_result_cursors(now: datetime | None = None) -> None:
    _state_prune_result_cursors(now=now)


def _record_affinity_touches(
    key: tuple[int, int],
    object_ids: list[str],
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
) -> None:
    _state_record_affinity_touches(key, object_ids, matching=matching, now=now)


def _load_affinity_scores(
    key: tuple[int, int],
    *,
    matching: MatchingConfig,
    now: datetime | None = None,
) -> dict[str, float]:
    return _state_load_affinity_scores(key, matching=matching, now=now)


def _store_result_cursor(
    context: TransportMessageContext | Any,
    config: dict[str, Any],
    object_ids: list[str],
    *,
    source_view: str = "unknown",
) -> None:
    surfacing = load_surfacing_config(config)
    _state_store_result_cursor(
        _cursor_key(context),
        object_ids,
        ttl_minutes=surfacing.pull_cursor_ttl_minutes,
        source_view=source_view,
    )


def _resolve_result_cursor(context: TransportMessageContext | Any, number: int) -> str | None:
    parent_key = _parent_cursor_key(context)
    fallback_keys: tuple[tuple[int, int], ...] = ()
    if parent_key is not None:
        fallback_keys = (parent_key,)
    return _state_resolve_result_cursor(_cursor_key(context), number, fallback_keys=fallback_keys)


def _resolve_result_cursor_with_reason(
    context: TransportMessageContext | Any,
    number: int,
) -> tuple[str | None, str | None, str | None]:
    parent_key = _parent_cursor_key(context)
    fallback_keys: tuple[tuple[int, int], ...] = ()
    if parent_key is not None:
        fallback_keys = (parent_key,)
    return _state_resolve_result_cursor_with_reason(
        _cursor_key(context),
        number,
        fallback_keys=fallback_keys,
    )


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


def _resolve_command_target(context: TransportMessageContext | Any, target_token: str) -> _CommandTargetResolution:
    number = _parse_positive_int(target_token)
    if number is None:
        return _CommandTargetResolution(
            target_id=target_token,
            error=None,
            reason=None,
            row_number=None,
            source_view=None,
        )

    target_id, reason, source_view = _resolve_result_cursor_with_reason(context, number)
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
    _state_prune_nl_clarification_contexts(now=now)


def _load_nl_clarification_context(context: TransportMessageContext | Any) -> _NLClarificationContext | None:
    return _state_get_nl_clarification_context(_cursor_key(context))


def _store_nl_clarification_context(
    *,
    context: TransportMessageContext | Any | None = None,
    message: TransportMessageContext | Any | None = None,
    raw_event_id: str,
    unresolved_scope: dict[str, dict[str, Any]],
    base_plan_input: dict[str, Any],
) -> None:
    active_context = context if context is not None else message
    if active_context is None:
        return
    _state_store_nl_clarification_context(
        _cursor_key(active_context),
        raw_event_id=raw_event_id,
        unresolved_scope=unresolved_scope,
        base_plan_input=base_plan_input,
        ttl_seconds=_NL_CLARIFICATION_TTL_SECONDS,
    )


def _clear_nl_clarification_context(context: TransportMessageContext | Any) -> None:
    _state_clear_nl_clarification_context(_cursor_key(context))


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


def _render_numbered_daily_digest_for_command(digest: Any) -> tuple[str, list[str]]:
    return _state_render_numbered_daily_digest_for_command(
        digest,
        numbered_command_tip=_NUMBERED_COMMAND_TIP,
    )


def _render_numbered_weekly_review_for_command(review: Any) -> tuple[str, list[str]]:
    return _state_render_numbered_weekly_review_for_command(
        review,
        numbered_command_tip=_NUMBERED_COMMAND_TIP,
    )


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
    message: TransportMessageContext | Any,
    raw_id: str,
    config: dict[str, Any],
    plan_input: dict[str, Any],
    confidence: float,
    routing: NLCommandRoutingConfig,
    source_view: str | None = None,
    allow_clarification: bool = True,
    runtime: "_DiscordRoutingRuntime | None" = None,
) -> bool:
    context = _build_transport_context(message)
    active_runtime = runtime or _DiscordRoutingRuntime(message)
    return await _transport_routing.queue_nl_mutation_confirmation(
        runtime=active_runtime,
        context=context,
        raw_id=raw_id,
        config=config,
        plan_input=plan_input,
        confidence=confidence,
        routing=routing,
        source_view=source_view,
        allow_clarification=allow_clarification,
    )


async def _maybe_route_nl_command(
    *,
    message: Any,
    content: str,
    raw_id: str,
    config: dict[str, Any],
    provider: OpenAIProvider,
    model: str,
) -> bool:
    context = _build_transport_context(message)
    runtime = _DiscordRoutingRuntime(message)
    return await _transport_routing.maybe_route_nl_command(
        runtime=runtime,
        context=context,
        content=content,
        raw_id=raw_id,
        config=config,
        provider=provider,
        model=model,
    )


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
                refresh_index_async=lambda root, db: _refresh_index_async(root, db, matching=matching_config),
                extract_target_ids_from_derived=_extract_target_ids_from_derived,
                extract_ids_from_written_paths=_extract_ids_from_written_paths,
                record_affinity_touches=lambda key, ids, matching: _record_affinity_touches(key, ids, matching=matching),
                now_iso=_now_iso,
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
    except Exception:
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


async def _safe_add_reaction(message: discord.Message, emoji: str) -> None:
    await _discord_safe_add_reaction(message, emoji)


async def _swap_reaction(message: discord.Message, remove_emoji: str, add_emoji: str) -> None:
    await _discord_swap_reaction(message, remove_emoji, add_emoji)


async def _send_response(
    message: discord.Message,
    content: str,
    thread_title: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    await _discord_send_response(
        message,
        content,
        thread_title=thread_title,
        view=view,
    )


class _DiscordRoutingRuntime:
    def __init__(self, message: Any) -> None:
        self._message = message

    def load_prompt(self, path: str) -> str:
        return load_prompt(path)

    async def interpret_text_async(
        self,
        *,
        provider: OpenAIProvider,
        text: str,
        model: str,
        system_prompt: str,
        schema_path: Path,
    ) -> Any:
        return await interpret_text_async(
            provider=provider,
            text=text,
            model=model,
            system_prompt=system_prompt,
            schema_path=schema_path,
        )

    async def handle_command(
        self,
        context: TransportMessageContext,
        content: str,
        raw_id: str,
        config: dict[str, Any],
    ) -> bool:
        del context
        return await _handle_command(self._message, content, raw_id, config)

    async def queue_nl_mutation_confirmation(
        self,
        *,
        context: TransportMessageContext,
        raw_id: str,
        config: dict[str, Any],
        plan_input: dict[str, Any],
        confidence: float,
        routing: NLCommandRoutingConfig,
        source_view: str | None = None,
        allow_clarification: bool = True,
    ) -> bool:
        kwargs: dict[str, Any] = {
            "message": context,
            "raw_id": raw_id,
            "config": config,
            "plan_input": plan_input,
            "confidence": confidence,
            "routing": routing,
            "source_view": source_view,
            "runtime": self,
        }
        if not allow_clarification:
            kwargs["allow_clarification"] = False
        try:
            return await _queue_nl_mutation_confirmation(**kwargs)
        except TypeError as exc:
            if "runtime" not in str(exc):
                raise
            kwargs.pop("runtime", None)
            return await _queue_nl_mutation_confirmation(**kwargs)

    def load_nl_clarification_context(self, context: TransportMessageContext) -> _NLClarificationContext | None:
        return _load_nl_clarification_context(context)

    def clear_nl_clarification_context(self, context: TransportMessageContext) -> None:
        _clear_nl_clarification_context(context)

    def store_nl_clarification_context(
        self,
        *,
        context: TransportMessageContext,
        raw_event_id: str,
        unresolved_scope: dict[str, dict[str, Any]],
        base_plan_input: dict[str, Any],
    ) -> None:
        _store_nl_clarification_context(
            context=context,
            raw_event_id=raw_event_id,
            unresolved_scope=unresolved_scope,
            base_plan_input=base_plan_input,
        )

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        del context
        await _swap_reaction(self._message, remove_emoji, add_emoji)

    async def send_response(
        self,
        context: TransportMessageContext,
        content: str,
        *,
        thread_title: str | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        del context
        await _send_response(
            self._message,
            content,
            thread_title=thread_title,
            view=view,
        )

    def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> _CommandTargetResolution:
        return _resolve_command_target(context, target_token)

    def map_target_resolution_reason_to_plan_reason(self, reason: str | None) -> str:
        return _map_target_resolution_reason_to_plan_reason(reason)

    def log_numbered_mutation_resolution_failed(
        self,
        *,
        raw_event_id: str,
        command: str,
        reason: str,
        row_number: int,
        source_view: str | None = None,
    ) -> None:
        _log_numbered_mutation_resolution_failed(
            raw_event_id=raw_event_id,
            command=command,
            reason=reason,
            row_number=row_number,
            source_view=source_view,
        )

    def log_numbered_mutation_resolved(
        self,
        *,
        raw_event_id: str,
        command: str,
        source_view: str | None,
        row_number: int,
        object_id: str,
    ) -> None:
        _log_numbered_mutation_resolved(
            raw_event_id=raw_event_id,
            command=command,
            source_view=source_view,
            row_number=row_number,
            object_id=object_id,
        )

    def find_object_path(self, objects_root: str | Path, target_id: str) -> Path | None:
        return find_object_path(objects_root, target_id)

    def load_frontmatter(self, path: str | Path) -> dict[str, Any]:
        return load_frontmatter(path)

    def write_nl_mutation_normalized_trace(
        self,
        *,
        config: dict[str, Any],
        raw_event_id: str,
        payload: dict[str, Any],
    ) -> None:
        _write_nl_mutation_normalized_trace(
            config=config,
            raw_event_id=raw_event_id,
            payload=payload,
        )

    def create_mutation_pending_view(
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
    ) -> MutationPendingView:
        return MutationPendingView(
            pending_id=pending_id,
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=index_db,
            author_id=author_id,
            matching=matching,
            affinity_key=affinity_key,
            on_canonical_change=on_canonical_change,
            refresh_index_async=lambda root, db: _refresh_index_async(root, db, matching=matching),
            extract_target_ids_from_derived=_extract_target_ids_from_derived,
            extract_ids_from_written_paths=_extract_ids_from_written_paths,
            record_affinity_touches=lambda key, ids, match: _record_affinity_touches(key, ids, matching=match),
            now_iso=_now_iso,
            log_confirm_applied=lambda pending_id: _log_nl_plan_confirm_applied(pending_id=pending_id),
        )

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        return _cursor_key(context)

    def notify_due_time_reminder_schedule_changed(self, config: dict[str, Any], *, clear_state: bool = False) -> None:
        _notify_due_time_reminder_schedule_changed(config, clear_state=clear_state)

    def now_iso(self) -> str:
        return _now_iso()


class _DiscordCommandRuntime:
    def __init__(self, message: Any) -> None:
        self._message = message

    @property
    def schema_map(self) -> dict[str, Path]:
        return _SCHEMA_MAP

    @property
    def help_copy(self) -> str:
        return _HELP_COPY

    @property
    def help_details(self) -> dict[str, str]:
        return _HELP_DETAILS

    @property
    def numbered_command_tip(self) -> str:
        return _NUMBERED_COMMAND_TIP

    @property
    def numbered_command_tip_with_recent_limit(self) -> str:
        return _NUMBERED_COMMAND_TIP_WITH_RECENT_LIMIT

    def load_matching_config(self, config: dict[str, Any]) -> MatchingConfig:
        return load_matching_config(config)

    def build_daily_digest(self, objects_root: str | Path, config: dict[str, Any]) -> DailyDigest:
        return build_daily_digest(objects_root, config)

    def build_weekly_review(self, objects_root: str | Path, config: dict[str, Any]) -> WeeklyReview:
        return build_weekly_review(objects_root, config)

    def render_numbered_daily_digest_for_command(self, digest: DailyDigest) -> tuple[str, list[str]]:
        return _render_numbered_daily_digest_for_command(digest)

    def render_numbered_weekly_review_for_command(self, review: WeeklyReview) -> tuple[str, list[str]]:
        return _render_numbered_weekly_review_for_command(review)

    def store_result_cursor(
        self,
        context: TransportMessageContext,
        config: dict[str, Any],
        object_ids: list[str],
        *,
        source_view: str = "unknown",
    ) -> None:
        _store_result_cursor(context, config, object_ids, source_view=source_view)

    def parse_positive_int(self, value: str) -> int | None:
        return _parse_positive_int(value)

    def normalize_help_topic(self, value: str) -> str:
        return _normalize_help_topic(value)

    def build_recent_list(
        self,
        objects_root: str | Path,
        config: dict[str, Any],
        *,
        limit: int | None = None,
    ) -> Any:
        return build_recent_list(objects_root, config, limit=limit)

    def build_find_list(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        config: dict[str, Any],
        query: str,
    ) -> Any:
        return build_find_list(objects_root, index_db, config, query)

    def resolve_result_cursor(self, context: TransportMessageContext, number: int) -> str | None:
        return _resolve_result_cursor(context, number)

    def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> _CommandTargetResolution:
        return _resolve_command_target(context, target_token)

    def log_numbered_mutation_resolution_failed(
        self,
        *,
        raw_event_id: str,
        command: str,
        reason: str,
        row_number: int,
        source_view: str | None = None,
    ) -> None:
        _log_numbered_mutation_resolution_failed(
            raw_event_id=raw_event_id,
            command=command,
            reason=reason,
            source_view=source_view,
            row_number=row_number,
        )

    async def apply_command_operation(
        self,
        context: TransportMessageContext,
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
        del context
        return await _apply_command_operation(
            self._message,
            raw_id,
            config,
            target_id,
            op,
            fields,
            validate_fix=validate_fix,
            command_name=command_name,
            row_number=row_number,
            source_view=source_view,
        )

    def start_archive_clear_confirmation(self, context: TransportMessageContext) -> None:
        _start_archive_clear_confirmation(context)

    def load_pending_action(self, root: str | Path, pending_id: str) -> PendingAction | None:
        return load_pending_action(root, pending_id)

    def apply_operations(
        self,
        derived: dict[str, Any],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ) -> Any:
        return apply_operations(
            derived,
            objects_root=objects_root,
            canonical_schema_path=canonical_schema_path,
            derived_schema_path=derived_schema_path,
            last_decision_id=last_decision_id,
        )

    def update_pending_action_status(self, root: str | Path, pending_id: str, status: str) -> PendingAction:
        return update_pending_action_status(root, pending_id, status)

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching: MatchingConfig | None = None,
    ) -> None:
        await _refresh_index_async(objects_root, index_db, matching=matching)

    def notify_due_time_reminder_schedule_changed(self, config: dict[str, Any], *, clear_state: bool = False) -> None:
        _notify_due_time_reminder_schedule_changed(config, clear_state=clear_state)

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        return _extract_target_ids_from_derived(derived)

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        return _extract_ids_from_written_paths(paths)

    def record_affinity_touches(
        self,
        key: tuple[int, int],
        object_ids: list[str],
        *,
        matching: MatchingConfig,
    ) -> None:
        _record_affinity_touches(key, object_ids, matching=matching)

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        return _cursor_key(context)

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        del context
        await _swap_reaction(self._message, remove_emoji, add_emoji)

    async def send_response(
        self,
        context: TransportMessageContext,
        content: str,
        *,
        thread_title: str | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        del context
        await _send_response(
            self._message,
            content,
            thread_title=thread_title,
            view=view,
        )

    def build_item_detail(self, objects_root: str | Path, object_id: str, config: dict[str, Any]) -> str:
        return build_item_detail(objects_root, object_id, config)

    def now_iso(self) -> str:
        return _now_iso()


async def _handle_command(
    message: Any,
    content: str,
    raw_id: str,
    config: dict[str, Any],
) -> bool:
    context = _build_transport_context(message)
    runtime = _DiscordCommandRuntime(message)
    return await _transport_commands.handle_command(
        runtime=runtime,
        context=context,
        content=content,
        raw_id=raw_id,
        config=config,
    )


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
    except Exception:
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


SquireBot = _DiscordAdapterBot


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
        bot = SquireBot(config=config, message_handler=_handle_message)
        bot.run(token)
    finally:
        if health_server:
            health_server.stop()
            logging.info("health_server_stopped")


if __name__ == "__main__":
    main()
