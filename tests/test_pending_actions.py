from __future__ import annotations

from squire_core.pending_actions import (
    PendingAction,
    load_pending_action,
    update_pending_action_status,
    write_pending_action,
)


def test_pending_action_round_trip(tmp_path) -> None:
    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_1",
        raw_event_id="R_1",
        object_type="admin",
        status="pending",
        created_at="2026-01-01T00:00:00Z",
        last_updated="2026-01-01T00:00:00Z",
        derived={"object_type": "admin", "proposed_operations": []},
        decision={"confidence": 0.7},
        decision_confidence=0.7,
        last_decision_id="R_1/decision_v1_20260101T000000Z.json",
    )
    write_pending_action(pending, tmp_path)
    loaded = load_pending_action(tmp_path, "PA_1")
    assert loaded is not None
    assert loaded.pending_action_id == "PA_1"
    assert loaded.status == "pending"
    assert loaded.decision_confidence == 0.7
    assert loaded.last_decision_id == "R_1/decision_v1_20260101T000000Z.json"


def test_pending_action_status_update(tmp_path) -> None:
    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_2",
        raw_event_id="R_2",
        object_type="projects",
        status="pending",
        created_at="2026-01-01T00:00:00Z",
        last_updated="2026-01-01T00:00:00Z",
        derived={"object_type": "projects", "proposed_operations": []},
    )
    write_pending_action(pending, tmp_path)
    updated = update_pending_action_status(tmp_path, "PA_2", "confirmed")
    assert updated is not None
    assert updated.status == "confirmed"
    assert updated.last_updated != "2026-01-01T00:00:00Z"
