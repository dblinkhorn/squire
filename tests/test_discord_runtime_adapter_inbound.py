from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord import runtime_adapter_inbound
from squire_core.transport.state import RuntimeStateStore


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Channel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _Message:
    def __init__(self) -> None:
        self.author = _Author(1)
        self.channel = _Channel(2)


def _context() -> TransportMessageContext:
    return TransportMessageContext(
        source="discord",
        user_id="1",
        channel_id="2",
        thread_id=None,
        message_id="3",
        content="test",
        is_dm=True,
        created_at=datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc),
    )


def test_send_unrecognized_category_sends_expected_response(monkeypatch) -> None:
    sent: list[str] = []

    async def _fake_send_response(message, content, thread_title=None, view=None) -> None:
        del message, thread_title, view
        sent.append(content)

    monkeypatch.setattr(runtime_adapter_inbound._discord_io, "send_response", _fake_send_response)
    runtime = runtime_adapter_inbound._DiscordInboundRuntime(
        _Message(),
        RuntimeStateStore(),
        lambda *args, **kwargs: None,
    )

    asyncio.run(runtime.send_unrecognized_category(_context()))

    assert sent == ["Unrecognized category. Please use a prefix."]
