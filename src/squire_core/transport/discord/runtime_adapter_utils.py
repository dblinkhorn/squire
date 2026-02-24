"""Shared utility helpers for Discord runtime adapter modules."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from squire_core.canonical_store import load_frontmatter
from squire_core.config_utils import MatchingConfig
from squire_core.transport import mutations as _transport_mutations


def refresh_index(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
) -> None:
    _transport_mutations.refresh_index(
        objects_root,
        index_db,
        matching=matching,
    )


async def refresh_index_async(
    objects_root: str | Path,
    index_db: str | Path,
    *,
    matching: MatchingConfig | None = None,
) -> None:
    await asyncio.to_thread(
        refresh_index,
        objects_root,
        index_db,
        matching=matching,
    )


def extract_target_ids_from_derived(derived: dict[str, Any]) -> list[str]:
    return _transport_mutations.extract_target_ids_from_derived(derived)


def extract_ids_from_written_paths(paths: list[Path]) -> list[str]:
    return _transport_mutations.extract_ids_from_written_paths(
        paths,
        load_frontmatter_fn=load_frontmatter,
    )


def log_numbered_mutation_resolved(
    *,
    raw_event_id: str,
    command: str,
    source_view: str | None,
    row_number: int,
    object_id: str,
) -> None:
    logging.info(
        "numbered_mutation_resolved raw_event_id=%s command=%s source_view=%s row_number=%s object_id=%s",
        raw_event_id,
        command,
        source_view or "unknown",
        row_number,
        object_id,
    )


def log_numbered_mutation_resolution_failed(
    *,
    raw_event_id: str,
    command: str,
    reason: str,
    row_number: int,
    source_view: str | None = None,
) -> None:
    logging.info(
        "numbered_mutation_resolution_failed raw_event_id=%s command=%s reason=%s source_view=%s row_number=%s",
        raw_event_id,
        command,
        reason,
        source_view or "unknown",
        row_number,
    )


def log_nl_plan_confirm_applied(*, pending_id: str) -> None:
    logging.info("nl_plan_confirm_applied pending_id=%s", pending_id)
