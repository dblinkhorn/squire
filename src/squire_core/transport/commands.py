"""Transport-agnostic explicit-command orchestration."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any, Protocol


class _CommandTargetResolutionLike(Protocol):
    target_id: str | None
    error: str | None
    reason: str | None
    row_number: int | None
    source_view: str | None


class _SurfacedListLike(Protocol):
    lines: list[str]
    object_ids: list[str]


class _PendingActionLike(Protocol):
    status: str
    object_type: str
    derived: dict[str, Any]
    last_decision_id: str | None


class _ApplyResultLike(Protocol):
    written_paths: list[Path]


class CommandRuntime(Protocol):
    @property
    def schema_map(self) -> dict[str, Path]:
        ...

    @property
    def help_copy(self) -> str:
        ...

    @property
    def help_details(self) -> dict[str, str]:
        ...

    @property
    def numbered_command_tip(self) -> str:
        ...

    @property
    def numbered_command_tip_with_recent_limit(self) -> str:
        ...

    def load_matching_config(self, config: dict[str, Any]) -> Any:
        ...

    def build_daily_digest(self, objects_root: str | Path, config: dict[str, Any]) -> Any:
        ...

    def build_weekly_review(self, objects_root: str | Path, config: dict[str, Any]) -> Any:
        ...

    def render_numbered_daily_digest_for_command(self, digest: Any) -> tuple[str, list[str]]:
        ...

    def render_numbered_weekly_review_for_command(self, review: Any) -> tuple[str, list[str]]:
        ...

    def store_result_cursor(
        self,
        message: Any,
        config: dict[str, Any],
        object_ids: list[str],
        *,
        source_view: str = "unknown",
    ) -> None:
        ...

    def parse_positive_int(self, value: str) -> int | None:
        ...

    def normalize_help_topic(self, value: str) -> str:
        ...

    def build_recent_list(
        self,
        objects_root: str | Path,
        config: dict[str, Any],
        *,
        limit: int | None = None,
    ) -> _SurfacedListLike:
        ...

    def build_find_list(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        config: dict[str, Any],
        query: str,
    ) -> _SurfacedListLike:
        ...

    def resolve_result_cursor(self, message: Any, number: int) -> str | None:
        ...

    def resolve_command_target(self, message: Any, target_token: str) -> _CommandTargetResolutionLike:
        ...

    def log_numbered_mutation_resolution_failed(
        self,
        *,
        raw_event_id: str,
        command: str,
        reason: str,
        row_number: int,
        source_view: str | None = None,
    ) -> None:
        ...

    async def apply_command_operation(
        self,
        message: Any,
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
        ...

    def start_archive_clear_confirmation(self, message: Any) -> None:
        ...

    def load_pending_action(self, root: str | Path, pending_id: str) -> _PendingActionLike | None:
        ...

    def apply_operations(
        self,
        derived: dict[str, Any],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ) -> _ApplyResultLike:
        ...

    def update_pending_action_status(self, root: str | Path, pending_id: str, status: str) -> Any:
        ...

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching: Any = None,
    ) -> None:
        ...

    def notify_due_time_reminder_schedule_changed(self, config: dict[str, Any], *, clear_state: bool = False) -> None:
        ...

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        ...

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        ...

    def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
        ...

    def cursor_key(self, message: Any) -> tuple[int, int]:
        ...

    async def swap_reaction(self, message: Any, remove_emoji: str, add_emoji: str) -> None:
        ...

    async def send_response(
        self,
        message: Any,
        content: str,
        *,
        thread_title: str | None = None,
        view: Any = None,
    ) -> None:
        ...

    def build_item_detail(self, objects_root: str | Path, object_id: str, config: dict[str, Any]) -> str:
        ...

    def now_iso(self) -> str:
        ...


async def handle_command(
    *,
    runtime: CommandRuntime,
    message: Any,
    content: str,
    raw_id: str,
    config: dict[str, Any],
) -> bool:
    parts = content.split()
    if not parts:
        return False
    command = parts[0].lower()
    matching_config = runtime.load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    if command == "!status":
        try:
            digest = runtime.build_daily_digest(objects_root, config)
        except Exception:
            logging.exception("status_digest_failed id=%s", raw_id)
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Failed to build status digest. Check logs for details.")
            return True
        rendered, cursor_object_ids = runtime.render_numbered_daily_digest_for_command(digest)
        if cursor_object_ids:
            runtime.store_result_cursor(message, config, cursor_object_ids, source_view="status")
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(message, rendered)
        return True
    if command == "!weekly":
        try:
            review = runtime.build_weekly_review(objects_root, config)
        except Exception:
            logging.exception("weekly_review_build_failed id=%s", raw_id)
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Failed to build weekly review. Check logs for details.")
            return True
        rendered, cursor_object_ids = runtime.render_numbered_weekly_review_for_command(review)
        if cursor_object_ids:
            runtime.store_result_cursor(message, config, cursor_object_ids, source_view="weekly")
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(message, rendered)
        return True
    if command == "!help":
        if len(parts) > 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !help [command]")
            return True
        if len(parts) == 2:
            topic = runtime.normalize_help_topic(parts[1])
            help_detail = runtime.help_details.get(topic)
            if help_detail is None:
                await runtime.swap_reaction(message, "⏳", "⚠️")
                await runtime.send_response(message, f"Unknown command `{parts[1]}`. Run `!help` for a command list.")
                return True
            await runtime.swap_reaction(message, "⏳", "✅")
            await runtime.send_response(message, help_detail)
            return True
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(message, runtime.help_copy)
        return True
    if command == "!recent":
        limit: int | None = None
        if len(parts) > 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !recent [number]")
            return True
        if len(parts) == 2:
            parsed = runtime.parse_positive_int(parts[1])
            if parsed is None:
                await runtime.swap_reaction(message, "⏳", "⚠️")
                await runtime.send_response(message, "Usage: !recent [number]")
                return True
            limit = parsed
        surfaced = runtime.build_recent_list(objects_root, config, limit=limit)
        if not surfaced.lines:
            await runtime.swap_reaction(message, "⏳", "✅")
            await runtime.send_response(message, "No recent notes found.")
            return True
        runtime.store_result_cursor(message, config, surfaced.object_ids, source_view="recent")
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(
            message,
            "Recent notes:\n"
            + "\n".join(surfaced.lines)
            + "\n\n"
            + runtime.numbered_command_tip_with_recent_limit,
        )
        return True
    if command == "!find":
        if len(parts) < 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !find <query>")
            return True
        query = content.split(None, 1)[1].strip()
        if not query:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !find <query>")
            return True
        surfaced = runtime.build_find_list(objects_root, index_db, config, query)
        if not surfaced.lines:
            await runtime.swap_reaction(message, "⏳", "✅")
            await runtime.send_response(message, f'No matches found for "{query}".')
            return True
        runtime.store_result_cursor(message, config, surfaced.object_ids, source_view="find")
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(
            message,
            "Matches:\n" + "\n".join(surfaced.lines) + "\n\n" + runtime.numbered_command_tip,
        )
        return True
    if command == "!show":
        if len(parts) != 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !show <number>")
            return True
        number = runtime.parse_positive_int(parts[1])
        if number is None:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !show <number>")
            return True
        object_id = runtime.resolve_result_cursor(message, number)
        if object_id is None:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(
                message,
                "No active result list for that number. Run !recent, !find, !status, or !weekly first.",
            )
            return True
        detail = runtime.build_item_detail(objects_root, object_id, config)
        if not detail:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "That note is no longer available.")
            return True
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(message, detail)
        return True
    if command == "!append":
        if len(parts) < 3:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !append <id|number> <text>")
            return True
        target_resolution = runtime.resolve_command_target(message, parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="append",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !append <id|number> <text>")
            return True
        text = content.split(None, 2)[2].strip()
        if not text:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !append <id|number> <text>")
            return True
        return await runtime.apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="append",
            fields={"body": text},
            command_name="append",
            row_number=target_resolution.row_number,
            source_view=target_resolution.source_view,
        )
    if command == "!done":
        if len(parts) != 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !done <id|number>")
            return True
        target_resolution = runtime.resolve_command_target(message, parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="done",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !done <id|number>")
            return True
        return await runtime.apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="update",
            fields={"status": "done", "completed_at": runtime.now_iso()},
            command_name="done",
            row_number=target_resolution.row_number,
            source_view=target_resolution.source_view,
        )
    if command == "!fix":
        try:
            fix_parts = shlex.split(content)
        except ValueError:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Invalid !fix syntax. Quote values containing spaces.")
            return True
        if len(fix_parts) < 3:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !fix <id|number> <field=value> [field=value ...]")
            return True
        target_resolution = runtime.resolve_command_target(message, fix_parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="fix",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !fix <id|number> <field=value> [field=value ...]")
            return True
        updates: dict[str, Any] = {}
        for token in fix_parts[2:]:
            if "=" not in token:
                await runtime.swap_reaction(message, "⏳", "⚠️")
                await runtime.send_response(
                    message,
                    "Invalid !fix syntax. Use field=value and quote values containing spaces.",
                )
                return True
            key, value = token.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                await runtime.swap_reaction(message, "⏳", "⚠️")
                await runtime.send_response(message, "Field name cannot be empty.")
                return True
            updates[key] = value
        if not updates:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "No valid fields provided.")
            return True
        return await runtime.apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="update",
            fields=updates,
            validate_fix=True,
            command_name="fix",
            row_number=target_resolution.row_number,
            source_view=target_resolution.source_view,
        )
    if command == "!clear-archive":
        if len(parts) != 1:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !clear-archive")
            return True
        runtime.start_archive_clear_confirmation(message)
        await runtime.swap_reaction(message, "⏳", "❓")
        await runtime.send_response(
            message,
            "This will permanently clear all archive data (except `.git`). Reply with `DELETE` within 2 minutes to confirm.",
        )
        return True
    if command == "!confirm":
        if len(parts) != 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !confirm <pending_id>")
            return True
        pending_id = parts[1]
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = runtime.load_pending_action(pending_root, pending_id)
        if not pending:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, f"Pending action {pending_id} is {pending.status}.")
            return True
        object_type = pending.object_type
        schema_path = runtime.schema_map.get(object_type)
        if not schema_path and object_type != "mixed":
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Pending action has an unsupported object type.")
            return True
        try:
            result = runtime.apply_operations(
                pending.derived,
                objects_root=objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=schema_path if schema_path else None,
                last_decision_id=pending.last_decision_id,
            )
        except Exception:
            logging.exception("pending_apply_failed id=%s", pending_id)
            runtime.update_pending_action_status(pending_root, pending_id, "failed")
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Failed to apply pending action. Check logs for details.")
            return True
        await runtime.refresh_index_async(objects_root, index_db, matching=matching_config)
        runtime.notify_due_time_reminder_schedule_changed(config)
        touched_ids = runtime.extract_target_ids_from_derived(pending.derived)
        touched_ids.extend(runtime.extract_ids_from_written_paths(result.written_paths))
        runtime.record_affinity_touches(runtime.cursor_key(message), touched_ids, matching=matching_config)
        runtime.update_pending_action_status(pending_root, pending_id, "confirmed")
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(
            message,
            f"Applied pending action {pending_id}. ({len(result.written_paths)} item(s) updated.)",
        )
        return True
    if command == "!cancel":
        if len(parts) != 2:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, "Usage: !cancel <pending_id>")
            return True
        pending_id = parts[1]
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = runtime.load_pending_action(pending_root, pending_id)
        if not pending:
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await runtime.swap_reaction(message, "⏳", "⚠️")
            await runtime.send_response(message, f"Pending action {pending_id} is {pending.status}.")
            return True
        runtime.update_pending_action_status(pending_root, pending_id, "cancelled")
        await runtime.swap_reaction(message, "⏳", "✅")
        await runtime.send_response(message, f"Cancelled pending action {pending_id}.")
        return True
    return False
