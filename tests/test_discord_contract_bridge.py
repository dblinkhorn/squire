from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport import commands as transport_commands
from squire_core.transport import routing as transport_routing
from squire_core.transport.discord import message_entry
from squire_core.transport.state import RuntimeStateStore


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.bot = False


class _Channel:
    def __init__(self, channel_id: int, *, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _Message:
    def __init__(
        self,
        *,
        content: str = "hello",
        user_id: int = 101,
        channel_id: int = 202,
        parent_id: int | None = 303,
        message_id: int = 404,
    ) -> None:
        self.author = _Author(user_id)
        self.channel = _Channel(channel_id, parent_id=parent_id)
        self.content = content
        self.id = message_id
        self.created_at = datetime(2026, 2, 22, 12, 0, tzinfo=timezone.utc)


def test_discord_handle_command_builds_transport_context(monkeypatch) -> None:
    captured: dict[str, object] = {}
    runtime_state = RuntimeStateStore()

    async def _fake_handle_command(*, runtime, context, content, raw_id, config):
        del runtime, raw_id, config
        captured["context"] = context
        captured["content"] = content
        return True

    monkeypatch.setattr(transport_commands, "handle_command", _fake_handle_command)

    message = _Message(content="!help")
    handled = asyncio.run(
        message_entry.handle_command(
            message,
            message.content,
            "R_1",
            {},
            runtime_state=runtime_state,
        )
    )

    assert handled is True
    assert captured["content"] == "!help"
    context = captured["context"]
    assert isinstance(context, TransportMessageContext)
    assert context.source == "discord"
    assert context.user_id == "101"
    assert context.channel_id == "202"
    assert context.thread_id == "303"
    assert context.message_id == "404"


def test_discord_nl_router_builds_transport_context(monkeypatch) -> None:
    captured: dict[str, object] = {}
    runtime_state = RuntimeStateStore()

    async def _fake_maybe_route_nl_command(*, runtime, context, content, raw_id, config, provider, model):
        del runtime, raw_id, config, provider, model
        captured["context"] = context
        captured["content"] = content
        return True

    monkeypatch.setattr(transport_routing, "maybe_route_nl_command", _fake_maybe_route_nl_command)

    message = _Message(content="mark item done")
    handled = asyncio.run(
        message_entry.maybe_route_nl_command(
            message=message,
            content=message.content,
            raw_id="R_2",
            config={},
            provider=SimpleNamespace(),
            model="gpt-5-mini",
            runtime_state=runtime_state,
        )
    )

    assert handled is True
    assert captured["content"] == "mark item done"
    context = captured["context"]
    assert isinstance(context, TransportMessageContext)
    assert context.source == "discord"
    assert context.user_id == "101"
    assert context.channel_id == "202"
    assert context.thread_id == "303"
    assert context.message_id == "404"
