from __future__ import annotations

from datetime import datetime, timezone

from squire_core.transport.discord import context as discord_context


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Channel:
    def __init__(self, channel_id: int, *, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _Message:
    def __init__(self) -> None:
        self.author = _Author(11)
        self.channel = _Channel(22, parent_id=33)
        self.id = 44
        self.content = "hello"
        self.created_at = datetime(2026, 2, 23, 12, 0, tzinfo=timezone.utc)


def test_build_transport_context_from_discord_message_shape() -> None:
    built = discord_context.build_transport_context(_Message())

    assert built.source == "discord"
    assert built.user_id == "11"
    assert built.channel_id == "22"
    assert built.thread_id == "33"
    assert built.message_id == "44"
    assert built.content == "hello"
