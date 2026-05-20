"""Local target grounding for natural-language mutation routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, utils

from squire_core.canonical_store import load_frontmatter
from squire_core.indexer import find_candidates

_SUMMARY_FIELDS = (
    "title",
    "next_action",
    "goal",
    "name",
    "one_liner",
    "next_step",
)
_INTENT_STATUSES = {
    "append": {"open"},
    "fix": {"open"},
    "done": {"open"},
    "reopen": {"done"},
}
_MIN_SCORE = 84.0
_MIN_MARGIN = 10.0
_STRONG_TEXT_SCORE = 90.0
_STRONG_TEXT_FUZZY_SCORE = 88.0
_STRONG_TEXT_FUZZY_MARGIN = 5.0
_FTS_BONUS = 3.0
_FTS_LIMIT = 25
_RECENT_LIMIT = 5
_RECENT_MAX_BOOST = 8.0
_TARGET_INFERENCE_STOPWORDS = {
    "actually",
    "appointment",
    "appointments",
    "appt",
    "appts",
    "change",
    "correct",
    "edit",
    "fix",
    "item",
    "items",
    "note",
    "notes",
    "schedule",
    "scheduled",
    "set",
    "task",
    "tasks",
    "time",
    "update",
}
_TARGET_INFERENCE_SYNONYMS = {
    "appt": "appointment",
    "appts": "appointments",
}


@dataclass(frozen=True)
class MutationTargetCandidate:
    object_id: str
    object_type: str
    title: str
    summary: str
    status: str
    fuzzy_score: float
    fts_score: float
    recent_score: float
    score: float


@dataclass(frozen=True)
class MutationTargetGrounding:
    target: MutationTargetCandidate | None
    pool_size: int
    best_score: float
    second_score: float | None
    margin: float | None
    outcome: str


def eligible_statuses_for_intent(intent: str) -> set[str]:
    return set(_INTENT_STATUSES.get(intent, {"open"}))


def ground_mutation_target(
    *,
    content: str,
    intent: str,
    objects_root: str | Path,
    index_db: str | Path | None = None,
    recent_ids: list[str] | None = None,
    object_type_hint: str | None = None,
    required_object_type: str | None = None,
    require_due_anchor: bool = False,
) -> MutationTargetGrounding:
    text = content.strip()
    if not text:
        return MutationTargetGrounding(
            target=None,
            pool_size=0,
            best_score=0.0,
            second_score=None,
            margin=None,
            outcome="no_query",
        )

    statuses = eligible_statuses_for_intent(intent)
    candidates = _load_eligible_candidates(
        objects_root=objects_root,
        statuses=statuses,
        object_type_hint=object_type_hint,
        required_object_type=required_object_type,
        require_due_anchor=require_due_anchor,
    )
    if not candidates:
        return MutationTargetGrounding(
            target=None,
            pool_size=0,
            best_score=0.0,
            second_score=None,
            margin=None,
            outcome="no_pool",
        )

    fts_scores = _load_fts_scores(
        content=text,
        index_db=index_db,
        object_type_hint=object_type_hint,
    )
    scored: list[MutationTargetCandidate] = []
    for base in candidates:
        fuzzy_score = _fuzzy_score(text, base.summary)
        fts_score = fts_scores.get(base.object_id, 0.0)
        recent_score = _recent_score(
            object_id=base.object_id,
            title=base.title,
            content=text,
            recent_ids=recent_ids or [],
        )
        score = min(100.0, fuzzy_score + recent_score + (_FTS_BONUS if fts_score > 0 else 0.0))
        scored.append(
            MutationTargetCandidate(
                object_id=base.object_id,
                object_type=base.object_type,
                title=base.title,
                summary=base.summary,
                status=base.status,
                fuzzy_score=fuzzy_score,
                fts_score=fts_score,
                recent_score=recent_score,
                score=score,
            )
        )

    ranked = sorted(scored, key=lambda item: item.score, reverse=True)
    best = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else None
    margin = (best.score - second_score) if second_score is not None else None
    if best.score < _MIN_SCORE:
        outcome = "score_below_threshold"
        target = None
    elif margin is not None and margin < _MIN_MARGIN and not _has_strong_text_match(best, ranked[1]):
        outcome = "margin_below_threshold"
        target = None
    else:
        outcome = "grounded"
        target = best

    return MutationTargetGrounding(
        target=target,
        pool_size=len(candidates),
        best_score=best.score,
        second_score=second_score,
        margin=margin,
        outcome=outcome,
    )


@dataclass(frozen=True)
class _BaseCandidate:
    object_id: str
    object_type: str
    title: str
    summary: str
    status: str


def _load_eligible_candidates(
    *,
    objects_root: str | Path,
    statuses: set[str],
    object_type_hint: str | None,
    required_object_type: str | None,
    require_due_anchor: bool,
) -> list[_BaseCandidate]:
    root = Path(objects_root)
    if not root.exists():
        return []
    rows: list[_BaseCandidate] = []
    for path in sorted(root.glob("*/*.md")):
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        object_id = _clean_str(frontmatter.get("id"))
        object_type = _clean_str(frontmatter.get("type"))
        if not object_id or not object_type:
            continue
        if object_type_hint and object_type != object_type_hint:
            continue
        if required_object_type and object_type != required_object_type:
            continue
        status = (_clean_str(frontmatter.get("status")) or "open").lower()
        if status not in statuses:
            continue
        if require_due_anchor and not (_clean_str(frontmatter.get("due_at")) or _clean_str(frontmatter.get("due_date"))):
            continue
        title = _clean_str(frontmatter.get("title")) or object_id
        summary = _summary_text(frontmatter)
        if not summary:
            summary = title
        rows.append(
            _BaseCandidate(
                object_id=object_id,
                object_type=object_type,
                title=title,
                summary=summary,
                status=status,
            )
        )
    return rows


def _load_fts_scores(
    *,
    content: str,
    index_db: str | Path | None,
    object_type_hint: str | None,
) -> dict[str, float]:
    if index_db is None:
        return {}
    try:
        rows = find_candidates(
            index_db,
            content,
            object_type=object_type_hint,
            limit=_FTS_LIMIT,
            score_threshold=0.0,
        )
    except Exception:
        return {}
    return {row.object_id: row.score for row in rows}


def _summary_text(frontmatter: dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for field in _SUMMARY_FIELDS:
        value = _clean_str(frontmatter.get(field))
        if not value:
            continue
        key = " ".join(value.lower().split())
        if key in seen:
            continue
        seen.add(key)
        parts.append(value)
    return " | ".join(parts)


def _fuzzy_score(query: str, candidate: str) -> float:
    scores = (
        fuzz.token_set_ratio(query, candidate, processor=utils.default_process),
        fuzz.WRatio(query, candidate, processor=utils.default_process),
    )
    return float(max(scores))


def _recent_score(*, object_id: str, title: str, content: str, recent_ids: list[str]) -> float:
    if object_id not in recent_ids[:_RECENT_LIMIT]:
        return 0.0
    content_tokens = _target_tokens(content)
    if not content_tokens:
        return 0.0
    overlap = content_tokens & _target_tokens(title)
    if not overlap:
        return 0.0
    recency_index = recent_ids.index(object_id)
    return max(0.0, min(_RECENT_MAX_BOOST, 4.0 + (len(overlap) * 2.0) - recency_index))


def _has_strong_text_match(best: MutationTargetCandidate, second: MutationTargetCandidate) -> bool:
    return (
        best.score >= _STRONG_TEXT_SCORE
        and best.fuzzy_score >= _STRONG_TEXT_FUZZY_SCORE
        and (best.fuzzy_score - second.fuzzy_score) >= _STRONG_TEXT_FUZZY_MARGIN
    )


def _target_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in re.findall(r"[A-Za-z0-9']+", value.lower()):
        token = raw_token.strip("'")
        if not token:
            continue
        token = _TARGET_INFERENCE_SYNONYMS.get(token, token)
        if len(token) < 4 or token in _TARGET_INFERENCE_STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
