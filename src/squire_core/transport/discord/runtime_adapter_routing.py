"""Discord routing runtime adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import discord

from squire_core.canonical_store import find_object_path, load_frontmatter
from squire_core.config_utils import NLCommandRoutingConfig
from squire_core.interpreter import interpret_text_async
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.prompts import load_prompt
from squire_core.transport import commands as _transport_commands
from squire_core.transport import routing as _transport_routing
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord import io as _discord_io
from squire_core.transport.discord.command_contract import now_iso
from squire_core.transport.discord.runtime_adapter_command import (
    _extract_ids_from_written_paths,
    _extract_target_ids_from_derived,
    _log_numbered_mutation_resolved,
    _log_numbered_mutation_resolution_failed,
    _refresh_index_async,
)
from squire_core.transport.discord.runtime_adapter_utils import log_nl_plan_confirm_applied as _log_nl_plan_confirm_applied
from squire_core.transport.discord.views import MutationPendingView
from squire_core.transport.reminders import invoke_due_time_reminder_notifier as _invoke_due_time_reminder_notifier
from squire_core.transport.state import (
    RuntimeStateStore,
    clear_nl_clarification_context as _state_clear_nl_clarification_context,
    get_nl_clarification_context as _state_get_nl_clarification_context,
    record_affinity_touches as _state_record_affinity_touches,
    store_nl_clarification_context as _state_store_nl_clarification_context,
)
from squire_core.transport.targeting import (
    cursor_key as _cursor_key,
    map_target_resolution_reason_to_plan_reason as _map_target_resolution_reason_to_plan_reason,
    resolve_command_target as _resolve_command_target,
)
from squire_core.transport.tracing import write_nl_mutation_normalized_trace as _write_nl_mutation_normalized_trace

_NL_CLARIFICATION_TTL_SECONDS = 600
CommandRuntimeFactory = Callable[[Any, RuntimeStateStore, Callable[..., Any] | None], Any]


class _DiscordRoutingRuntime:
    def __init__(
        self,
        message: Any,
        state_store: RuntimeStateStore,
        command_runtime_factory: CommandRuntimeFactory,
        due_time_reminder_notifier: Callable[..., Any] | None = None,
    ) -> None:
        self._message = message
        self._state_store = state_store
        self._command_runtime_factory = command_runtime_factory
        self._due_time_reminder_notifier = due_time_reminder_notifier

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
        command_runtime = self._command_runtime_factory(
            self._message,
            self._state_store,
            self._due_time_reminder_notifier,
        )
        return await _transport_commands.handle_command(
            runtime=command_runtime,
            context=context,
            content=content,
            raw_id=raw_id,
            config=config,
        )

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
        return await _transport_routing.queue_nl_mutation_confirmation(
            runtime=self,
            context=context,
            raw_id=raw_id,
            config=config,
            plan_input=plan_input,
            confidence=confidence,
            routing=routing,
            source_view=source_view,
            allow_clarification=allow_clarification,
        )

    def load_nl_clarification_context(self, context: TransportMessageContext) -> Any | None:
        return _state_get_nl_clarification_context(
            _cursor_key(context),
            state_store=self._state_store,
        )

    def clear_nl_clarification_context(self, context: TransportMessageContext) -> None:
        _state_clear_nl_clarification_context(
            _cursor_key(context),
            state_store=self._state_store,
        )

    def store_nl_clarification_context(
        self,
        *,
        context: TransportMessageContext,
        raw_event_id: str,
        unresolved_scope: dict[str, dict[str, Any]],
        base_plan_input: dict[str, Any],
    ) -> None:
        _state_store_nl_clarification_context(
            _cursor_key(context),
            raw_event_id=raw_event_id,
            unresolved_scope=unresolved_scope,
            base_plan_input=base_plan_input,
            ttl_seconds=_NL_CLARIFICATION_TTL_SECONDS,
            state_store=self._state_store,
        )

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        del context
        await _discord_io.swap_reaction(self._message, remove_emoji, add_emoji)

    async def send_response(
        self,
        context: TransportMessageContext,
        content: str,
        *,
        thread_title: str | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        del context
        await _discord_io.send_response(
            self._message,
            content,
            thread_title=thread_title,
            view=view,
        )

    def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> Any:
        return _resolve_command_target(
            context,
            target_token,
            state_store=self._state_store,
        )

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
        matching,
        affinity_key: tuple[int, int],
        on_canonical_change: Callable[[], None] | None = None,
    ) -> Any:
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
            record_affinity_touches=lambda key, ids, match: _state_record_affinity_touches(
                key,
                ids,
                matching=match,
                state_store=self._state_store,
            ),
            now_iso=now_iso,
            log_confirm_applied=lambda pending_id: _log_nl_plan_confirm_applied(pending_id=pending_id),
        )

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        return _cursor_key(context)

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        _invoke_due_time_reminder_notifier(
            self._due_time_reminder_notifier,
            clear_state=clear_state,
        )

    def now_iso(self) -> str:
        return now_iso()
