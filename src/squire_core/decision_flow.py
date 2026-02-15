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
    top_score: float
    second_score: float | None
    margin: float | None


def evaluate_decision(
    decision: dict[str, Any],
    config: DecisionConfig,
    *,
    top_score: float | None = None,
    second_score: float | None = None,
) -> DecisionRouting:
    confidence = float(decision.get("confidence", 0))
    if top_score is None:
        extracted_top, extracted_second = _extract_top_scores(decision)
        top_score = extracted_top
        second_score = extracted_second
    if second_score is not None and second_score > top_score:
        second_score = top_score
    margin = (top_score - second_score) if second_score is not None else None
    ops = decision.get("proposed_operations") or []
    decision_ops = [
        op
        for op in ops
        if op.get("op") in _DECISION_TARGET_OPS and isinstance(op.get("target_id"), str)
    ]
    if not decision_ops:
        return DecisionRouting(
            action="create",
            confidence=confidence,
            decision_ops=[],
            top_score=top_score,
            second_score=second_score,
            margin=margin,
        )
    auto_gate_passed = (
        confidence >= config.auto_apply_threshold
        and len(decision_ops) == 1
        and top_score >= config.auto_min_score
        and (margin is None or margin >= config.auto_min_margin)
    )
    if auto_gate_passed:
        return DecisionRouting(
            action="auto_apply",
            confidence=confidence,
            decision_ops=decision_ops,
            top_score=top_score,
            second_score=second_score,
            margin=margin,
        )
    if confidence >= config.confirm_threshold:
        return DecisionRouting(
            action="needs_confirmation",
            confidence=confidence,
            decision_ops=decision_ops,
            top_score=top_score,
            second_score=second_score,
            margin=margin,
        )
    return DecisionRouting(
        action="create",
        confidence=confidence,
        decision_ops=decision_ops,
        top_score=top_score,
        second_score=second_score,
        margin=margin,
    )


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


def _extract_top_scores(decision: dict[str, Any]) -> tuple[float, float | None]:
    candidates = decision.get("candidates") or []
    if not isinstance(candidates, list):
        return 0.0, None
    scores: list[float] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("score")
        if not isinstance(value, (int, float)):
            continue
        score = float(value)
        if score < 0:
            score = 0.0
        if score > 1:
            score = 1.0
        scores.append(score)
    if not scores:
        return 0.0, None
    scores.sort(reverse=True)
    if len(scores) == 1:
        return scores[0], None
    return scores[0], scores[1]
