"""Shared archive-clear confirmation helpers."""

from __future__ import annotations

from typing import Any

from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.state import (
    RuntimeStateStore,
    consume_archive_clear_confirmation as _state_consume_archive_clear_confirmation,
    store_archive_clear_confirmation as _state_store_archive_clear_confirmation,
)
from squire_core.transport.targeting import archive_clear_key as _archive_clear_key

ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS = 120


def archive_clear_key(context: TransportMessageContext | Any) -> tuple[int, int]:
    return _archive_clear_key(context)


def start_archive_clear_confirmation(
    context: TransportMessageContext | Any,
    *,
    ttl_seconds: int = ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS,
    state_store: RuntimeStateStore,
) -> None:
    _state_store_archive_clear_confirmation(
        archive_clear_key(context),
        ttl_seconds=ttl_seconds,
        state_store=state_store,
    )


def consume_archive_clear_confirmation(
    context: TransportMessageContext | Any,
    *,
    state_store: RuntimeStateStore,
) -> bool:
    return _state_consume_archive_clear_confirmation(
        archive_clear_key(context),
        state_store=state_store,
    )


__all__ = [
    "ARCHIVE_CLEAR_CONFIRM_TTL_SECONDS",
    "archive_clear_key",
    "start_archive_clear_confirmation",
    "consume_archive_clear_confirmation",
]
