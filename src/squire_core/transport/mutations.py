"""Shared mutation and index-sync helpers for transport runtimes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Protocol

from squire_core.canonical_store import load_frontmatter
from squire_core.config_utils import MatchingConfig
from squire_core.indexer import rebuild_index
from squire_core.llm.registry import get_sync_embedding_provider, provider_name
from squire_core.matching import sync_semantic_index
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.validation import validate_fix_updates


class CommandMutationRuntime(Protocol):
    def load_matching_config(self, config: dict[str, Any]) -> MatchingConfig:
        ...

    def find_object_path(self, objects_root: str | Path, target_id: str) -> Path | None:
        ...

    def load_frontmatter(self, path: str | Path) -> dict[str, Any]:
        ...

    def apply_operations(
        self,
        derived: dict[str, Any],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ) -> Any:
        ...

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching: MatchingConfig | None = None,
    ) -> None:
        ...

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        ...

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        ...

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        ...

    def record_affinity_touches(
        self,
        key: tuple[int, int],
        object_ids: list[str],
        *,
        matching: MatchingConfig,
    ) -> None:
        ...

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
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

    def log_numbered_mutation_resolved(
        self,
        *,
        raw_event_id: str,
        command: str,
        source_view: str | None,
        row_number: int,
        object_id: str,
    ) -> None:
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


def refresh_index(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
    embedding_provider: Any = None,
) -> None:
    try:
        stats = rebuild_index(objects_root, index_db)
        logging.info("index_rebuilt path=%s indexed=%s skipped=%s", index_db, stats.indexed_count, stats.skipped_count)
    except Exception as exc:
        logging.exception("index_rebuild_failed error=%s", exc)
        return

    if not matching or matching.semantic_weight <= 0:
        return
    sync_embedding_provider = get_sync_embedding_provider(embedding_provider)
    if sync_embedding_provider is None:
        logging.warning(
            "semantic_sync_skipped reason=embedding_unavailable provider=%s",
            provider_name(embedding_provider) if embedding_provider is not None else "none",
        )
        return
    try:
        stats = sync_semantic_index(
            objects_root=objects_root,
            db_path=index_db,
            matching_config=matching,
            embedding_provider=sync_embedding_provider,
        )
        logging.info(
            "semantic_sync_ok path=%s indexed=%s unchanged=%s removed=%s metadata_reset=%s duration_ms=%s",
            index_db,
            stats.indexed_count,
            stats.unchanged_count,
            stats.removed_count,
            stats.metadata_reset,
            stats.duration_ms,
        )
    except Exception as exc:
        logging.exception("semantic_sync_failed error=%s", exc)


async def refresh_index_async(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
    embedding_provider: Any = None,
) -> None:
    await asyncio.to_thread(
        refresh_index,
        objects_root,
        index_db,
        matching=matching,
        embedding_provider=embedding_provider,
    )


def extract_target_ids_from_derived(derived: dict[str, Any]) -> list[str]:
    ops = derived.get("proposed_operations") or []
    if not isinstance(ops, list):
        return []
    object_ids: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        target_id = op.get("target_id")
        if isinstance(target_id, str) and target_id.strip():
            object_ids.append(target_id.strip())
    return object_ids


def extract_ids_from_written_paths(
    paths: list[Path],
    *,
    load_frontmatter_fn: Any = None,
) -> list[str]:
    active_load_frontmatter = load_frontmatter_fn or load_frontmatter
    object_ids: list[str] = []
    for path in paths:
        try:
            frontmatter = active_load_frontmatter(path)
        except Exception:
            continue
        object_id = frontmatter.get("id")
        if isinstance(object_id, str) and object_id.strip():
            object_ids.append(object_id.strip())
    return object_ids


def first_title_from_paths(
    paths: list[Path],
    *,
    load_frontmatter_fn: Any = None,
) -> str | None:
    active_load_frontmatter = load_frontmatter_fn or load_frontmatter
    for path in paths:
        try:
            frontmatter = active_load_frontmatter(path)
        except Exception:
            continue
        title = frontmatter.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def titles_from_paths(
    paths: list[Path],
    *,
    load_frontmatter_fn: Any = None,
) -> list[str]:
    active_load_frontmatter = load_frontmatter_fn or load_frontmatter
    titles: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            frontmatter = active_load_frontmatter(path)
        except Exception:
            continue
        title = frontmatter.get("title")
        if not isinstance(title, str):
            continue
        value = title.strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        titles.append(value)
    return titles


def format_apply_success_message(
    *,
    written_paths: list[Path],
    fallback_title: str | None = None,
    load_frontmatter_fn: Any = None,
) -> str:
    titles = titles_from_paths(
        written_paths,
        load_frontmatter_fn=load_frontmatter_fn,
    )
    if not titles and fallback_title:
        titles = [fallback_title]
    if len(titles) >= 1:
        if len(titles) == 1:
            header = "✅ Applied update to 1 note:"
        else:
            header = f"✅ Applied updates to {len(titles)} notes:"
        lines = [header]
        for title in titles[:5]:
            lines.append(f'- "{title}"')
        more_count = len(titles) - 5
        if more_count > 0:
            lines.append(f"- and {more_count} more")
        return "\n".join(lines)
    return f"✅ Applied update. ({len(written_paths)} item(s) updated.)"


async def apply_command_operation(
    *,
    runtime: CommandMutationRuntime,
    context: TransportMessageContext,
    raw_id: str,
    config: dict[str, Any],
    target_id: str,
    op: str,
    fields: dict[str, Any],
    validate_fix: bool = False,
    command_name: str | None = None,
    row_number: int | None = None,
    source_view: str | None = None,
) -> bool:
    matching_config = runtime.load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    target_path = runtime.find_object_path(objects_root, target_id)
    if not target_path:
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, f"Unknown ID: {target_id}")
        return True
    frontmatter = runtime.load_frontmatter(target_path)
    object_type = frontmatter.get("type")
    if not object_type:
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, f"Unable to determine object type for {target_id}")
        return True
    if not isinstance(object_type, str):
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, f"Unable to determine object type for {target_id}")
        return True
    if validate_fix:
        fields, validation_error = validate_fix_updates(object_type, fields)
        if validation_error:
            await runtime.swap_reaction(context, "⏳", "⚠️")
            await runtime.send_response(context, validation_error)
            return True
    if op == "update" and object_type != "admin" and fields.get("status") == "done":
        if command_name and row_number is not None:
            runtime.log_numbered_mutation_resolution_failed(
                raw_event_id=raw_id,
                command=command_name,
                reason="wrong_type",
                source_view=source_view,
                row_number=row_number,
            )
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, "Only admin items can be marked done.")
        return True
    if command_name and row_number is not None:
        runtime.log_numbered_mutation_resolved(
            raw_event_id=raw_id,
            command=command_name,
            source_view=source_view,
            row_number=row_number,
            object_id=target_id,
        )
    derived = {
        "object_type": object_type,
        "raw_event_id": raw_id,
        "extracted_fields": {},
        "proposed_operations": [
            {
                "op": op,
                "target_id": target_id,
                "fields": fields,
            }
        ],
    }
    try:
        result = runtime.apply_operations(
            derived,
            objects_root=objects_root,
            canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
            derived_schema_path=None,
        )
    except Exception:
        logging.exception("command_apply_failed id=%s op=%s", raw_id, op)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await runtime.send_response(context, "Command failed. Check logs for details.")
        return True
    await runtime.refresh_index_async(
        objects_root,
        config.get("paths", {}).get("index_db", "index/sb.sqlite"),
        matching=matching_config,
    )
    runtime.notify_due_time_reminder_schedule_changed()
    touched_ids = runtime.extract_target_ids_from_derived(derived)
    touched_ids.extend(runtime.extract_ids_from_written_paths(result.written_paths))
    runtime.record_affinity_touches(runtime.cursor_key(context), touched_ids, matching=matching_config)
    await runtime.swap_reaction(context, "⏳", "✅")
    title = frontmatter.get("title") or target_id
    await runtime.send_response(
        context,
        f"Updated {object_type} \"{title}\".",
        thread_title=title,
    )
    return True
