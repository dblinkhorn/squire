"""Transport-agnostic explicit-command orchestration."""

from __future__ import annotations

from difflib import get_close_matches
import logging
import shlex
from pathlib import Path
from typing import Any, Protocol

from squire_core import telemetry
from squire_core.transport.contracts import TransportMessageContext


_RECENT_CATEGORY_ALIASES = {
    "admin": "admin",
    "admins": "admin",
    "project": "projects",
    "projects": "projects",
    "person": "people",
    "people": "people",
    "idea": "ideas",
    "ideas": "ideas",
}

_RECENT_CATEGORY_DISPLAY = {
    "admin": "admin",
    "projects": "project",
    "people": "people",
    "ideas": "idea",
}


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
        context: TransportMessageContext,
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
        object_type: str | None = None,
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

    def resolve_result_cursor(self, context: TransportMessageContext, number: int) -> str | None:
        ...

    def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> _CommandTargetResolutionLike:
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
        ...

    def start_archive_clear_confirmation(self, context: TransportMessageContext) -> None:
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

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        ...

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        ...

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        ...

    def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
        ...

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        ...

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        ...

    async def send_response(
        self,
        context: TransportMessageContext,
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


def _normalize_recent_category(value: str) -> str | None:
    return _RECENT_CATEGORY_ALIASES.get(value.strip().lower())


def _recent_usage() -> str:
    return "Usage: !recent [number] [category]"


def _valid_commands(runtime: CommandRuntime) -> list[str]:
    return [f"!{name}" for name in runtime.help_details.keys()]


def _suggest_command(runtime: CommandRuntime, attempted: str) -> str | None:
    matches = get_close_matches(attempted.lower(), _valid_commands(runtime), n=1, cutoff=0.6)
    if not matches:
        return None
    return matches[0]


def _unknown_command_message(runtime: CommandRuntime, attempted: str) -> str:
    lines = [f"Unknown command: {attempted}."]
    suggestion = _suggest_command(runtime, attempted)
    if suggestion:
        lines[-1] += f" Did you mean {suggestion}?"
    lines.append("")
    lines.append("Run !help for a list of commands.")
    return "\n".join(lines)


async def _send_traced_response(
    runtime: CommandRuntime,
    context: TransportMessageContext,
    content: str,
    *,
    thread_title: str | None = None,
    view: Any = None,
) -> None:
    with telemetry.start_span("response.send"):
        await runtime.send_response(
            context,
            content,
            thread_title=thread_title,
            view=view,
        )


async def handle_command(
    *,
    runtime: CommandRuntime,
    context: TransportMessageContext,
    content: str,
    raw_id: str,
    config: dict[str, Any],
) -> bool:
    parts = content.split()
    if not parts:
        return False
    command = parts[0].lower()
    root_span = telemetry.current_span()
    telemetry.set_span_attributes(
        {
            "squire.command": command,
            "squire.flow": "command",
        },
        span=root_span,
    )
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    with telemetry.start_span("command.dispatch"):
        if command == "!status":
            try:
                with telemetry.start_span("digest.build.status"):
                    digest = runtime.build_daily_digest(objects_root, config)
            except Exception as exc:
                telemetry.record_exception(exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "status_build_failed", span=root_span)
                logging.exception("status_digest_failed id=%s", raw_id)
                await runtime.swap_reaction(context, "⏳", "⚠️")
                await _send_traced_response(runtime, context, "Failed to build status digest. Check logs for details.")
                return True
            rendered, cursor_object_ids = runtime.render_numbered_daily_digest_for_command(digest)
            if cursor_object_ids:
                runtime.store_result_cursor(context, config, cursor_object_ids, source_view="status")
            telemetry.set_span_attribute("squire.outcome", "status_sent", span=root_span)
            await runtime.swap_reaction(context, "⏳", "✅")
            await _send_traced_response(runtime, context, rendered)
            return True
        if command == "!weekly":
            try:
                with telemetry.start_span("review.build.weekly"):
                    review = runtime.build_weekly_review(objects_root, config)
            except Exception as exc:
                telemetry.record_exception(exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "weekly_build_failed", span=root_span)
                logging.exception("weekly_review_build_failed id=%s", raw_id)
                await runtime.swap_reaction(context, "⏳", "⚠️")
                await _send_traced_response(runtime, context, "Failed to build weekly review. Check logs for details.")
                return True
            rendered, cursor_object_ids = runtime.render_numbered_weekly_review_for_command(review)
            if cursor_object_ids:
                runtime.store_result_cursor(context, config, cursor_object_ids, source_view="weekly")
            telemetry.set_span_attribute("squire.outcome", "weekly_sent", span=root_span)
            await runtime.swap_reaction(context, "⏳", "✅")
            await _send_traced_response(runtime, context, rendered)
            return True
    if command == "!help":
        if len(parts) > 2:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !help [command]")
            return True
        if len(parts) == 2:
            topic = runtime.normalize_help_topic(parts[1])
            help_detail = runtime.help_details.get(topic)
            if help_detail is None:
                await runtime.swap_reaction(context, "⏳", "⚠️")
                telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
                await _send_traced_response(runtime, context, f"Unknown command `{parts[1]}`. Run `!help` for a command list.")
                return True
            await runtime.swap_reaction(context, "⏳", "✅")
            telemetry.set_span_attribute("squire.outcome", "help_sent", span=root_span)
            await _send_traced_response(runtime, context, help_detail)
            return True
        await runtime.swap_reaction(context, "⏳", "✅")
        telemetry.set_span_attribute("squire.outcome", "help_sent", span=root_span)
        await _send_traced_response(runtime, context, runtime.help_copy)
        return True
    if command == "!recent":
        limit: int | None = None
        object_type: str | None = None
        if len(parts) > 3:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, _recent_usage())
            return True

        for token in parts[1:]:
            parsed = runtime.parse_positive_int(token)
            if parsed is not None:
                if limit is not None:
                    await runtime.swap_reaction(context, "⏳", "⚠️")
                    telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
                    await _send_traced_response(runtime, context, _recent_usage())
                    return True
                limit = parsed
                continue

            normalized_category = _normalize_recent_category(token)
            if normalized_category is not None:
                if object_type is not None:
                    await runtime.swap_reaction(context, "⏳", "⚠️")
                    telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
                    await _send_traced_response(runtime, context, _recent_usage())
                    return True
                object_type = normalized_category
                continue

            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, _recent_usage())
            return True

        with telemetry.start_span("recent.build"):
            surfaced = runtime.build_recent_list(objects_root, config, limit=limit, object_type=object_type)
        recent_label = _RECENT_CATEGORY_DISPLAY.get(object_type, "recent")
        if not surfaced.lines:
            await runtime.swap_reaction(context, "⏳", "✅")
            if object_type:
                telemetry.set_span_attribute("squire.outcome", "recent_empty", span=root_span)
                await _send_traced_response(runtime, context, f"No recent {recent_label} notes found.")
            else:
                telemetry.set_span_attribute("squire.outcome", "recent_empty", span=root_span)
                await _send_traced_response(runtime, context, "No recent notes found.")
            return True
        runtime.store_result_cursor(context, config, surfaced.object_ids, source_view="recent")
        await runtime.swap_reaction(context, "⏳", "✅")
        header = "Recent notes:"
        if object_type:
            header = f"Recent {recent_label} notes:"
        telemetry.set_span_attribute("squire.outcome", "recent_sent", span=root_span)
        await _send_traced_response(
            runtime,
            context,
            header
            + "\n"
            + "\n".join(surfaced.lines)
            + "\n\n"
            + runtime.numbered_command_tip_with_recent_limit,
        )
        return True
    if command == "!find":
        if len(parts) < 2:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !find <query>")
            return True
        query = content.split(None, 1)[1].strip()
        if not query:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !find <query>")
            return True
        with telemetry.start_span("find.build"):
            surfaced = runtime.build_find_list(objects_root, index_db, config, query)
        if not surfaced.lines:
            await runtime.swap_reaction(context, "⏳", "✅")
            telemetry.set_span_attribute("squire.outcome", "find_empty", span=root_span)
            await _send_traced_response(runtime, context, f'No matches found for "{query}".')
            return True
        runtime.store_result_cursor(context, config, surfaced.object_ids, source_view="find")
        await runtime.swap_reaction(context, "⏳", "✅")
        telemetry.set_span_attribute("squire.outcome", "find_sent", span=root_span)
        await _send_traced_response(
            runtime,
            context,
            "Matches:\n" + "\n".join(surfaced.lines) + "\n\n" + runtime.numbered_command_tip,
        )
        return True
    if command == "!show":
        if len(parts) != 2:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !show <number>")
            return True
        number = runtime.parse_positive_int(parts[1])
        if number is None:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !show <number>")
            return True
        object_id = runtime.resolve_result_cursor(context, number)
        if object_id is None:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "show_missing_cursor", span=root_span)
            await _send_traced_response(
                runtime,
                context,
                "No active result list for that number. Run !recent, !find, !status, or !weekly first.",
            )
            return True
        telemetry.set_span_attribute("squire.row_number", number, span=root_span)
        with telemetry.start_span("show.build"):
            detail = runtime.build_item_detail(objects_root, object_id, config)
        if not detail:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "show_missing_note", span=root_span)
            await _send_traced_response(runtime, context, "That note is no longer available.")
            return True
        await runtime.swap_reaction(context, "⏳", "✅")
        telemetry.set_span_attribute("squire.outcome", "show_sent", span=root_span)
        await _send_traced_response(runtime, context, detail)
        return True
    if command == "!append":
        if len(parts) < 3:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !append <id|number> <text>")
            return True
        target_resolution = runtime.resolve_command_target(context, parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="append",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.source_view", target_resolution.source_view, span=root_span)
            telemetry.set_span_attribute("squire.row_number", target_resolution.row_number, span=root_span)
            telemetry.set_span_attribute("squire.outcome", "target_resolution_failed", span=root_span)
            await _send_traced_response(runtime, context, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !append <id|number> <text>")
            return True
        text = content.split(None, 2)[2].strip()
        if not text:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !append <id|number> <text>")
            return True
        telemetry.set_span_attributes(
            {
                "squire.source_view": target_resolution.source_view,
                "squire.row_number": target_resolution.row_number,
            },
            span=root_span,
        )
        return await runtime.apply_command_operation(
            context,
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
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !done <id|number>")
            return True
        target_resolution = runtime.resolve_command_target(context, parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="done",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "target_resolution_failed", span=root_span)
            await _send_traced_response(runtime, context, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !done <id|number>")
            return True
        telemetry.set_span_attributes(
            {
                "squire.source_view": target_resolution.source_view,
                "squire.row_number": target_resolution.row_number,
            },
            span=root_span,
        )
        return await runtime.apply_command_operation(
            context,
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
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Invalid !fix syntax. Quote values containing spaces.")
            return True
        if len(fix_parts) < 3:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !fix <id|number> <field=value> [field=value ...]")
            return True
        target_resolution = runtime.resolve_command_target(context, fix_parts[1])
        if target_resolution.reason and target_resolution.row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command="fix",
                reason=target_resolution.reason,
                source_view=target_resolution.source_view,
                row_number=target_resolution.row_number,
            )
        if target_resolution.error:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "target_resolution_failed", span=root_span)
            await _send_traced_response(runtime, context, target_resolution.error)
            return True
        target_id = target_resolution.target_id
        if not target_id:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !fix <id|number> <field=value> [field=value ...]")
            return True
        updates: dict[str, Any] = {}
        for token in fix_parts[2:]:
            if "=" not in token:
                await runtime.swap_reaction(context, "⏳", "⚠️")
                telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
                await _send_traced_response(
                    runtime,
                    context,
                    "Invalid !fix syntax. Use field=value and quote values containing spaces.",
                )
                return True
            key, value = token.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                await runtime.swap_reaction(context, "⏳", "⚠️")
                telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
                await _send_traced_response(runtime, context, "Field name cannot be empty.")
                return True
            updates[key] = value
        if not updates:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "No valid fields provided.")
            return True
        telemetry.set_span_attributes(
            {
                "squire.source_view": target_resolution.source_view,
                "squire.row_number": target_resolution.row_number,
            },
            span=root_span,
        )
        return await runtime.apply_command_operation(
            context,
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
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !clear-archive")
            return True
        with telemetry.start_span("archive.clear.confirmation.start"):
            runtime.start_archive_clear_confirmation(context)
        await runtime.swap_reaction(context, "⏳", "❓")
        telemetry.set_span_attribute("squire.outcome", "awaiting_delete_confirmation", span=root_span)
        await _send_traced_response(
            runtime,
            context,
            "This will permanently clear all archive data (except `.git`). Reply with `DELETE` within 2 minutes to confirm.",
        )
        return True
    if command == "!confirm":
        if len(parts) != 2:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !confirm <pending_id>")
            return True
        matching_config = runtime.load_matching_config(config)
        pending_id = parts[1]
        telemetry.set_span_attribute("squire.pending_action_id", pending_id, span=root_span)
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = runtime.load_pending_action(pending_root, pending_id)
        if not pending:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "pending_missing", span=root_span)
            await _send_traced_response(runtime, context, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "pending_not_active", span=root_span)
            await _send_traced_response(runtime, context, f"Pending action {pending_id} is {pending.status}.")
            return True
        object_type = pending.object_type
        schema_path = runtime.schema_map.get(object_type)
        if not schema_path and object_type != "mixed":
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "pending_unsupported_type", span=root_span)
            await _send_traced_response(runtime, context, "Pending action has an unsupported object type.")
            return True
        try:
            with telemetry.start_span("command.pending.apply"):
                result = runtime.apply_operations(
                    pending.derived,
                    objects_root=objects_root,
                    canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                    derived_schema_path=schema_path if schema_path else None,
                    last_decision_id=pending.last_decision_id,
                )
        except Exception as exc:
            telemetry.record_exception(exc, span=root_span)
            telemetry.set_span_attribute("squire.outcome", "pending_apply_failed", span=root_span)
            logging.exception("pending_apply_failed id=%s", pending_id)
            runtime.update_pending_action_status(pending_root, pending_id, "failed")
            await runtime.swap_reaction(context, "⏳", "⚠️")
            await _send_traced_response(runtime, context, "Failed to apply pending action. Check logs for details.")
            return True
        with telemetry.start_span("index.refresh"):
            await runtime.refresh_index_async(objects_root, index_db, matching=matching_config)
        runtime.notify_due_time_reminder_schedule_changed()
        touched_ids = runtime.extract_target_ids_from_derived(pending.derived)
        touched_ids.extend(runtime.extract_ids_from_written_paths(result.written_paths))
        runtime.record_affinity_touches(runtime.cursor_key(context), touched_ids, matching=matching_config)
        runtime.update_pending_action_status(pending_root, pending_id, "confirmed")
        await runtime.swap_reaction(context, "⏳", "✅")
        telemetry.set_span_attribute("squire.outcome", "pending_confirmed", span=root_span)
        await _send_traced_response(
            runtime,
            context,
            f"Applied pending action {pending_id}. ({len(result.written_paths)} item(s) updated.)",
        )
        return True
    if command == "!cancel":
        if len(parts) != 2:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "usage_error", span=root_span)
            await _send_traced_response(runtime, context, "Usage: !cancel <pending_id>")
            return True
        pending_id = parts[1]
        telemetry.set_span_attribute("squire.pending_action_id", pending_id, span=root_span)
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = runtime.load_pending_action(pending_root, pending_id)
        if not pending:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "pending_missing", span=root_span)
            await _send_traced_response(runtime, context, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await runtime.swap_reaction(context, "⏳", "⚠️")
            telemetry.set_span_attribute("squire.outcome", "pending_not_active", span=root_span)
            await _send_traced_response(runtime, context, f"Pending action {pending_id} is {pending.status}.")
            return True
        runtime.update_pending_action_status(pending_root, pending_id, "cancelled")
        await runtime.swap_reaction(context, "⏳", "✅")
        telemetry.set_span_attribute("squire.outcome", "pending_cancelled", span=root_span)
        await _send_traced_response(runtime, context, f"Cancelled pending action {pending_id}.")
        return True
    await runtime.swap_reaction(context, "⏳", "⚠️")
    telemetry.set_span_attribute("squire.outcome", "unknown_command", span=root_span)
    await _send_traced_response(runtime, context, _unknown_command_message(runtime, command))
    return True
