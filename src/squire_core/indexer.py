from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from squire_core.decision_models import DecisionCandidate


@dataclass(frozen=True)
class LexicalCandidate:
    object_id: str
    title: str
    snippet: str
    score: float
    updated_at: str | None
    status: str | None


@dataclass(frozen=True)
class IndexRebuildStats:
    indexed_count: int
    skipped_count: int


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        raise ValueError("Missing frontmatter")

    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Invalid frontmatter format")

    frontmatter_raw = parts[1]
    body = parts[2].lstrip("\n")
    frontmatter = yaml.safe_load(frontmatter_raw) or {}
    return frontmatter, body


def _iter_canonical_files(objects_root: str | Path) -> list[Path]:
    root = Path(objects_root)
    if not root.exists():
        return []
    return [path for path in root.rglob("*.md") if path.is_file()]


def _build_index_row(frontmatter: dict[str, Any], body: str) -> tuple[str, str, str, str | None, str, int, str, str]:
    object_id = frontmatter.get("id")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError("Missing required string field: id")

    object_type = frontmatter.get("type")
    if not isinstance(object_type, str) or not object_type.strip():
        raise ValueError("Missing required string field: type")

    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Missing required string field: title")

    updated_at = frontmatter.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at.strip():
        raise ValueError("Missing required string field: updated_at")

    status = frontmatter.get("status")
    if status is not None and not isinstance(status, str):
        status = str(status)

    tags_value = frontmatter.get("tags", [])
    if tags_value is None:
        tags: list[str] = []
    elif isinstance(tags_value, list):
        tags = [str(item) for item in tags_value]
    else:
        raise ValueError("Field tags must be a list when present")

    return (
        object_id.strip(),
        object_type.strip(),
        title.strip(),
        status,
        updated_at.strip(),
        1 if frontmatter.get("archived") else 0,
        ",".join(tags),
        body,
    )


def rebuild_index(objects_root: str | Path, db_path: str | Path) -> IndexRebuildStats:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS objects")
        cursor.execute("DROP TABLE IF EXISTS objects_fts")

        cursor.execute(
            """
            CREATE TABLE objects (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT,
                updated_at TEXT NOT NULL,
                archived INTEGER NOT NULL,
                tags TEXT,
                body TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE VIRTUAL TABLE objects_fts USING fts5(
                id,
                title,
                body,
                content='objects',
                content_rowid='rowid'
            )
            """
        )

        indexed_count = 0
        skipped_count = 0
        for path in _iter_canonical_files(objects_root):
            try:
                content = path.read_text(encoding="utf-8")
                frontmatter, body = _parse_frontmatter(content)
                row = _build_index_row(frontmatter, body)
                cursor.execute(
                    """
                    INSERT INTO objects (id, type, title, status, updated_at, archived, tags, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                indexed_count += 1
            except Exception as exc:
                skipped_count += 1
                logging.warning("index_rebuild_skipped_file path=%s error=%s", path, exc)
                continue

        cursor.execute(
            """
            INSERT INTO objects_fts (rowid, id, title, body)
            SELECT rowid, id, title, body FROM objects
            """
        )

        conn.commit()
        if skipped_count:
            logging.warning(
                "index_rebuild_completed_with_skips path=%s indexed=%s skipped=%s",
                db_path,
                indexed_count,
                skipped_count,
            )
        return IndexRebuildStats(indexed_count=indexed_count, skipped_count=skipped_count)
    finally:
        conn.close()


_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _normalize_query(query: str) -> str:
    tokens = [token.lower() for token in _WORD_RE.findall(query)]
    tokens = [token for token in tokens if len(token) > 2]
    if not tokens:
        return ""
    unique_tokens: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique_tokens.append(token)
    return " OR ".join(unique_tokens[:12])


def _fallback_snippet(snippet: str | None, body: str | None, title: str) -> str:
    if snippet:
        trimmed = snippet.strip()
        if trimmed:
            return trimmed
    text = (body or "").strip()
    if not text:
        return title
    if len(text) <= 120:
        return text
    return f"{text[:117].rstrip()}..."


def find_candidates(
    db_path: str | Path,
    query: str,
    *,
    object_type: str | None = None,
    limit: int = 3,
    score_threshold: float = 0.2,
) -> list[DecisionCandidate]:
    lexical = search_lexical_candidates(
        db_path,
        query,
        object_type=object_type,
        limit=limit,
        score_threshold=score_threshold,
    )
    return [
        DecisionCandidate(
            object_id=item.object_id,
            title=item.title,
            snippet=item.snippet,
            score=item.score,
        )
        for item in lexical
    ]


def search_lexical_candidates(
    db_path: str | Path,
    query: str,
    *,
    object_type: str | None = None,
    limit: int = 3,
    score_threshold: float = 0.2,
    pool_limit: int | None = None,
) -> list[LexicalCandidate]:
    normalized = _normalize_query(query)
    if not normalized or limit <= 0:
        return []

    fetch_limit = max(limit * 5, limit)
    if isinstance(pool_limit, int) and pool_limit > 0:
        fetch_limit = max(pool_limit, limit)
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        params: list[Any] = [normalized]
        type_filter = ""
        if object_type:
            type_filter = " AND objects.type = ?"
            params.append(object_type)
        params.append(fetch_limit)
        rows = cursor.execute(
            f"""
            SELECT
                objects.id,
                objects.title,
                objects.body,
                objects.updated_at,
                objects.status,
                bm25(objects_fts) AS rank,
                snippet(objects_fts, 2, '', '', '...', 12) AS snippet
            FROM objects_fts
            JOIN objects ON objects_fts.rowid = objects.rowid
            WHERE objects_fts MATCH ?
              AND objects.archived = 0
              {type_filter}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    candidates: list[LexicalCandidate] = []
    for object_id, title, body, updated_at, status, rank, snippet in rows:
        if object_id is None or title is None:
            continue
        rank_value = float(rank) if rank is not None else 0.0
        if rank_value < 0:
            rank_value = 0.0
        score = 1.0 / (1.0 + rank_value)
        if score < score_threshold:
            continue
        candidates.append(
            LexicalCandidate(
                object_id=str(object_id),
                title=str(title),
                snippet=_fallback_snippet(snippet, body, str(title)),
                score=score,
                updated_at=str(updated_at) if isinstance(updated_at, str) else None,
                status=str(status) if isinstance(status, str) else None,
            )
        )
        if len(candidates) >= limit:
            break

    return candidates
