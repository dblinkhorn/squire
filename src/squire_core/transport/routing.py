"""Transport-agnostic NL routing and mutation normalization module."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from squire_core.config_utils import NLCommandRoutingConfig, load_matching_config, load_nl_command_routing_config
from squire_core.id_utils import generate_prefixed_id
from squire_core.pending_actions import PendingAction, write_pending_action
from squire_core.timezone_utils import resolve_timezone
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.tracing import (
    build_nl_trace_operation as _build_nl_trace_operation,
    build_unresolved_scope_from_entries as _build_unresolved_scope_from_entries,
    summarize_unresolved_scope as _summarize_unresolved_scope,
)
from squire_core.transport.validation import (
    FIX_ALLOWED_FIELDS as _FIX_ALLOWED_FIELDS,
    FIX_DATE_FIELDS as _FIX_DATE_FIELDS,
    FIX_DATETIME_FIELDS as _FIX_DATETIME_FIELDS,
    FIX_ENUM_VALUES as _FIX_ENUM_VALUES,
    validate_fix_updates as _validate_fix_updates,
)

_NL_ROUTE_MEDIUM_CONFIDENCE = 0.5
_NL_INTENT_SCHEMA_PATH = Path("config/schemas/nl_route_intent_v1.json")
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


class _TargetResolutionLike(Protocol):
    target_id: str | None
    error: str | None
    reason: str | None
    row_number: int | None
    source_view: str | None


class _ClarificationContextLike(Protocol):
    unresolved_scope: dict[str, dict[str, Any]]
    base_plan_input: dict[str, Any]


class RoutingRuntime(Protocol):
    def load_prompt(self, path: str) -> str:
        ...

    async def interpret_text_async(
        self,
        *,
        provider: Any,
        text: str,
        model: str,
        system_prompt: str,
        schema_path: Path,
    ) -> Any:
        ...

    async def handle_command(
        self,
        context: TransportMessageContext,
        content: str,
        raw_id: str,
        config: dict[str, Any],
    ) -> bool:
        ...

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
        ...

    def load_nl_clarification_context(self, context: TransportMessageContext) -> _ClarificationContextLike | None:
        ...

    def clear_nl_clarification_context(self, context: TransportMessageContext) -> None:
        ...

    def store_nl_clarification_context(
        self,
        *,
        context: TransportMessageContext,
        raw_event_id: str,
        unresolved_scope: dict[str, dict[str, Any]],
        base_plan_input: dict[str, Any],
    ) -> None:
        ...

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        ...

    async def send_response(
        self,
        context: TransportMessageContext,
        content: str,
        *,
        thread_title: str | None = None,
        view: Any = None,
    ) -> None:
        ...

    def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> _TargetResolutionLike:
        ...

    def map_target_resolution_reason_to_plan_reason(self, reason: str | None) -> str:
        ...

    def log_numbered_mutation_resolution_failed(
        self,
        *,
        raw_event_id: str,
        command: str,
        reason: str,
        row_number: int,
        source_view: str | None = None,
    ) -> None:
        ...

    def log_numbered_mutation_resolved(
        self,
        *,
        raw_event_id: str,
        command: str,
        source_view: str | None,
        row_number: int,
        object_id: str,
    ) -> None:
        ...

    def find_object_path(self, objects_root: str | Path, target_id: str) -> Path | None:
        ...

    def load_frontmatter(self, path: str | Path) -> dict[str, Any]:
        ...

    def write_nl_mutation_normalized_trace(
        self,
        *,
        config: dict[str, Any],
        raw_event_id: str,
        payload: dict[str, Any],
    ) -> None:
        ...

    def create_mutation_pending_view(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        author_id: int,
        matching: Any,
        affinity_key: tuple[int, int],
        on_canonical_change: Callable[[], None] | None = None,
    ) -> Any:
        ...

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        ...

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        ...

    def now_iso(self) -> str:
        ...


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            return int(trimmed)
    return None


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
    logging.info("nl_route_blocked raw_event_id=%s intent=%s reason=%s", raw_event_id, intent, reason)


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


def _log_nl_plan_unresolved_cancelled(*, raw_event_id: str, count: int) -> None:
    logging.info("nl_plan_unresolved_cancelled raw_event_id=%s count=%s", raw_event_id, count)


def _log_nl_clarification_scope_blocked(*, raw_event_id: str) -> None:
    logging.info("nl_clarification_scope_blocked raw_event_id=%s", raw_event_id)


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


def normalize_nl_mutation_plan_input(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    raw_operations = payload.get("operations")
    operation_items: list[Any]
    if isinstance(raw_operations, list):
        operation_items = raw_operations
    else:
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
        requires_clarification = bool(requires_clarification_raw) if isinstance(requires_clarification_raw, bool) else False
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
    elif hour < 0 or hour > 23:
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


def _resolve_field_for_update(*, object_type: str, update: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
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


def normalize_set_fields(
    *,
    object_type: str,
    field_updates: list[Any],
    routing: NLCommandRoutingConfig,
    now: datetime,
    tz: tzinfo,
) -> tuple[dict[str, str] | None, str | None, list[str]]:
    del routing  # Reserved for future routing-aware normalization policies.
    normalized: dict[str, str] = {}
    notes: list[str] = []
    if not field_updates:
        return None, "field_unknown", notes
    for raw_update in field_updates:
        if not isinstance(raw_update, dict):
            continue
        field_id, field_error, field_notes = _resolve_field_for_update(object_type=object_type, update=raw_update)
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


def _command_name_for_action_type(action_type: str) -> str:
    mapping = {"mark_done": "done", "append_body": "append", "set_fields": "fix"}
    return mapping.get(action_type, "fix")


def _action_phrase_for_entry(entry: dict[str, Any]) -> str:
    action_type = _coerce_non_empty_str(entry.get("action_type")) or "set_fields"
    title = _coerce_non_empty_str(entry.get("target_title")) or _coerce_non_empty_str(entry.get("target_resolved_id")) or "item"
    if action_type == "mark_done":
        return f'Mark "{title}" done'
    if action_type == "append_body":
        return f'Append to "{title}"'
    return f'Update "{title}"'


async def queue_nl_mutation_confirmation(
    *,
    runtime: RoutingRuntime,
    context: TransportMessageContext,
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
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, "I can apply that update, but I need which actions to run.")
        return True

    _log_nl_plan_generated(raw_event_id=raw_id, action_type="multi_operation")
    matching_config = load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    tz = resolve_timezone(config.get("timezone"))
    now_local = datetime.now(tz)
    now_iso = runtime.now_iso()
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

            target_resolution = runtime.resolve_command_target(context, target_token)
            entry["_row_number"] = target_resolution.row_number
            entry["_source_view"] = target_resolution.source_view or source_view
            if target_resolution.reason and target_resolution.row_number is not None:
                runtime.log_numbered_mutation_resolution_failed(
                    raw_event_id=raw_id,
                    command=command_name,
                    reason=target_resolution.reason,
                    source_view=target_resolution.source_view or source_view,
                    row_number=target_resolution.row_number,
                )
            if target_resolution.error:
                entry["reason_code"] = runtime.map_target_resolution_reason_to_plan_reason(target_resolution.reason)
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

            target_path = runtime.find_object_path(objects_root, target_id)
            if not target_path:
                entry["reason_code"] = "target_unknown_id"
                entry["normalization_notes"] = [f"unknown_id:{target_id}"]
                normalized_entries.append(entry)
                continue
            frontmatter = runtime.load_frontmatter(target_path)
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
                normalized_fields, reason_code, notes = normalize_set_fields(
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
            runtime.write_nl_mutation_normalized_trace(
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
        runtime.store_nl_clarification_context(
            context=context,
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
        await runtime.swap_reaction(context, "⏳", "❓")
        await runtime.send_response(
            context,
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
            runtime.write_nl_mutation_normalized_trace(
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
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, "\n".join(lines))
        return True

    for entry in resolved_entries:
        command_name = _coerce_non_empty_str(entry.get("_command_name")) or "fix"
        row_number = entry.get("_row_number")
        if not isinstance(row_number, int):
            continue
        target_id = _coerce_non_empty_str(entry.get("target_resolved_id"))
        if target_id is None:
            continue
        runtime.log_numbered_mutation_resolved(
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
        runtime.write_nl_mutation_normalized_trace(
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
    try:
        author_id = int(context.user_id)
    except (TypeError, ValueError):
        author_id = 0
    view = runtime.create_mutation_pending_view(
        pending_id=pending_id,
        pending_root=pending_root,
        objects_root=objects_root,
        index_db=index_db,
        author_id=author_id,
        matching=matching_config,
        affinity_key=runtime.cursor_key(context),
        on_canonical_change=lambda: runtime.notify_due_time_reminder_schedule_changed(),
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
    await runtime.swap_reaction(context, "⏳", "❓")
    await runtime.send_response(
        context,
        "\n".join(lines),
        thread_title=thread_title,
        view=view,
    )
    return True


async def maybe_route_nl_command(
    *,
    runtime: RoutingRuntime,
    context: TransportMessageContext,
    content: str,
    raw_id: str,
    config: dict[str, Any],
    provider: Any,
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
        prompt = runtime.load_prompt(prompt_path)
    except OSError as exc:
        logging.warning("nl_route_prompt_load_failed path=%s error=%s", prompt_path, exc)
        return False

    try:
        interpretation = await runtime.interpret_text_async(
            provider=provider,
            text=content,
            model=model,
            system_prompt=prompt,
            schema_path=_NL_INTENT_SCHEMA_PATH,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "InterpretationValidationError":
            logging.warning("nl_route_invalid id=%s error=%s", raw_id, exc)
        else:
            logging.warning("nl_route_failed id=%s error=%s", raw_id, exc)
        return False

    payload = interpretation.derived if isinstance(getattr(interpretation, "derived", None), dict) else {}
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

    clarification_context = runtime.load_nl_clarification_context(context)
    if clarification_context is not None:
        runtime.clear_nl_clarification_context(context)
        if route.route != "mutation_plan":
            _log_nl_clarification_scope_blocked(raw_event_id=raw_id)
            _log_nl_plan_unresolved_cancelled(raw_event_id=raw_id, count=len(clarification_context.unresolved_scope))
            await runtime.swap_reaction(context, "⏳", "⚠️")
            await runtime.send_response(
                context,
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
        clarification_plan_input, clarification_error = normalize_nl_mutation_plan_input(clarification_raw_plan)
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
            await runtime.swap_reaction(context, "⏳", "⚠️")
            await runtime.send_response(
                context,
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
        return await runtime.queue_nl_mutation_confirmation(
            context=context,
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
        await runtime.swap_reaction(context, "⏳", "❓")
        await runtime.send_response(context, _explicit_only_guidance_for_intent(intent))
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
            await runtime.swap_reaction(context, "⏳", "❓")
            await runtime.send_response(context, clarification)
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
                await runtime.swap_reaction(context, "⏳", "❓")
                await runtime.send_response(context, clarification)
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
            return await runtime.handle_command(context, command, raw_id, config)
        if routing.clarify_on_ambiguous and band == "medium":
            options = clarification_options or [f"Run `{command}`", "Save this as a note"]
            clarification = _format_nl_clarification_message(
                question=clarification_question,
                options=options,
                fallback="Did you want me to run this command or capture a note?",
            )
            _log_nl_route_clarified(raw_event_id=raw_id, options=options)
            _log_nl_route_evaluated(
                raw_event_id=raw_id,
                route_result="clarified",
                intent=read_intent,
                risk_tier="read",
                confidence_band=band,
                mapped_command=command,
            )
            await runtime.swap_reaction(context, "⏳", "❓")
            await runtime.send_response(context, clarification)
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
            await runtime.swap_reaction(context, "⏳", "❓")
            await runtime.send_response(
                context,
                "Natural-language mutations are disabled. Use explicit commands like `!done`, `!append`, or `!fix`.",
            )
            return True

        raw_plan = route.mutation_plan or {}
        plan_input, plan_error = normalize_nl_mutation_plan_input(raw_plan)
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
                await runtime.swap_reaction(context, "⏳", "❓")
                await runtime.send_response(context, clarification)
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
            return await runtime.queue_nl_mutation_confirmation(
                context=context,
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
            await runtime.swap_reaction(context, "⏳", "❓")
            await runtime.send_response(context, clarification)
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
