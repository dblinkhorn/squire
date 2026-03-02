"""Shared matching and decision helper orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from squire_core.config_utils import DecisionConfig, MatchingConfig
from squire_core.derived_event_store import write_derived_event
from squire_core.interpreter import InterpretationValidationError, interpret_text_async
from squire_core.llm.provider import AsyncLLMProvider, LLMProvider
from squire_core.llm.registry import get_async_embedding_provider
from squire_core.matching import build_matching_candidates_async


@dataclass(frozen=True)
class MatchingDecisionResult:
    decision_payload: dict[str, Any] | None
    decision_artifact_id: str | None
    matching_trace: dict[str, Any] | None


def build_decision_input(
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
        "candidates": [
            {
                "id": candidate.object_id,
                "title": candidate.title,
                "snippet": candidate.snippet,
                "score": candidate.score,
            }
            for candidate in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=True)


async def candidate_queries_from_llm(
    *,
    provider: LLMProvider | AsyncLLMProvider,
    model: str,
    prompt: str,
    message: str,
) -> list[str]:
    schema_path = Path("config/schemas/candidate_query_v1.json")
    try:
        result = await interpret_text_async(
            provider=provider,
            text=message,
            model=model,
            system_prompt=prompt,
            schema_path=schema_path,
        )
    except Exception as exc:
        logging.warning("candidate_query_failed error=%s", exc)
        return []
    payload = result.derived if isinstance(result.derived, dict) else {}
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return []
    cleaned = []
    for query in queries:
        if not isinstance(query, str):
            continue
        value = query.strip()
        if value:
            cleaned.append(value)
    return cleaned


async def run_matching_decision(
    *,
    provider: LLMProvider | AsyncLLMProvider,
    embedding_provider: LLMProvider | AsyncLLMProvider | None,
    model: str,
    raw_event_id: str,
    object_type: str,
    message: str,
    config: dict[str, Any],
    derived_root: str | Path,
    decision_prompt: str,
    decision_config: DecisionConfig,
    matching_config: MatchingConfig,
    affinity_scores: dict[str, float],
    now_iso: str,
    candidate_query_prompt: str | None = None,
) -> MatchingDecisionResult:
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    queries = [message]
    if candidate_query_prompt:
        llm_queries = await candidate_queries_from_llm(
            provider=provider,
            model=model,
            prompt=candidate_query_prompt,
            message=message,
        )
        if llm_queries:
            queries = llm_queries
    active_embedding_provider = embedding_provider or provider
    semantic_provider = (
        get_async_embedding_provider(active_embedding_provider) if matching_config.semantic_weight > 0 else None
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

    if retrieval.retrieval_mode == "none":
        logging.warning("matching_decision_skipped id=%s reason=retrieval_unavailable", raw_event_id)
        return MatchingDecisionResult(
            decision_payload=None,
            decision_artifact_id=None,
            matching_trace=matching_trace,
        )

    decision_input = build_decision_input(
        raw_event_id=raw_event_id,
        object_type=object_type,
        message=message,
        candidates=candidates,
    )
    decision_schema = Path("config/schemas/decision_v1.json")
    decision_payload: dict[str, Any] | None = None
    decision_artifact_id: str | None = None
    try:
        decision = await interpret_text_async(
            provider=provider,
            text=decision_input,
            model=model,
            system_prompt=decision_prompt,
            schema_path=decision_schema,
        )
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="decision_invalid",
            error=exc,
        )
        logging.warning("decision_invalid id=%s error=%s", raw_event_id, exc)
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="decision_invalid",
            error=exc,
        )
        logging.exception("decision_failed id=%s", raw_event_id)
    else:
        decision_result = write_derived_event(
            derived=decision.derived,
            raw_text=decision.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_event_id,
            label="decision",
        )
        decision_payload = decision.derived
        if decision_result.derived_path:
            try:
                decision_artifact_id = str(decision_result.derived_path.relative_to(Path(derived_root)))
            except ValueError:
                decision_artifact_id = str(decision_result.derived_path)
        logging.info(
            "decision_ok id=%s object_type=%s confidence=%.2f candidates=%s",
            raw_event_id,
            object_type,
            decision.derived.get("confidence", 0),
            len(candidates),
        )

    return MatchingDecisionResult(
        decision_payload=decision_payload,
        decision_artifact_id=decision_artifact_id,
        matching_trace=matching_trace,
    )
