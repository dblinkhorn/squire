from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from squire_core.config_utils import MatchingConfig
from squire_core.decision_models import DecisionCandidate
from squire_core.indexer import LexicalCandidate, search_lexical_candidates

_SEMANTIC_SCHEMA_VERSION = 1
_SEMANTIC_METADATA_KEYS = {
    "embedding_provider",
    "embedding_model",
    "chunk_size",
    "chunk_overlap",
    "embedding_text_schema_version",
    "index_schema_version",
}


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        ...


class AsyncEmbeddingProvider(Protocol):
    async def embed_async(self, texts: list[str], model: str) -> list[list[float]]:
        ...


@dataclass(frozen=True)
class SemanticSyncStats:
    indexed_count: int
    unchanged_count: int
    removed_count: int
    metadata_reset: bool
    duration_ms: int


@dataclass(frozen=True)
class MatchingRetrievalResult:
    candidates: list[DecisionCandidate]
    retrieval_mode: str
    fallback_reason: str | None
    candidate_pool_before_dedupe: int
    candidate_pool_after_dedupe: int
    weights: dict[str, float]
    trace_candidates: list[dict[str, Any]]
    top_score: float
    second_score: float | None
    margin: float | None


@dataclass
class _FusionCandidate:
    object_id: str
    title: str
    snippet: str
    updated_at: str | None
    status: str | None
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    recency_score: float = 0.0
    affinity_score: float = 0.0
    final_score: float = 0.0


