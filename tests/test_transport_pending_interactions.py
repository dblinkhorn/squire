from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from squire_core.config_utils import load_matching_config
from squire_core.pending_actions import PendingAction, load_pending_action, write_pending_action
from squire_core.transport.pending_interactions import (
    cancel_pending_action,
    confirm_capture_pending_create_new,
    confirm_capture_pending_update,
    confirm_nl_pending,
)


def _matching_config():
    return load_matching_config(
        {
            "llm": {"provider": "openai", "model": "gpt-5-mini"},
            "matching": {"semantic_weight": 0},
        }
    )


class _Runtime:
    def __init__(self) -> None:
        self.applied: list[dict[str, object]] = []
        self.refreshed: list[tuple[str | Path, str | Path, object]] = []
        self.notified: list[bool] = []
        self.affinity_touches: list[tuple[tuple[int, int], list[str], object]] = []
        self.frontmatter_by_path: dict[Path, dict[str, object]] = {}
        self.apply_result = SimpleNamespace(written_paths=[])

    def load_pending_action(self, root: str | Path, pending_id: str) -> PendingAction | None:
        return load_pending_action(root, pending_id)

    def write_pending_action(self, pending: PendingAction, root: str | Path) -> Path:
        return write_pending_action(pending, root)

    def apply_operations(
        self,
        derived: dict[str, object],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ):
        self.applied.append(
            {
                "derived": derived,
                "objects_root": objects_root,
                "canonical_schema_path": canonical_schema_path,
                "derived_schema_path": derived_schema_path,
                "last_decision_id": last_decision_id,
            }
        )
        return self.apply_result

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching=None,
    ) -> None:
        self.refreshed.append((objects_root, index_db, matching))

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        self.notified.append(clear_state)

    def extract_target_ids_from_derived(self, derived: dict[str, object]) -> list[str]:
        ops = derived.get("proposed_operations") or []
        if not isinstance(ops, list):
            return []
        result: list[str] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            target_id = op.get("target_id")
            if isinstance(target_id, str):
                result.append(target_id)
        return result

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        result: list[str] = []
        for path in paths:
            frontmatter = self.load_frontmatter(path)
            object_id = frontmatter.get("id")
            if isinstance(object_id, str):
                result.append(object_id)
        return result

    def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching) -> None:
        self.affinity_touches.append((key, list(object_ids), matching))

    def load_frontmatter(self, path: str | Path) -> dict[str, object]:
        return self.frontmatter_by_path.get(Path(path), {})


def test_confirm_capture_pending_update_retargets_and_formats_response(tmp_path: Path) -> None:
    runtime = _Runtime()
    pending_root = tmp_path / "pending"
    objects_root = tmp_path / "objects"
    index_db = tmp_path / "index.sqlite"
    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_1",
        raw_event_id="R_1",
        object_type="admin",
        status="pending",
        created_at="2026-03-20T00:00:00+00:00",
        last_updated="2026-03-20T00:00:00+00:00",
        derived={
            "object_type": "admin",
            "proposed_operations": [{"op": "update", "target_id": "A_1", "fields": {"status": "done"}}],
        },
        last_decision_id="R_1/decision.json",
    )
    write_pending_action(pending, pending_root)
    runtime.apply_result = SimpleNamespace(written_paths=[objects_root / "admin" / "A_2.md"])

    result = asyncio.run(
        confirm_capture_pending_update(
            runtime=runtime,
            pending_id="PA_1",
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=index_db,
            derived_schema_path=Path("config/schemas/derived_event_admin_v1.json"),
            selected_target_id="A_2",
            default_target_id="A_1",
            fallback_title="Call dentist",
            matching=_matching_config(),
            affinity_key=(1, 2),
        )
    )

    stored = load_pending_action(pending_root, "PA_1")
    assert stored is not None
    assert stored.status == "confirmed"
    assert stored.derived["proposed_operations"][0]["target_id"] == "A_2"
    assert runtime.applied[0]["derived"]["proposed_operations"][0]["target_id"] == "A_2"
    assert runtime.refreshed == [(objects_root, index_db, _matching_config())]
    assert runtime.notified == [False]
    assert runtime.affinity_touches[0][0] == (1, 2)
    assert runtime.affinity_touches[0][1] == ["A_2"]
    assert result.outcome == "confirmed"
    assert result.clear_pending_instructions is True
    assert result.response_text == '✅ Applied update to 1 note:\n- "Call dentist"'


