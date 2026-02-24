"""Discord-specific context translation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from squire_core.transport.contracts import TransportMessageContext


def build_transport_context(value: TransportMessageContext | Any) -> TransportMessageContext:
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
