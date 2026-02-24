"""Discord runtime entrypoint and lifecycle wiring."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

import discord
from dotenv import load_dotenv

from squire_core.config_utils import (
    load_config,
    load_matching_config,
    normalize_archive_config,
)
from squire_core.indexer import rebuild_index
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.matching import sync_semantic_index
from squire_core.transport.bootstrap import (
    apply_test_archive_root_override as _apply_test_archive_root_override,
    configure_logging as _configure_logging,
    run_test_mode_reset_seed as _run_test_mode_reset_seed,
)
from squire_core.transport.discord.adapter import DiscordSquireBot
from squire_core.transport.discord import message_entry as _message_entry
from squire_core.transport.health import start_health_server as _start_health_server
from squire_core.transport.state import RuntimeStateStore



DueTimeReminderNotifier = Callable[..., Any]


async def handle_message(
    message: discord.Message,
    config: dict[str, Any],
    runtime_state: RuntimeStateStore,
    due_time_reminder_notifier: DueTimeReminderNotifier | None = None,
) -> None:
    await _message_entry.handle_message(
        message,
        config,
        runtime_state=runtime_state,
        due_time_reminder_notifier=due_time_reminder_notifier,
    )


def main() -> None:
    load_dotenv()
    _configure_logging()
    config_path = Path("config.yaml")
    config = load_config(config_path)
    config = _apply_test_archive_root_override(config)
    try:
        config = normalize_archive_config(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        _run_test_mode_reset_seed(config)
    except Exception as exc:
        logging.exception("test_mode_startup_failed reason=%s", exc)
        raise SystemExit(str(exc)) from exc

    matching_config = load_matching_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    index_path = Path(index_db)
    if not index_path.exists():
        logging.info("index_missing rebuilding index at %s", index_db)
        try:
            rebuild_index(objects_root, index_db)
        except Exception as exc:
            logging.exception("index_rebuild_failed error=%s", exc)
    if matching_config.semantic_weight > 0:
        if matching_config.semantic_provider != "openai":
            logging.warning(
                "semantic_startup_sync_skipped reason=unsupported_provider provider=%s",
                matching_config.semantic_provider,
            )
        else:
            try:
                provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))
                stats = sync_semantic_index(
                    objects_root=objects_root,
                    db_path=index_db,
                    matching_config=matching_config,
                    embedding_provider=provider,
                )
                logging.info(
                    "semantic_startup_sync_ok indexed=%s unchanged=%s removed=%s metadata_reset=%s duration_ms=%s",
                    stats.indexed_count,
                    stats.unchanged_count,
                    stats.removed_count,
                    stats.metadata_reset,
                    stats.duration_ms,
                )
            except Exception as exc:
                logging.warning("semantic_startup_sync_failed error=%s", exc)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is required")

    health_server = _start_health_server()
    runtime_state = RuntimeStateStore()
    due_time_reminder_notifier: DueTimeReminderNotifier | None = None

    async def _handle_message(message: discord.Message, runtime_config: dict[str, Any]) -> None:
        await handle_message(
            message,
            runtime_config,
            runtime_state=runtime_state,
            due_time_reminder_notifier=due_time_reminder_notifier,
        )

    try:
        bot = DiscordSquireBot(config=config, message_handler=_handle_message)
        due_time_reminder_notifier = bot.request_due_time_reminder_schedule_refresh
        bot.run(token)
    finally:
        if health_server:
            health_server.stop()
            logging.info("health_server_stopped")


__all__ = [
    "main",
    "handle_message",
]
