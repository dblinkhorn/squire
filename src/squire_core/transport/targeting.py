"""Shared target-resolution and cursor-key helpers."""

from __future__ import annotations

from typing import Any

from squire_core.surfacing import load_surfacing_config
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.state import (
    CommandTargetResolution,
    InteractionKey,
    RuntimeStateStore,
    resolve_result_cursor as _state_resolve_result_cursor,
    resolve_result_cursor_with_reason as _state_resolve_result_cursor_with_reason,
    store_result_cursor as _state_store_result_cursor,
)


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


def _parse_positive_int(value: str) -> int | None:
    trimmed = value.strip()
    if not trimmed.isdigit():
        return None
    parsed = int(trimmed)
    if parsed <= 0:
        return None
    return parsed


def cursor_key(context: TransportMessageContext | Any) -> InteractionKey:
    if isinstance(context, TransportMessageContext):
        user_id = _coerce_context_id(context.user_id) or 0
        channel_id = _coerce_context_id(context.channel_id) or 0
        return (user_id, channel_id)
    return (int(getattr(context.author, "id", 0)), int(getattr(context.channel, "id", 0)))


def parent_cursor_key(context: TransportMessageContext | Any) -> InteractionKey | None:
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


def archive_clear_key(context: TransportMessageContext | Any) -> InteractionKey:
    return cursor_key(context)


def store_result_cursor(
    context: TransportMessageContext | Any,
    config: dict[str, Any],
    object_ids: list[str],
    *,
    source_view: str = "unknown",
    state_store: RuntimeStateStore,
) -> None:
    surfacing = load_surfacing_config(config)
    _state_store_result_cursor(
        cursor_key(context),
        object_ids,
        ttl_minutes=surfacing.pull_cursor_ttl_minutes,
        source_view=source_view,
        state_store=state_store,
    )


def resolve_result_cursor(
    context: TransportMessageContext | Any,
    number: int,
    *,
    state_store: RuntimeStateStore,
) -> str | None:
    parent_key = parent_cursor_key(context)
    fallback_keys: tuple[InteractionKey, ...] = ()
    if parent_key is not None:
        fallback_keys = (parent_key,)
    return _state_resolve_result_cursor(
        cursor_key(context),
        number,
        fallback_keys=fallback_keys,
        state_store=state_store,
    )


def resolve_result_cursor_with_reason(
    context: TransportMessageContext | Any,
    number: int,
    *,
    state_store: RuntimeStateStore,
) -> tuple[str | None, str | None, str | None]:
    parent_key = parent_cursor_key(context)
    fallback_keys: tuple[InteractionKey, ...] = ()
    if parent_key is not None:
        fallback_keys = (parent_key,)
    return _state_resolve_result_cursor_with_reason(
        cursor_key(context),
        number,
        fallback_keys=fallback_keys,
        state_store=state_store,
    )


def resolve_command_target(
    context: TransportMessageContext | Any,
    target_token: str,
    *,
    state_store: RuntimeStateStore,
) -> CommandTargetResolution:
    number = _parse_positive_int(target_token)
    if number is None:
        return CommandTargetResolution(
            target_id=target_token,
            error=None,
            reason=None,
            row_number=None,
            source_view=None,
        )

    target_id, reason, source_view = resolve_result_cursor_with_reason(
        context,
        number,
        state_store=state_store,
    )
    if target_id is not None:
        return CommandTargetResolution(
            target_id=target_id,
            error=None,
            reason=None,
            row_number=number,
            source_view=source_view,
        )
    if reason == "out_of_range":
        return CommandTargetResolution(
            target_id=None,
            error="That number is out of range for your last list.",
            reason="out_of_range",
            row_number=number,
            source_view=source_view,
        )
    if reason == "expired":
        return CommandTargetResolution(
            target_id=None,
            error="Your last numbered list expired. Run `!recent`, `!find`, `!status`, or `!weekly` first.",
            reason="expired",
            row_number=number,
            source_view=None,
        )
    return CommandTargetResolution(
        target_id=None,
        error="No active numbered list for that command. Run `!recent`, `!find`, `!status`, or `!weekly` first.",
        reason="no_cursor",
        row_number=number,
        source_view=None,
    )


def map_target_resolution_reason_to_plan_reason(reason: str | None) -> str:
    if reason == "out_of_range":
        return "target_out_of_range"
    if reason == "expired":
        return "target_expired"
    if reason == "no_cursor":
        return "target_no_cursor"
    return "target_missing"
