"""Shared pending interaction orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from squire_core import telemetry
from squire_core.config_utils import MatchingConfig
from squire_core.decision_flow import DecisionRouting, apply_decision_to_derived
from squire_core.pending_actions import PendingAction
from squire_core.transport.mutations import format_apply_success_message, titles_from_paths

_CANONICAL_SCHEMA_PATH = Path("config/schemas/canonical_object_v1.json")

NowIsoFn = Callable[[], str]
LogConfirmAppliedFn = Callable[[str], None]


class PendingInteractionRuntime(Protocol):
    def load_pending_action(self, root: str | Path, pending_id: str) -> PendingAction | None:
        ...

    def write_pending_action(self, pending: PendingAction, root: str | Path) -> Path:
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

    def load_frontmatter(self, path: str | Path) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class PendingInteractionResult:
    outcome: str
    response_text: str
    clear_pending_instructions: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(
    outcome: str,
    response_text: str,
    *,
    clear_pending_instructions: bool = False,
) -> PendingInteractionResult:
    return PendingInteractionResult(
        outcome=outcome,
        response_text=response_text,
        clear_pending_instructions=clear_pending_instructions,
    )


def _write_pending_with_status(
    runtime: PendingInteractionRuntime,
    root: str | Path,
    pending: PendingAction,
    status: str,
    *,
    derived: dict[str, Any] | None = None,
    now_iso: NowIsoFn | None = None,
) -> PendingAction:
    now_iso_fn = now_iso or _now_iso
    updated = PendingAction(
        schema_version=pending.schema_version,
        pending_action_id=pending.pending_action_id,
        raw_event_id=pending.raw_event_id,
        object_type=pending.object_type,
        status=status,
        created_at=pending.created_at,
        last_updated=now_iso_fn(),
        derived=derived or pending.derived,
        decision=pending.decision,
        decision_confidence=pending.decision_confidence,
        last_decision_id=pending.last_decision_id,
    )
    runtime.write_pending_action(updated, root)
    return updated


def force_create_derived(derived: dict[str, Any]) -> dict[str, Any]:
    routing = DecisionRouting(
        action="create",
        confidence=0.0,
        decision_ops=[],
        top_score=0.0,
        second_score=None,
        margin=None,
    )
    return apply_decision_to_derived(derived, routing)


def _load_active_pending_action(
    runtime: PendingInteractionRuntime,
    *,
    pending_root: str | Path,
    pending_id: str,
) -> tuple[PendingAction | None, PendingInteractionResult | None]:
    with telemetry.start_span("pending.load"):
        pending = runtime.load_pending_action(pending_root, pending_id)
    if pending is None:
        return None, _result("pending_missing", "That pending action no longer exists.")
    if pending.status != "pending":
        return None, _result("pending_not_active", f"This pending action is already {pending.status}.")
    return pending, None


def _rewrite_capture_target(
    *,
    derived: dict[str, Any],
    selected_target_id: str | None,
    default_target_id: str | None,
) -> dict[str, Any]:
    ops = derived.get("proposed_operations") or []
    if not (
        selected_target_id
        and default_target_id
        and selected_target_id != default_target_id
        and isinstance(ops, list)
        and len(ops) == 1
        and isinstance(ops[0], dict)
    ):
        return derived
    updated_op = dict(ops[0])
    updated_op["target_id"] = selected_target_id
    updated = dict(derived)
    updated["proposed_operations"] = [updated_op]
    return updated


def _first_written_title(
    runtime: PendingInteractionRuntime,
    written_paths: list[Path],
) -> str | None:
    titles = titles_from_paths(
        written_paths,
        load_frontmatter_fn=runtime.load_frontmatter,
    )
    if not titles:
        return None
    return titles[0]


async def confirm_capture_pending_update(
    *,
    runtime: PendingInteractionRuntime,
    pending_id: str,
    pending_root: str | Path,
    objects_root: str | Path,
    index_db: str | Path,
    derived_schema_path: Path,
    selected_target_id: str | None,
    default_target_id: str | None,
    fallback_title: str | None,
    matching: MatchingConfig | None,
    affinity_key: tuple[int, int],
    now_iso: NowIsoFn | None = None,
) -> PendingInteractionResult:
    pending, invalid_result = _load_active_pending_action(
        runtime,
        pending_root=pending_root,
        pending_id=pending_id,
    )
    if invalid_result is not None:
        return invalid_result
    assert pending is not None

    derived = _rewrite_capture_target(
        derived=pending.derived,
        selected_target_id=selected_target_id,
        default_target_id=default_target_id,
    )
    try:
        with telemetry.start_span("canonical.apply"):
            result = runtime.apply_operations(
                derived,
                objects_root=objects_root,
                canonical_schema_path=_CANONICAL_SCHEMA_PATH,
                derived_schema_path=derived_schema_path,
                last_decision_id=pending.last_decision_id,
            )
    except Exception as exc:
        telemetry.record_exception(exc)
        logging.exception("pending_apply_failed id=%s", pending_id)
        _write_pending_with_status(
            runtime,
            pending_root,
            pending,
            "failed",
            derived=derived,
            now_iso=now_iso,
        )
        return _result("apply_failed", "Failed to apply pending action. Check logs for details.")

    with telemetry.start_span("index.refresh"):
        await runtime.refresh_index_async(objects_root, index_db, matching=matching)
    runtime.notify_due_time_reminder_schedule_changed()
    if matching is not None:
        touched_ids = runtime.extract_target_ids_from_derived(derived)
        runtime.record_affinity_touches(affinity_key, touched_ids, matching=matching)
    _write_pending_with_status(
        runtime,
        pending_root,
        pending,
        "confirmed",
        derived=derived,
        now_iso=now_iso,
    )
    return _result(
        "confirmed",
        format_apply_success_message(
            written_paths=result.written_paths,
            fallback_title=fallback_title,
            load_frontmatter_fn=runtime.load_frontmatter,
        ),
        clear_pending_instructions=True,
    )


async def confirm_capture_pending_create_new(
    *,
    runtime: PendingInteractionRuntime,
    pending_id: str,
    pending_root: str | Path,
    objects_root: str | Path,
    index_db: str | Path,
    derived_schema_path: Path,
    matching: MatchingConfig | None,
    affinity_key: tuple[int, int],
    now_iso: NowIsoFn | None = None,
) -> PendingInteractionResult:
    pending, invalid_result = _load_active_pending_action(
        runtime,
        pending_root=pending_root,
        pending_id=pending_id,
    )
    if invalid_result is not None:
        return invalid_result
    assert pending is not None

    derived = force_create_derived(pending.derived)
    try:
        with telemetry.start_span("canonical.apply"):
            result = runtime.apply_operations(
                derived,
                objects_root=objects_root,
                canonical_schema_path=_CANONICAL_SCHEMA_PATH,
                derived_schema_path=derived_schema_path,
                last_decision_id=pending.last_decision_id,
            )
    except Exception as exc:
        telemetry.record_exception(exc)
        logging.exception("pending_create_new_failed id=%s", pending_id)
        _write_pending_with_status(
            runtime,
            pending_root,
            pending,
            "failed",
            derived=derived,
            now_iso=now_iso,
        )
        return _result("apply_failed", "Failed to create a new item. Check logs for details.")

    with telemetry.start_span("index.refresh"):
        await runtime.refresh_index_async(objects_root, index_db, matching=matching)
    runtime.notify_due_time_reminder_schedule_changed()
    if matching is not None:
        touched_ids = runtime.extract_ids_from_written_paths(result.written_paths)
        runtime.record_affinity_touches(affinity_key, touched_ids, matching=matching)
    _write_pending_with_status(
        runtime,
        pending_root,
        pending,
        "confirmed",
        derived=derived,
        now_iso=now_iso,
    )
    title = _first_written_title(runtime, result.written_paths)
    if title:
        response_text = f'Created a new note "{title}".'
    else:
        response_text = f"Created a new note. ({len(result.written_paths)} item(s) updated.)"
    return _result("created_new", response_text, clear_pending_instructions=True)


async def confirm_nl_pending(
    *,
    runtime: PendingInteractionRuntime,
    pending_id: str,
    pending_root: str | Path,
    objects_root: str | Path,
    index_db: str | Path,
    matching: MatchingConfig | None,
    affinity_key: tuple[int, int],
    now_iso: NowIsoFn | None = None,
    log_confirm_applied: LogConfirmAppliedFn | None = None,
) -> PendingInteractionResult:
    pending, invalid_result = _load_active_pending_action(
        runtime,
        pending_root=pending_root,
        pending_id=pending_id,
    )
    if invalid_result is not None:
        return invalid_result
    assert pending is not None

    try:
        with telemetry.start_span("canonical.apply"):
            result = runtime.apply_operations(
                pending.derived,
                objects_root=objects_root,
                canonical_schema_path=_CANONICAL_SCHEMA_PATH,
                derived_schema_path=None,
                last_decision_id=pending.last_decision_id,
            )
    except Exception as exc:
        telemetry.record_exception(exc)
        logging.exception("nl_mutation_pending_apply_failed id=%s", pending_id)
        _write_pending_with_status(runtime, pending_root, pending, "failed", now_iso=now_iso)
        return _result("apply_failed", "Failed to apply pending action. Check logs for details.")

    with telemetry.start_span("index.refresh"):
        await runtime.refresh_index_async(objects_root, index_db, matching=matching)
    runtime.notify_due_time_reminder_schedule_changed()
    if matching is not None:
        touched_ids = runtime.extract_target_ids_from_derived(pending.derived)
        touched_ids.extend(runtime.extract_ids_from_written_paths(result.written_paths))
        runtime.record_affinity_touches(affinity_key, touched_ids, matching=matching)
    _write_pending_with_status(runtime, pending_root, pending, "confirmed", now_iso=now_iso)
    if log_confirm_applied is not None:
        log_confirm_applied(pending_id)
    return _result(
        "confirmed",
        format_apply_success_message(
            written_paths=result.written_paths,
            load_frontmatter_fn=runtime.load_frontmatter,
        ),
    )


async def cancel_pending_action(
    *,
    runtime: PendingInteractionRuntime,
    pending_id: str,
    pending_root: str | Path,
    clear_pending_instructions: bool = False,
    now_iso: NowIsoFn | None = None,
) -> PendingInteractionResult:
    pending, invalid_result = _load_active_pending_action(
        runtime,
        pending_root=pending_root,
        pending_id=pending_id,
    )
    if invalid_result is not None:
        return invalid_result
    assert pending is not None

    _write_pending_with_status(runtime, pending_root, pending, "cancelled", now_iso=now_iso)
    return _result(
        "cancelled",
        "Cancelled. No changes made.",
        clear_pending_instructions=clear_pending_instructions,
    )