def test_confirm_capture_pending_create_new_forces_create_and_reports_title(tmp_path: Path) -> None:
    runtime = _Runtime()
    pending_root = tmp_path / "pending"
    objects_root = tmp_path / "objects"
    index_db = tmp_path / "index.sqlite"
    written_path = objects_root / "admin" / "A_9.md"
    runtime.frontmatter_by_path[written_path] = {"id": "A_9", "title": "Book annual physical"}
    runtime.apply_result = SimpleNamespace(written_paths=[written_path])

    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_CREATE",
        raw_event_id="R_2",
        object_type="admin",
        status="pending",
        created_at="2026-03-20T00:00:00+00:00",
        last_updated="2026-03-20T00:00:00+00:00",
        derived={
            "object_type": "admin",
            "proposed_operations": [{"op": "update", "target_id": "A_1", "fields": {"title": "Book annual physical"}}],
        },
    )
    write_pending_action(pending, pending_root)

    result = asyncio.run(
        confirm_capture_pending_create_new(
            runtime=runtime,
            pending_id="PA_CREATE",
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=index_db,
            derived_schema_path=Path("config/schemas/derived_event_admin_v1.json"),
            matching=_matching_config(),
            affinity_key=(1, 2),
        )
    )

    stored = load_pending_action(pending_root, "PA_CREATE")
    assert stored is not None
    assert stored.status == "confirmed"
    assert stored.derived["proposed_operations"][0]["op"] == "create"
    assert stored.derived["proposed_operations"][0]["target_id"] is None
    assert stored.derived["intent"] == "create"
    assert runtime.affinity_touches[0][1] == ["A_9"]
    assert result.outcome == "created_new"
    assert result.clear_pending_instructions is True
    assert result.response_text == 'Created a new note "Book annual physical".'


def test_confirm_nl_pending_records_affinity_and_logs_confirmation(tmp_path: Path) -> None:
    runtime = _Runtime()
    pending_root = tmp_path / "pending"
    objects_root = tmp_path / "objects"
    index_db = tmp_path / "index.sqlite"
    written_path = objects_root / "admin" / "A_1.md"
    runtime.frontmatter_by_path[written_path] = {"id": "A_1", "title": "Call internet provider"}
    runtime.apply_result = SimpleNamespace(written_paths=[written_path])
    logged: list[str] = []

    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_NL",
        raw_event_id="R_3",
        object_type="admin",
        status="pending",
        created_at="2026-03-20T00:00:00+00:00",
        last_updated="2026-03-20T00:00:00+00:00",
        derived={
            "object_type": "admin",
            "proposed_operations": [{"op": "update", "target_id": "A_1", "fields": {"status": "done"}}],
        },
    )
    write_pending_action(pending, pending_root)

    result = asyncio.run(
        confirm_nl_pending(
            runtime=runtime,
            pending_id="PA_NL",
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=index_db,
            matching=_matching_config(),
            affinity_key=(4, 5),
            log_confirm_applied=logged.append,
        )
    )

    stored = load_pending_action(pending_root, "PA_NL")
    assert stored is not None
    assert stored.status == "confirmed"
    assert runtime.notified == [False]
    assert runtime.affinity_touches[0][0] == (4, 5)
    assert runtime.affinity_touches[0][1] == ["A_1", "A_1"]
    assert logged == ["PA_NL"]
    assert result.outcome == "confirmed"
    assert result.clear_pending_instructions is False
    assert result.response_text == '✅ Applied update to 1 note:\n- "Call internet provider"'


def test_cancel_pending_action_marks_pending_cancelled(tmp_path: Path) -> None:
    runtime = _Runtime()
    pending_root = tmp_path / "pending"
    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_CANCEL",
        raw_event_id="R_4",
        object_type="admin",
        status="pending",
        created_at="2026-03-20T00:00:00+00:00",
        last_updated="2026-03-20T00:00:00+00:00",
        derived={"object_type": "admin", "proposed_operations": []},
    )
    write_pending_action(pending, pending_root)

    result = asyncio.run(
        cancel_pending_action(
            runtime=runtime,
            pending_id="PA_CANCEL",
            pending_root=pending_root,
            clear_pending_instructions=True,
        )
    )

    stored = load_pending_action(pending_root, "PA_CANCEL")
    assert stored is not None
    assert stored.status == "cancelled"
    assert result.outcome == "cancelled"
    assert result.clear_pending_instructions is True
    assert result.response_text == "Cancelled. No changes made."
