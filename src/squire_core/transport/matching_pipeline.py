"""Shared matching retrieval and candidate-aware capture helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from squire_core.config_utils import DecisionConfig, MatchingConfig
from squire_core.llm.registry import get_async_embedding_provider
from squire_core.matching import build_matching_candidates_async


@dataclass(frozen=True)
class MatchingContext:
    candidates: list[Any]
    matching_trace: dict[str, Any]


def _candidate_payloads(candidates: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": candidate.object_id,
            "title": candidate.title,
            "snippet": candidate.snippet,
            "score": candidate.score,
        }
        for candidate in candidates
    ]


def build_capture_input(
    *,
    raw_event_id: str,
    object_type: str,
    message: str,
    candidates: list[Any],
) -> str:
    payload = {
        "raw_event_id": raw_event_id,
        "object_type": object_type,
        "message": message,
        "candidates": _candidate_payloads(candidates),
    }
    return json.dumps(payload, ensure_ascii=True)


def build_capture_prompt(
    *,
    extract_prompt: str,
    decision_prompt: str,
) -> str:
    prompt = extract_prompt.strip()
    return (
        f"{prompt}\n\n"
        "You will receive a JSON object with raw_event_id, object_type, message, and candidates.\n"
        "Extract fields from the message only.\n"
        "Use candidates only to choose proposed_operations and target_id values.\n"
        "If no candidate clearly matches, use a create operation.\n"
        "If a candidate clearly matches and fields should change, use update.\n"
        "If a candidate clearly matches and the message mainly adds notes or follow-up detail, use append.\n"
        "Set decision_confidence to a number between 0 and 1 for the routing choice.\n"
        "Keep confidence as the extraction confidence for the note content.\n"
        "Return only JSON that matches the provided schema.\n\n"
        f"Candidate-aware routing rules:\n{decision_prompt.strip()}"
    )


def build_decision_payload_from_capture(
    *,
    raw_event_id: str,
    object_type: str,
    derived: dict[str, Any],
    candidates: list[Any],
) -> dict[str, Any]:
    proposed_operations = derived.get("proposed_operations") or []
    payload_operations: list[dict[str, Any]] = []
    if isinstance(proposed_operations, list):
        for op in proposed_operations:
            if not isinstance(op, dict):
                continue
            payload_operations.append(
                {
                    "op": op.get("op"),
                    "target_id": op.get("target_id"),
                }
            )
    return {
        "schema_version": 1,
        "raw_event_id": raw_event_id,
        "object_type": object_type,
        "confidence": float(derived.get("decision_confidence", derived.get("confidence", 0))),
        "candidates": _candidate_payloads(candidates),
        "proposed_operations": payload_operations,
        "model": derived.get("model"),
        "prompt_version": derived.get("prompt_version"),
        "timestamp": derived.get("timestamp"),
    }


async def build_matching_context(
    *,
    embedding_provider: Any,
    raw_event_id: str,
    object_type: str,
    message: str,
    config: dict[str, Any],
    decision_config: DecisionConfig,
    matching_config: MatchingConfig,
    affinity_scores: dict[str, float],
    now_iso: str,
) -> MatchingContext:
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    queries = [message]
    semantic_provider = (
        get_async_embedding_provider(embedding_provider) if matching_config.semantic_weight > 0 else None
    )
    retrieval = await build_matching_candidates_async(
        db_path=index_db,
        queries=queries,
        object_type=object_type,
        matching_config=matching_config,
        score_threshold=decision_config.candidate_score_threshold,
        affinity_scores=affinity_scores,
        embedding_provider=semantic_provider,
    )
    candidates = retrieval.candidates[: decision_config.candidate_limit]
    ranking_rows = [
        {"id": candidate.object_id, "score": candidate.score}
        for candidate in candidates
    ]
    matching_trace: dict[str, Any] = {
        "schema_version": 1,
        "raw_event_id": raw_event_id,
        "object_type": object_type,
        "timestamp": now_iso,
        "queries": queries,
        "retrieval_mode": retrieval.retrieval_mode,
        "fallback_reason": retrieval.fallback_reason,
        "candidate_pool": {
            "before_dedupe": retrieval.candidate_pool_before_dedupe,
            "after_dedupe": retrieval.candidate_pool_after_dedupe,
            "returned_k": len(candidates),
        },
        "weights": retrieval.weights,
        "candidates": retrieval.trace_candidates[: decision_config.candidate_limit],
        "ranking": {
            "ordered": ranking_rows,
            "top_score": retrieval.top_score,
            "second_score": retrieval.second_score,
            "margin": retrieval.margin,
        },
        "gate": {
            "decision_confidence": 0.0,
            "auto_min_score": decision_config.auto_min_score,
            "auto_min_margin": decision_config.auto_min_margin,
            "outcome": "create",
        },
    }
    logging.info(
        "matching_retrieval_ok id=%s mode=%s fallback=%s queries=%s before=%s after=%s returned=%s top=%.3f margin=%s",
        raw_event_id,
        retrieval.retrieval_mode,
        retrieval.fallback_reason or "",
        len(queries),
        retrieval.candidate_pool_before_dedupe,
        retrieval.candidate_pool_after_dedupe,
        len(candidates),
        retrieval.top_score,
        f"{retrieval.margin:.3f}" if retrieval.margin is not None else "none",
    )
    return MatchingContext(
        candidates=candidates,
        matching_trace=matching_trace,
    )
