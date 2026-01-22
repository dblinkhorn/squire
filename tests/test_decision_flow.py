from __future__ import annotations

from squire_core.config_utils import DecisionConfig
from squire_core.decision_flow import apply_decision_to_derived, evaluate_decision


def _base_derived() -> dict:
    return {
        "schema_version": 1,
        "raw_event_id": "R_1",
        "object_type": "admin",
        "intent": "create",
        "extracted_fields": {"title": "Pay rent", "status": "open", "next_action": "Pay rent"},
        "confidence": 0.9,
        "proposed_operations": [
            {
                "op": "create",
                "target_id": None,
                "fields": {"title": "Pay rent", "status": "open", "next_action": "Pay rent"},
            }
        ],
        "model": None,
        "prompt_version": None,
        "timestamp": None,
    }


def test_evaluate_decision_auto_apply_single_target() -> None:
    decision = {
        "confidence": 0.9,
        "proposed_operations": [{"op": "update", "target_id": "A_123"}],
    }
    config = DecisionConfig(
        auto_apply_threshold=0.85,
        confirm_threshold=0.65,
        candidate_limit=3,
        candidate_score_threshold=0.2,
    )
    routing = evaluate_decision(decision, config)
    assert routing.action == "auto_apply"
    assert routing.decision_ops[0]["target_id"] == "A_123"


def test_evaluate_decision_requires_confirmation_multiple_targets() -> None:
    decision = {
        "confidence": 0.9,
        "proposed_operations": [
            {"op": "update", "target_id": "A_1"},
            {"op": "append", "target_id": "A_2"},
        ],
    }
    config = DecisionConfig(
        auto_apply_threshold=0.85,
        confirm_threshold=0.65,
        candidate_limit=3,
        candidate_score_threshold=0.2,
    )
    routing = evaluate_decision(decision, config)
    assert routing.action == "needs_confirmation"
    assert len(routing.decision_ops) == 2


def test_evaluate_decision_low_confidence_forces_create() -> None:
    decision = {
        "confidence": 0.4,
        "proposed_operations": [{"op": "update", "target_id": "A_1"}],
    }
    config = DecisionConfig(
        auto_apply_threshold=0.85,
        confirm_threshold=0.65,
        candidate_limit=3,
        candidate_score_threshold=0.2,
    )
    routing = evaluate_decision(decision, config)
    assert routing.action == "create"


def test_apply_decision_forces_create_on_low_confidence() -> None:
    derived = _base_derived()
    decision = {
        "confidence": 0.4,
        "proposed_operations": [{"op": "update", "target_id": "A_1"}],
    }
    config = DecisionConfig(
        auto_apply_threshold=0.85,
        confirm_threshold=0.65,
        candidate_limit=3,
        candidate_score_threshold=0.2,
    )
    routing = evaluate_decision(decision, config)
    updated = apply_decision_to_derived(derived, routing)
    assert updated["proposed_operations"][0]["op"] == "create"
    assert updated["proposed_operations"][0]["target_id"] is None


def test_apply_decision_sets_target_for_update() -> None:
    derived = _base_derived()
    decision = {
        "confidence": 0.9,
        "proposed_operations": [{"op": "append", "target_id": "A_9"}],
    }
    config = DecisionConfig(
        auto_apply_threshold=0.85,
        confirm_threshold=0.65,
        candidate_limit=3,
        candidate_score_threshold=0.2,
    )
    routing = evaluate_decision(decision, config)
    updated = apply_decision_to_derived(derived, routing)
    assert updated["proposed_operations"][0]["op"] == "append"
    assert updated["proposed_operations"][0]["target_id"] == "A_9"
