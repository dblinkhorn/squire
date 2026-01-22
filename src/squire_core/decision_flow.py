from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from squire_core.config_utils import DecisionConfig

_DECISION_TARGET_OPS = {"update", "append"}


@dataclass(frozen=True)
class DecisionRouting:
    action: str
    confidence: float
    decision_ops: list[dict[str, Any]]


def evaluate_decision(decision: dict[str, Any], config: DecisionConfig) -> DecisionRouting:
    confidence = float(decision.get("confidence", 0))
    ops = decision.get("proposed_operations") or []
    decision_ops = [
        op
        for op in ops
        if op.get("op") in _DECISION_TARGET_OPS and isinstance(op.get("target_id"), str)
    ]
    if not decision_ops:
        return DecisionRouting(action="create", confidence=confidence, decision_ops=[])
    if confidence >= config.auto_apply_threshold and len(decision_ops) == 1:
        return DecisionRouting(action="auto_apply", confidence=confidence, decision_ops=decision_ops)
    if confidence >= config.confirm_threshold:
        return DecisionRouting(action="needs_confirmation", confidence=confidence, decision_ops=decision_ops)
    return DecisionRouting(action="create", confidence=confidence, decision_ops=decision_ops)


def apply_decision_to_derived(derived: dict[str, Any], routing: DecisionRouting) -> dict[str, Any]:
    updated = deepcopy(derived)
    existing_ops = updated.get("proposed_operations") or []
    fields_template = _select_fields_template(updated, existing_ops)

    if routing.action == "create":
        updated_ops = []
        for op in existing_ops or [{}]:
            updated_ops.append(
                {
                    "op": "create",
                    "target_id": None,
                    "fields": op.get("fields", fields_template),
                }
            )
        updated["proposed_operations"] = updated_ops
        updated["intent"] = "create"
        return updated

    updated_ops: list[dict[str, Any]] = []
    for decision_op in routing.decision_ops:
        updated_ops.append(
            {
                "op": decision_op.get("op"),
                "target_id": decision_op.get("target_id"),
                "fields": fields_template,
            }
        )
    updated["proposed_operations"] = updated_ops
    if updated_ops:
        updated["intent"] = updated_ops[0].get("op")
    return updated


def _select_fields_template(updated: dict[str, Any], existing_ops: list[dict[str, Any]]) -> dict[str, Any]:
    if existing_ops:
        fields = existing_ops[0].get("fields")
        if isinstance(fields, dict):
            return dict(fields)
    extracted = updated.get("extracted_fields")
    if isinstance(extracted, dict):
        return dict(extracted)
    return {}
