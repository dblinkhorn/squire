"""Discord command runtime adapter."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import discord

from squire_core.canonical_store import find_object_path, load_frontmatter
from squire_core.config_utils import MatchingConfig, load_matching_config
from squire_core.operation_apply import apply_operations
from squire_core.pending_actions import load_pending_action, update_pending_action_status
from squire_core.surfacing import (
    build_daily_digest,
    build_find_list,
    build_item_detail,
    build_recent_list,
    build_weekly_review,
)
from squire_core.transport import mutations as _transport_mutations
from squire_core.transport.archive_clear import start_archive_clear_confirmation as _start_archive_clear_confirmation
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord import io as _discord_io
from squire_core.transport.discord.command_contract import (
    HELP_COPY,
    HELP_DETAILS,
    NUMBERED_COMMAND_TIP,
    NUMBERED_COMMAND_TIP_WITH_RECENT_LIMIT,
    SCHEMA_MAP,
    normalize_help_topic,
    now_iso,
    parse_positive_int,
)
from squire_core.transport.discord.runtime_adapter_utils import (
    extract_ids_from_written_paths as _extract_ids_from_written_paths_util,
    extract_target_ids_from_derived as _extract_target_ids_from_derived_util,
    log_numbered_mutation_resolved as _log_numbered_mutation_resolved_util,
    log_numbered_mutation_resolution_failed as _log_numbered_mutation_resolution_failed_util,
    refresh_index as _refresh_index_util,
)
from squire_core.transport.reminders import invoke_due_time_reminder_notifier as _invoke_due_time_reminder_notifier
from squire_core.transport.state import (
    RuntimeStateStore,
    record_affinity_touches as _state_record_affinity_touches,
    render_numbered_daily_digest_for_command as _state_render_numbered_daily_digest_for_command,
    render_numbered_weekly_review_for_command as _state_render_numbered_weekly_review_for_command,
)
from squire_core.transport.targeting import (
    cursor_key as _cursor_key,
    resolve_command_target as _resolve_command_target,
    resolve_result_cursor as _resolve_result_cursor,
    store_result_cursor as _store_result_cursor,
)


# Module-level seams intentionally kept for focused test patching.
def _refresh_index(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
    embedding_provider: Any = None,
) -> None:
    _refresh_index_util(
        objects_root,
        index_db,
        matching=matching,
        embedding_provider=embedding_provider,
    )


async def _refresh_index_async(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
    embedding_provider: Any = None,
) -> None:
    await asyncio.to_thread(
        _refresh_index,
        objects_root,
        index_db,
        matching=matching,
        embedding_provider=embedding_provider,
    )


def _extract_target_ids_from_derived(derived: dict[str, Any]) -> list[str]:
    return _extract_target_ids_from_derived_util(derived)


def _extract_ids_from_written_paths(paths: list[Path]) -> list[str]:
    return _extract_ids_from_written_paths_util(paths)


def _log_numbered_mutation_resolved(
    *,
    raw_event_id: str,
    command: str,
    source_view: str | None,
    row_number: int,
    object_id: str,
) -> None:
    _log_numbered_mutation_resolved_util(
        raw_event_id=raw_event_id,
        command=command,
        source_view=source_view,
        row_number=row_number,
        object_id=object_id,
    )


def _log_numbered_mutation_resolution_failed(
    *,
    raw_event_id: str,
    command: str,
    reason: str,
    row_number: int,
    source_view: str | None = None,
) -> None:
    _log_numbered_mutation_resolution_failed_util(
        raw_event_id=raw_event_id,
        command=command,
        reason=reason,
        source_view=source_view,
        row_number=row_number,
    )


class _DiscordCommandRuntime:
    def __init__(
        self,
        message: Any,
        state_store: RuntimeStateStore,
        llm_provider: Any = None,
        embedding_provider: Any = None,
        due_time_reminder_notifier: Callable[..., Any] | None = None,
    ) -> None:
        self._message = message
        self._state_store = state_store
        self._embedding_provider = embedding_provider or llm_provider
        self._due_time_reminder_notifier = due_time_reminder_notifier

    @property
    def schema_map(self) -> dict[str, Path]:
        return SCHEMA_MAP

    @property
    def help_copy(self) -> str:
        return HELP_COPY

    @property
    def help_details(self) -> dict[str, str]:
        return HELP_DETAILS

    @property
    def numbered_command_tip(self) -> str:
        return NUMBERED_COMMAND_TIP

    @property
    def numbered_command_tip_with_recent_limit(self) -> str:
        return NUMBERED_COMMAND_TIP_WITH_RECENT_LIMIT

    def load_matching_config(self, config: dict[str, Any]) -> MatchingConfig:
        return load_matching_config(config)

    def build_daily_digest(self, objects_root: str | Path, config: dict[str, Any]) -> Any:
        return build_daily_digest(objects_root, config)

    def build_weekly_review(self, objects_root: str | Path, config: dict[str, Any]) -> Any:
        return build_weekly_review(objects_root, config)

    def render_numbered_daily_digest_for_command(self, digest: Any) -> tuple[str, list[str]]:
        return _state_render_numbered_daily_digest_for_command(
            digest,
            numbered_command_tip=NUMBERED_COMMAND_TIP,
        )

    def render_numbered_weekly_review_for_command(self, review: Any) -> tuple[str, list[str]]:
        return _state_render_numbered_weekly_review_for_command(
            review,
            numbered_command_tip=NUMBERED_COMMAND_TIP,
        )

    def store_result_cursor(
        self,
        context: TransportMessageContext,
        config: dict[str, Any],
        object_ids: list[str],
        *,
        source_view: str = "unknown",
    ) -> None:
        _store_result_cursor(
            context,
            config,
            object_ids,
            source_view=source_view,
            state_store=self._state_store,
        )

    def parse_positive_int(self, value: str) -> int | None:
        return parse_positive_int(value)

    def normalize_help_topic(self, value: str) -> str:
        return normalize_help_topic(value)

    def build_recent_list(
        self,
        objects_root: str | Path,
        config: dict[str, Any],
        *,
        limit: int | None = None,
    ) -> Any:
        return build_recent_list(objects_root, config, limit=limit)

    def build_find_list(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        config: dict[str, Any],
        query: str,
    ) -> Any:
        return build_find_list(objects_root, index_db, config, query)

    def resolve_result_cursor(self, context: TransportMessageContext, number: int) -> str | None:
        return _resolve_result_cursor(
            context,
            number,
            state_store=self._state_store,
        )

    def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> Any:
        return _resolve_command_target(
            context,
            target_token,
            state_store=self._state_store,
        )

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
            source_view=source_view,
            row_number=row_number,
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

    async def apply_command_operation(
        self,
        context: TransportMessageContext,
        raw_id: str,
        config: dict[str, Any],
        target_id: str,
        op: str,
        fields: dict[str, Any],
        *,
        validate_fix: bool = False,
        command_name: str | None = None,
        row_number: int | None = None,
        source_view: str | None = None,
    ) -> bool:
        return await _transport_mutations.apply_command_operation(
            runtime=self,
            context=context,
            raw_id=raw_id,
            config=config,
            target_id=target_id,
            op=op,
            fields=fields,
            validate_fix=validate_fix,
            command_name=command_name,
            row_number=row_number,
            source_view=source_view,
        )

    def start_archive_clear_confirmation(self, context: TransportMessageContext) -> None:
        _start_archive_clear_confirmation(
            context,
            state_store=self._state_store,
        )

    def load_pending_action(self, root: str | Path, pending_id: str) -> Any | None:
        return load_pending_action(root, pending_id)

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

    def update_pending_action_status(self, root: str | Path, pending_id: str, status: str) -> Any:
        return update_pending_action_status(root, pending_id, status)

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

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        return _cursor_key(context)

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

    def build_item_detail(self, objects_root: str | Path, object_id: str, config: dict[str, Any]) -> str:
        return build_item_detail(objects_root, object_id, config)

    def now_iso(self) -> str:
        return now_iso()