def sync_semantic_index(
    *,
    objects_root: str | Path,
    db_path: str | Path,
    matching_config: MatchingConfig,
    embedding_provider: EmbeddingProvider,
) -> SemanticSyncStats:
    started = datetime.now(timezone.utc)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_semantic_schema(conn)
        metadata_reset = _ensure_semantic_metadata(conn, matching_config)
        existing = {
            str(row["object_id"]): str(row["content_hash"])
            for row in conn.execute("SELECT object_id, content_hash FROM semantic_objects").fetchall()
        }
        active_records = _collect_active_object_records(objects_root, matching_config.semantic_text_schema_version)
        indexed_count = 0
        unchanged_count = 0
        to_embed: list[tuple[str, dict[str, Any]]] = []
        for object_id, record in active_records.items():
            content_hash = str(record["content_hash"])
            if existing.get(object_id) == content_hash:
                unchanged_count += 1
                continue
            to_embed.append((object_id, record))
        removed_ids = sorted(set(existing) - set(active_records))
        if removed_ids:
            conn.executemany(
                "DELETE FROM semantic_objects WHERE object_id = ?",
                [(object_id,) for object_id in removed_ids],
            )
        if to_embed:
            vectors = embedding_provider.embed(
                [str(record["embedding_text"]) for _, record in to_embed],
                matching_config.semantic_model,
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            rows = []
            for (object_id, record), vector in zip(to_embed, vectors, strict=True):
                rows.append(
                    (
                        object_id,
                        record["object_type"],
                        record["title"],
                        record["snippet"],
                        record["status"],
                        record["updated_at"],
                        record["content_hash"],
                        json.dumps(vector),
                        len(vector),
                        now_iso,
                    )
                )
            conn.executemany(
                """
                INSERT INTO semantic_objects (
                    object_id, object_type, title, snippet, status, updated_at,
                    content_hash, embedding, embedding_dim, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_id) DO UPDATE SET
                    object_type = excluded.object_type,
                    title = excluded.title,
                    snippet = excluded.snippet,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    content_hash = excluded.content_hash,
                    embedding = excluded.embedding,
                    embedding_dim = excluded.embedding_dim,
                    indexed_at = excluded.indexed_at
                """,
                rows,
            )
            indexed_count = len(rows)
        conn.commit()
    finally:
        conn.close()
    elapsed = datetime.now(timezone.utc) - started
    return SemanticSyncStats(
        indexed_count=indexed_count,
        unchanged_count=unchanged_count,
        removed_count=len(removed_ids),
        metadata_reset=metadata_reset,
        duration_ms=max(0, int(elapsed.total_seconds() * 1000)),
    )


async def build_matching_candidates_async(
    *,
    db_path: str | Path,
    queries: list[str],
    object_type: str,
    matching_config: MatchingConfig,
    score_threshold: float,
    affinity_scores: dict[str, float] | None,
    embedding_provider: AsyncEmbeddingProvider | None,
) -> MatchingRetrievalResult:
    cleaned_queries = [value.strip() for value in queries if isinstance(value, str) and value.strip()]
    if not cleaned_queries:
        return MatchingRetrievalResult(
            candidates=[],
            retrieval_mode="none",
            fallback_reason="no_queries",
            candidate_pool_before_dedupe=0,
            candidate_pool_after_dedupe=0,
            weights={"lexical": 0.0, "recency": 0.0, "affinity": 0.0, "semantic": 0.0},
            trace_candidates=[],
            top_score=0.0,
            second_score=None,
            margin=None,
        )

    candidate_pool = min(
        matching_config.max_candidate_pool,
        matching_config.candidate_limit * matching_config.candidate_multiplier,
    )
    lexical_rows: list[LexicalCandidate] = []
    db_exists = Path(db_path).exists()
    lexical_available = db_exists
    if lexical_available:
        try:
            for query in cleaned_queries:
                lexical_rows.extend(
                    search_lexical_candidates(
                        db_path,
                        query,
                        object_type=object_type,
                        limit=candidate_pool,
                        score_threshold=0.0,
                        pool_limit=candidate_pool,
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive logging path
            lexical_available = False
            logging.warning("matching_lexical_unavailable error=%s", exc)
            lexical_rows = []

    semantic_rows: list[dict[str, Any]] = []
    semantic_available = False
    fallback_reason: str | None = None
    if matching_config.semantic_weight > 0 and embedding_provider is not None:
        if not db_exists:
            fallback_reason = "semantic_index_missing"
        else:
            try:
                semantic_rows = await _search_semantic_candidates_async(
                    db_path=db_path,
                    queries=cleaned_queries,
                    object_type=object_type,
                    candidate_pool=candidate_pool,
                    matching_config=matching_config,
                    embedding_provider=embedding_provider,
                )
                semantic_available = True
            except Exception as exc:  # pragma: no cover - defensive logging path
                fallback_reason = f"semantic_unavailable:{exc.__class__.__name__}"
                logging.warning("matching_semantic_unavailable error=%s", exc)
                semantic_rows = []
    elif matching_config.semantic_weight > 0:
        fallback_reason = "semantic_provider_unavailable"

    if not lexical_available and not semantic_available:
        return MatchingRetrievalResult(
            candidates=[],
            retrieval_mode="none",
            fallback_reason=fallback_reason or "retrieval_unavailable",
            candidate_pool_before_dedupe=0,
            candidate_pool_after_dedupe=0,
            weights={"lexical": 0.0, "recency": 0.0, "affinity": 0.0, "semantic": 0.0},
            trace_candidates=[],
            top_score=0.0,
            second_score=None,
            margin=None,
        )

    retrieval_mode = _resolve_retrieval_mode(lexical_available, semantic_available, matching_config.semantic_weight)
    weights = _normalized_weights(
        matching_config=matching_config,
        lexical_available=lexical_available,
        semantic_available=semantic_available,
    )
    merged: dict[str, _FusionCandidate] = {}
    before_dedupe = 0
    for row in lexical_rows:
        before_dedupe += 1
        entry = merged.get(row.object_id)
        if entry is None:
            entry = _FusionCandidate(
                object_id=row.object_id,
                title=row.title,
                snippet=row.snippet,
                updated_at=row.updated_at,
                status=row.status,
            )
            merged[row.object_id] = entry
        if row.score > entry.lexical_score:
            entry.lexical_score = row.score
            entry.title = row.title
            entry.snippet = row.snippet
            entry.updated_at = row.updated_at or entry.updated_at
            entry.status = row.status or entry.status

    for row in semantic_rows:
        object_id = str(row["object_id"])
        before_dedupe += 1
        entry = merged.get(object_id)
        if entry is None:
            entry = _FusionCandidate(
                object_id=object_id,
                title=str(row["title"]),
                snippet=str(row["snippet"]),
                updated_at=row.get("updated_at"),
                status=row.get("status"),
            )
            merged[object_id] = entry
        semantic_score = float(row.get("semantic_score", 0.0))
        if semantic_score > entry.semantic_score:
            entry.semantic_score = semantic_score
            entry.title = str(row["title"])
            entry.snippet = str(row["snippet"])
            entry.updated_at = row.get("updated_at") or entry.updated_at
            entry.status = row.get("status") or entry.status

    affinity = affinity_scores or {}
    now = datetime.now(timezone.utc)
    for entry in merged.values():
        entry.recency_score = _recency_score(entry.updated_at, now)
        entry.affinity_score = max(0.0, min(1.0, float(affinity.get(entry.object_id, 0.0))))
        lexical_contrib = weights["lexical"] * entry.lexical_score
        recency_contrib = weights["recency"] * entry.recency_score
        semantic_contrib = weights["semantic"] * entry.semantic_score
        affinity_contrib = min(
            weights["affinity"] * entry.affinity_score,
            matching_config.affinity_max_boost,
        )
        entry.final_score = min(1.0, lexical_contrib + recency_contrib + semantic_contrib + affinity_contrib)

    ranked = sorted(merged.values(), key=lambda item: item.final_score, reverse=True)
    after_dedupe = len(ranked)
    filtered = [item for item in ranked if item.final_score >= score_threshold]
    top_ranked = filtered[: matching_config.candidate_limit]
    decision_candidates = [
        DecisionCandidate(
            object_id=item.object_id,
            title=item.title,
            snippet=item.snippet,
            score=item.final_score,
        )
        for item in top_ranked
    ]
    top_score = top_ranked[0].final_score if top_ranked else 0.0
    second_score = top_ranked[1].final_score if len(top_ranked) > 1 else None
    margin = (top_score - second_score) if second_score is not None else None
    trace_candidates = [
        {
            "id": item.object_id,
            "title": item.title,
            "component_scores": {
                "lexical": item.lexical_score,
                "recency": item.recency_score,
                "affinity": item.affinity_score,
                "semantic": item.semantic_score,
            },
            "final_score": item.final_score,
        }
        for item in top_ranked
    ]
    return MatchingRetrievalResult(
        candidates=decision_candidates,
        retrieval_mode=retrieval_mode,
        fallback_reason=fallback_reason,
        candidate_pool_before_dedupe=before_dedupe,
        candidate_pool_after_dedupe=after_dedupe,
        weights=weights,
        trace_candidates=trace_candidates,
        top_score=top_score,
        second_score=second_score,
        margin=margin,
    )


def _ensure_semantic_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_objects (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            title TEXT NOT NULL,
            snippet TEXT NOT NULL,
            status TEXT,
            updated_at TEXT,
            content_hash TEXT NOT NULL,
            embedding TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_semantic_objects_type
        ON semantic_objects (object_type)
        """
    )


def _ensure_semantic_metadata(conn: sqlite3.Connection, matching_config: MatchingConfig) -> bool:
    current = {
        str(row["key"]): str(row["value"])
        for row in conn.execute("SELECT key, value FROM semantic_meta").fetchall()
    }
    expected = {
        "embedding_provider": matching_config.semantic_provider,
        "embedding_model": matching_config.semantic_model,
        "chunk_size": "0",
        "chunk_overlap": "0",
        "embedding_text_schema_version": str(matching_config.semantic_text_schema_version),
        "index_schema_version": str(_SEMANTIC_SCHEMA_VERSION),
    }
    metadata_reset = any(current.get(key) != value for key, value in expected.items())
    if metadata_reset:
        conn.execute("DELETE FROM semantic_objects")
        conn.execute("DELETE FROM semantic_meta")
        conn.executemany(
            "INSERT INTO semantic_meta (key, value) VALUES (?, ?)",
            [(key, value) for key, value in expected.items()],
        )
        conn.commit()
        logging.info(
            "semantic_metadata_reset provider=%s model=%s schema=%s text_schema=%s",
            expected["embedding_provider"],
            expected["embedding_model"],
            expected["index_schema_version"],
            expected["embedding_text_schema_version"],
        )
    else:
        missing = _SEMANTIC_METADATA_KEYS - set(current.keys())
        if missing:
            for key in sorted(missing):
                conn.execute("INSERT OR REPLACE INTO semantic_meta (key, value) VALUES (?, ?)", (key, expected[key]))
            conn.commit()
    return metadata_reset


def _collect_active_object_records(
    objects_root: str | Path,
    text_schema_version: int,
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    root = Path(objects_root)
    if not root.exists():
        return records
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            frontmatter, body = _parse_canonical_file(path)
        except Exception:
            continue
        object_id = frontmatter.get("id")
        object_type = frontmatter.get("type")
        title = frontmatter.get("title")
        if not isinstance(object_id, str) or not isinstance(object_type, str) or not isinstance(title, str):
            continue
        if bool(frontmatter.get("archived")):
            continue
        embedding_text = _build_embedding_text(frontmatter, body, text_schema_version)
        content_hash = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()
        records[object_id] = {
            "object_id": object_id,
            "object_type": object_type,
            "title": title,
            "snippet": _fallback_snippet(body, title),
            "status": _to_optional_str(frontmatter.get("status")),
            "updated_at": _to_optional_str(frontmatter.get("updated_at")),
            "embedding_text": embedding_text,
            "content_hash": content_hash,
        }
    return records


def _parse_canonical_file(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}, content.strip()
    parts = content.split("---", 2)
    if len(parts) != 3:
        return {}, content.strip()
    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n").strip()
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, body


def _build_embedding_text(frontmatter: dict[str, Any], body: str, text_schema_version: int) -> str:
    lines = [f"embedding_text_schema_version: {text_schema_version}"]
    for key in ("id", "type", "title"):
        value = frontmatter.get(key)
        if value is not None:
            lines.append(f"{key}: {_to_text(value)}")
    for key in ("status", "next_action", "one_liner", "goal", "next_step", "name", "context"):
        value = frontmatter.get(key)
        if value is not None and _to_text(value).strip():
            lines.append(f"{key}: {_to_text(value)}")
    tags = frontmatter.get("tags")
    if isinstance(tags, list) and tags:
        normalized_tags = [str(value).strip() for value in tags if str(value).strip()]
        if normalized_tags:
            lines.append(f"tags: {', '.join(normalized_tags)}")
    if body.strip():
        lines.append("body:")
        lines.append(body.strip())
    return "\n".join(lines)


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_to_text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _to_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fallback_snippet(body: str, title: str) -> str:
    if body:
        snippet = body.strip()
        if len(snippet) <= 120:
            return snippet
        return f"{snippet[:117].rstrip()}..."
    return title


async def _search_semantic_candidates_async(
    *,
    db_path: str | Path,
    queries: list[str],
    object_type: str,
    candidate_pool: int,
    matching_config: MatchingConfig,
    embedding_provider: AsyncEmbeddingProvider,
) -> list[dict[str, Any]]:
    vectors = await embedding_provider.embed_async(queries, matching_config.semantic_model)
    return _search_semantic_candidates_from_vectors(
        db_path=db_path,
        vectors=vectors,
        object_type=object_type,
        candidate_pool=candidate_pool,
    )


def _search_semantic_candidates_from_vectors(
    *,
    db_path: str | Path,
    vectors: list[list[float]],
    object_type: str,
    candidate_pool: int,
) -> list[dict[str, Any]]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_semantic_schema(conn)
        rows = conn.execute(
            """
            SELECT object_id, title, snippet, status, updated_at, embedding
            FROM semantic_objects
            WHERE object_type = ?
            """,
            (object_type,),
        ).fetchall()
    finally:
        conn.close()
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        object_id = str(row["object_id"])
        embedding = _decode_embedding(row["embedding"])
        if not embedding:
            continue
        for vector in vectors:
            cosine = _cosine_similarity(vector, embedding)
            semantic_score = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
            semantic_score *= _semantic_status_modifier(row["status"])
            if semantic_score <= 0:
                continue
            current = merged.get(object_id)
            if current is None or semantic_score > float(current["semantic_score"]):
                merged[object_id] = {
                    "object_id": object_id,
                    "title": str(row["title"]),
                    "snippet": str(row["snippet"]),
                    "status": _to_optional_str(row["status"]),
                    "updated_at": _to_optional_str(row["updated_at"]),
                    "semantic_score": semantic_score,
                }
    ranked = sorted(merged.values(), key=lambda item: float(item["semantic_score"]), reverse=True)
    return ranked[:candidate_pool]


def _decode_embedding(value: Any) -> list[float]:
    if not isinstance(value, str):
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    vector: list[float] = []
    for item in payload:
        if not isinstance(item, (int, float)):
            continue
        vector.append(float(item))
    return vector


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right, strict=True):
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def _semantic_status_modifier(status_value: Any) -> float:
    status = _to_optional_str(status_value)
    if not status:
        return 1.0
    lowered = status.lower()
    if lowered in {"done", "completed"}:
        return 0.9
    return 1.0


def _resolve_retrieval_mode(lexical_available: bool, semantic_available: bool, semantic_weight: float) -> str:
    if lexical_available and semantic_available and semantic_weight > 0:
        return "hybrid"
    if lexical_available:
        return "lexical_only"
    if semantic_available:
        return "semantic_only"
    return "none"


def _normalized_weights(
    *,
    matching_config: MatchingConfig,
    lexical_available: bool,
    semantic_available: bool,
) -> dict[str, float]:
    raw = {
        "lexical": matching_config.lexical_weight if lexical_available else 0.0,
        "recency": matching_config.recency_weight,
        "affinity": matching_config.affinity_weight,
        "semantic": matching_config.semantic_weight if semantic_available else 0.0,
    }
    total = sum(value for value in raw.values() if value > 0)
    if total <= 0:
        return {"lexical": 1.0, "recency": 0.0, "affinity": 0.0, "semantic": 0.0}
    return {key: (value / total if value > 0 else 0.0) for key, value in raw.items()}


def _recency_score(updated_at: str | None, now: datetime) -> float:
    if not updated_at:
        return 0.0
    parsed = _parse_datetime(updated_at)
    if parsed is None:
        return 0.0
    delta_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
    return 1.0 / (1.0 + (delta_days / 30.0))


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
