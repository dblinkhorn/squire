"""Discord inbound runtime adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from squire_core.config_utils import MatchingConfig
from squire_core.interpreter import interpret_text_async
from squire_core.llm.provider import AsyncLLMProvider, LLMProvider
from squire_core.llm.prompts import load_prompt
from squire_core.operation_apply import apply_operations
from squire_core.transport import routing as _transport_routing
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord import io as _discord_io
from squire_core.transport.discord.command_contract import format_pending_message, now_iso
from squire_core.transport.discord.runtime_adapter_command import (
    _extract_ids_from_written_paths,
    _extract_target_ids_from_derived,
    _refresh_index_async,
)
from squire_core.transport.discord.views import AutoApplyFeedbackView, PendingActionView
from squire_core.transport.reminders import invoke_due_time_reminder_notifier as _invoke_due_time_reminder_notifier
from squire_core.transport.state import (
    RuntimeStateStore,
    load_affinity_scores as _state_load_affinity_scores,
    record_affinity_touches as _state_record_affinity_touches,
)
from squire_core.transport.targeting import cursor_key as _cursor_key
from squire_core.transport.tracing import write_matching_trace as _write_matching_trace


RoutingRuntimeFactory = Callable[[Any, RuntimeStateStore, Callable[..., Any] | None], Any]


class _DiscordInboundRuntime:
    def __init__(
        self,
        message: Any,
        state_store: RuntimeStateStore,
        routing_runtime_factory: RoutingRuntimeFactory,
        llm_provider: Any = None,
        embedding_provider: Any = None,
        due_time_reminder_notifier: Callable[..., Any] | None = None,
    ) -> None:
        self._message = message
        self._state_store = state_store
        self._routing_runtime_factory = routing_runtime_factory
        self._embedding_provider = embedding_provider or llm_provider
        self._due_time_reminder_notifier = due_time_reminder_notifier

    async def maybe_route_nl_command(
        self,
        *,
        context: TransportMessageContext,
        content: str,
        raw_id: str,
        config: dict[str, Any],
        provider: LLMProvider | AsyncLLMProvider,
        model: str,
    ) -> bool:
        routing_runtime = self._routing_runtime_factory(
            self._message,
            self._state_store,
            self._due_time_reminder_notifier,
        )
        return await _transport_routing.maybe_route_nl_command(
            runtime=routing_runtime,
            context=context,
            content=content,
            raw_id=raw_id,
            config=config,
            provider=provider,
            model=model,
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
        view: Any = None,
    ) -> None:
        del context
        await _discord_io.send_response(
            self._message,
            content,
            thread_title=thread_title,
            view=view,
        )

    async def send_unrecognized_category(self, context: TransportMessageContext) -> None:
        del context
        await self._message.channel.send("Unrecognized category. Please use a prefix.")

    def load_prompt(self, path: str) -> str:
        return load_prompt(path)

    async def interpret_text_async(
        self,
        *,
        provider: LLMProvider | AsyncLLMProvider,
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

    def now_iso(self) -> str:
        return now_iso()

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        return _cursor_key(context)

    def load_affinity_scores(self, key: tuple[int, int], *, matching: MatchingConfig) -> dict[str, float]:
        return _state_load_affinity_scores(
            key,
            matching=matching,
            state_store=self._state_store,
        )

    def write_matching_trace(
        self,
        *,
        derived_root: str | Path,
        raw_event_id: str,
        trace_payload: dict[str, Any],
    ) -> None:
        _write_matching_trace(
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            trace_payload=trace_payload,
        )

    def apply_operations(
        self,
        derived: dict[str, Any],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ) -> Any:
        return apply_operations(
            derived,
            objects_root=objects_root,
            canonical_schema_path=canonical_schema_path,
            derived_schema_path=derived_schema_path,
            last_decision_id=last_decision_id,
        )

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching: MatchingConfig | None = None,
    ) -> None:
        await _refresh_index_async(
            objects_root,
            index_db,
            matching=matching,
            embedding_provider=self._embedding_provider,
        )

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        _invoke_due_time_reminder_notifier(
            self._due_time_reminder_notifier,
            clear_state=clear_state,
        )

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        return _extract_target_ids_from_derived(derived)

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        return _extract_ids_from_written_paths(paths)

    def record_affinity_touches(
        self,
        key: tuple[int, int],
        object_ids: list[str],
        *,
        matching: MatchingConfig,
    ) -> None:
        _state_record_affinity_touches(
            key,
            object_ids,
            matching=matching,
            state_store=self._state_store,
        )

    def author_id(self, context: TransportMessageContext) -> int:
        del context
        return int(getattr(self._message.author, "id", 0))

    def create_pending_action_view(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        schema_path: Path | None,
        author_id: int,
        candidates: list[dict[str, Any]],
        default_target_id: str | None,
        matching: MatchingConfig,
        affinity_key: tuple[int, int],
        config: dict[str, Any],
    ) -> Any:
        del config
        if schema_path is None:
            return None
        return PendingActionView(
            pending_id=pending_id,
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=index_db,
            schema_path=schema_path,
            author_id=author_id,
            candidates=candidates,
            default_target_id=default_target_id,
            matching=matching,
            affinity_key=affinity_key,
            on_canonical_change=lambda: _invoke_due_time_reminder_notifier(self._due_time_reminder_notifier),
            refresh_index_async=lambda root, db: _refresh_index_async(
                root,
                db,
                matching=matching,
                embedding_provider=self._embedding_provider,
            ),
            extract_target_ids_from_derived=_extract_target_ids_from_derived,
            extract_ids_from_written_paths=_extract_ids_from_written_paths,
            record_affinity_touches=lambda key, ids, match: _state_record_affinity_touches(
                key,
                ids,
                matching=match,
                state_store=self._state_store,
            ),
            now_iso=now_iso,
        )

    def format_pending_message(self, pending_id: str, decision_payload: dict[str, Any]) -> str:
        return format_pending_message(pending_id, decision_payload)

    def create_auto_apply_feedback_view(self, *, author_id: int, target_id: str) -> Any:
        return AutoApplyFeedbackView(author_id=author_id, target_id=target_id)
