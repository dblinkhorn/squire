"""Shared target-resolution and cursor-key helpers."""

from __future__ import annotations

from typing import Any

from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.state import (
    CommandTargetResolution,
    InteractionKey,
    ResultCursorKey,
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


def result_cursor_key(context: TransportMessageContext | Any) -> ResultCursorKey:
    if isinstance(context, TransportMessageContext):
        return _coerce_context_id(context.thread_id) or _coerce_context_id(context.channel_id) or 0
    channel = getattr(context, "channel", None)
    parent_id = getattr(channel, "parent_id", None)
    if isinstance(parent_id, int):
        return parent_id
    return int(getattr(channel, "id", 0))


def archive_clear_key(context: TransportMessageContext | Any) -> InteractionKey:
    return cursor_key(context)


def store_result_cursor(
    context: TransportMessageContext | Any,
    object_ids: list[str],
    *,
    source_view: str = "unknown",
    state_store: RuntimeStateStore,
) -> None:
    _state_store_result_cursor(
        result_cursor_key(context),
        object_ids,
        source_view=source_view,
        state_store=state_store,
    )


def resolve_result_cursor(
    context: TransportMessageContext | Any,
    number: int,
    *,
    state_store: RuntimeStateStore,
) -> str | None:
    return _state_resolve_result_cursor(
        result_cursor_key(context),
        number,
        state_store=state_store,
    )


def resolve_result_cursor_with_reason(
    context: TransportMessageContext | Any,
    number: int,
    *,
    state_store: RuntimeStateStore,
) -> tuple[str | None, str | None, str | None]:
    return _state_resolve_result_cursor_with_reason(
        result_cursor_key(context),
        number,
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
    return CommandTargetResolution(
        target_id=None,
        error="No active numbered list for that command. Run `!recent`, `!active`, `!find`, `!status`, or `!weekly` first.",
        reason="no_cursor",
        row_number=number,
        source_view=None,
    )


def map_target_resolution_reason_to_plan_reason(reason: str | None) -> str:
    if reason == "out_of_range":
        return "target_out_of_range"
    if reason == "no_cursor":
        return "target_no_cursor"
    return "target_missing"
