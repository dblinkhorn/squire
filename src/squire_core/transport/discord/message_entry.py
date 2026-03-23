"""Discord message-entry orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import discord
from opentelemetry.trace import SpanKind

from squire_core.config_utils import load_llm_config
from squire_core.id_utils import generate_prefixed_id
from squire_core.llm.provider import AsyncLLMProvider, LLMProvider
from squire_core.llm.registry import create_provider
from squire_core.raw_event import RawEvent, Source, write_raw_event
from squire_core import telemetry
from squire_core.transport import commands as _transport_commands
from squire_core.transport import inbound as _transport_inbound
from squire_core.transport import routing as _transport_routing
from squire_core.transport.archive_clear import consume_archive_clear_confirmation
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord.command_contract import SCHEMA_MAP
from squire_core.transport.discord.context import build_transport_context
from squire_core.transport.discord import io as _discord_io
from squire_core.transport.discord.runtime_adapter_command import _DiscordCommandRuntime
from squire_core.transport.discord.runtime_adapter_inbound import _DiscordInboundRuntime
from squire_core.transport.discord.runtime_adapter_routing import _DiscordRoutingRuntime
from squire_core.transport.state import (
    RuntimeStateStore,
    clear_runtime_state as _clear_runtime_state,
    prune_nl_clarification_contexts,
)
from squire_core.transport.bootstrap import clear_archive_contents as _clear_archive_contents
from squire_core.transport.reminders import (
    invoke_due_time_reminder_notifier as _invoke_due_time_reminder_notifier,
)


DueTimeReminderNotifier = Callable[..., Any]


def _generate_raw_id() -> str:
    return generate_prefixed_id("R_")


def _build_command_runtime(
    message: Any,
    runtime_state: RuntimeStateStore,
    llm_provider: Any,
    embedding_provider: Any,
    due_time_reminder_notifier: DueTimeReminderNotifier | None,
) -> _DiscordCommandRuntime:
    return _DiscordCommandRuntime(
        message,
        runtime_state,
        llm_provider=llm_provider,
        embedding_provider=embedding_provider,
        due_time_reminder_notifier=due_time_reminder_notifier,
    )


def _build_routing_runtime(
    message: Any,
    runtime_state: RuntimeStateStore,
    llm_provider: Any,
    embedding_provider: Any,
    due_time_reminder_notifier: DueTimeReminderNotifier | None,
) -> _DiscordRoutingRuntime:
    command_runtime_factory = (
        lambda current_message, state_store, notifier: _build_command_runtime(
            current_message,
            state_store,
            llm_provider,
            embedding_provider,
            notifier,
        )
    )
    return _DiscordRoutingRuntime(
        message,
        runtime_state,
        command_runtime_factory=command_runtime_factory,
        llm_provider=llm_provider,
        embedding_provider=embedding_provider,
        due_time_reminder_notifier=due_time_reminder_notifier,
    )


def _build_inbound_runtime(
    message: Any,
    runtime_state: RuntimeStateStore,
    llm_provider: Any,
    embedding_provider: Any,
    due_time_reminder_notifier: DueTimeReminderNotifier | None,
) -> _DiscordInboundRuntime:
    routing_runtime_factory = (
        lambda current_message, state_store, notifier: _build_routing_runtime(
            current_message,
            state_store,
            llm_provider,
            embedding_provider,
            notifier,
        )
    )
    return _DiscordInboundRuntime(
        message,
        runtime_state,
        routing_runtime_factory=routing_runtime_factory,
        llm_provider=llm_provider,
        embedding_provider=embedding_provider,
        due_time_reminder_notifier=due_time_reminder_notifier,
    )


async def _safe_add_reaction(message: discord.Message, emoji: str) -> None:
    await _discord_io.safe_add_reaction(message, emoji)


async def _send_response(
    message: discord.Message,
    content: str,
    thread_title: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    await _discord_io.send_response(
        message,
        content,
        thread_title=thread_title,
        view=view,
    )


async def handle_command(
    message: Any,
    content: str,
    raw_id: str,
    config: dict[str, Any],
    *,
    runtime_state: RuntimeStateStore,
    llm_provider: Any = None,
    embedding_provider: Any = None,
    due_time_reminder_notifier: DueTimeReminderNotifier | None = None,
) -> bool:
    context = build_transport_context(message)
    runtime = _build_command_runtime(
        message,
        runtime_state,
        llm_provider,
        embedding_provider,
        due_time_reminder_notifier,
    )
    return await _transport_commands.handle_command(
        runtime=runtime,
        context=context,
        content=content,
        raw_id=raw_id,
        config=config,
    )


async def triage_message(
    *,
    message: Any,
    content: str,
    raw_id: str,
    config: dict[str, Any],
    provider: LLMProvider | AsyncLLMProvider,
    model: str,
    runtime_state: RuntimeStateStore,
    embedding_provider: LLMProvider | AsyncLLMProvider | None = None,
    due_time_reminder_notifier: DueTimeReminderNotifier | None = None,
) -> bool:
    context = build_transport_context(message)
    runtime = _build_routing_runtime(
        message,
        runtime_state,
        provider,
        embedding_provider or provider,
        due_time_reminder_notifier,
    )
    triage_outcome = await _transport_routing.triage_message(
        runtime=runtime,
        context=context,
        content=content,
        raw_id=raw_id,
        config=config,
        provider=provider,
        model=model,
    )
    return triage_outcome.handled


async def handle_archive_clear_confirmation(
    message: discord.Message,
    config: dict[str, Any],
    *,
    runtime_state: RuntimeStateStore,
    due_time_reminder_notifier: DueTimeReminderNotifier | None = None,
) -> bool:
    if not consume_archive_clear_confirmation(message, state_store=runtime_state):
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
    _clear_runtime_state(state_store=runtime_state)
    _invoke_due_time_reminder_notifier(
        due_time_reminder_notifier,
        clear_state=True,
    )
    await _safe_add_reaction(message, "✅")
    await _send_response(message, f"Archive cleared. Removed {removed} top-level entries from `{archive_root}`.")
    return True


async def handle_message(
    message: discord.Message,
    config: dict[str, Any],
    *,
    runtime_state: RuntimeStateStore,
    llm_provider: LLMProvider | AsyncLLMProvider | None = None,
    embedding_provider: LLMProvider | AsyncLLMProvider | None = None,
    llm_model: str | None = None,
    due_time_reminder_notifier: DueTimeReminderNotifier | None = None,
) -> None:
    if message.author.bot:
        return

    content = (message.content or "").strip()
    if not content:
        return

    context: TransportMessageContext = build_transport_context(message)
    prune_nl_clarification_contexts(state_store=runtime_state)
    if content == "DELETE":
        with telemetry.start_span(
            "discord.message.archive_clear_confirm",
            kind=SpanKind.CONSUMER,
            attributes=_message_span_attributes(context),
        ) as root_span:
            telemetry.set_span_attribute("squire.flow", "archive_clear_confirm", span=root_span)
            telemetry.set_span_attribute("squire.outcome", "started", span=root_span)
            handled = await handle_archive_clear_confirmation(
                message,
                config,
                runtime_state=runtime_state,
                due_time_reminder_notifier=due_time_reminder_notifier,
            )
            if handled:
                telemetry.set_span_attribute("squire.outcome", "handled", span=root_span)
                return

    await _safe_add_reaction(message, "⏳")

    raw_dir = Path(config.get("paths", {}).get("events_raw", "events/raw"))
    raw_id = _generate_raw_id()
    span_name = "discord.message.command" if content.startswith("!") else "discord.message.capture"
    with telemetry.start_span(
        span_name,
        kind=SpanKind.CONSUMER,
        attributes=_message_span_attributes(context, raw_id=raw_id),
    ) as root_span:
        telemetry.set_span_attribute("squire.flow", "command" if content.startswith("!") else "capture", span=root_span)
        telemetry.set_span_attribute("squire.outcome", "started", span=root_span)
        raw_event = RawEvent(
            raw_event_id=raw_id,
            source=Source.discord,
            source_message_id=str(message.id),
            timestamp=message.created_at.isoformat(),
            text=content,
        )
        with telemetry.start_span("event.raw.write") as raw_write_span:
            telemetry.set_span_attribute("squire.raw_id", raw_id, span=raw_write_span)
            write_raw_event(raw_event, raw_dir)
        logging.info(
            "raw_event_written id=%s source=discord source_message_id=%s",
            raw_id,
            message.id,
        )

        if content.startswith("!"):
            handled = await handle_command(
                message,
                content,
                raw_id,
                config,
                runtime_state=runtime_state,
                llm_provider=llm_provider,
                embedding_provider=embedding_provider,
                due_time_reminder_notifier=due_time_reminder_notifier,
            )
            if handled:
                return

        try:
            llm_config = load_llm_config(config)
        except ValueError as exc:
            telemetry.record_exception(exc, span=root_span)
            telemetry.set_span_attribute("squire.outcome", "config_error", span=root_span)
            await _discord_io.swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, str(exc))
            return
        model = llm_model or llm_config.model
        if not model:
            telemetry.set_span_attribute("squire.outcome", "config_error", span=root_span)
            await _discord_io.swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "No interpreter model configured.")
            return

        provider = llm_provider
        if provider is None:
            try:
                provider = create_provider(llm_config.provider)
            except Exception as exc:
                telemetry.record_exception(exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "provider_init_failed", span=root_span)
                await _discord_io.swap_reaction(message, "⏳", "⚠️")
                await _send_response(message, f"Failed to initialize configured LLM provider: {exc}")
                return
        active_embedding_provider = embedding_provider or provider
        runtime = _build_inbound_runtime(
            message,
            runtime_state,
            provider,
            active_embedding_provider,
            due_time_reminder_notifier,
        )
        await _transport_inbound.handle_non_command_message(
            runtime=runtime,
            context=context,
            content=content,
            raw_id=raw_id,
            config=config,
            provider=provider,
            model=model,
            schema_map=SCHEMA_MAP,
            embedding_provider=active_embedding_provider,
        )


def _message_span_attributes(
    context: TransportMessageContext,
    *,
    raw_id: str | None = None,
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "squire.transport": "discord",
        "discord.message_id": context.message_id,
        "discord.channel_id": context.channel_id,
        "discord.user_id": context.user_id,
        "squire.is_dm": context.is_dm,
    }
    if context.thread_id:
        attributes["discord.thread_id"] = context.thread_id
    if raw_id:
        attributes["squire.raw_id"] = raw_id
    return attributes


__all__ = [
    "handle_message",
    "handle_command",
    "triage_message",
    "handle_archive_clear_confirmation",
]
