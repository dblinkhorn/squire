from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from squire_core.decision_models import DecisionCandidate


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


def rebuild_index(objects_root: str | Path, db_path: str | Path) -> None:
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

        for path in _iter_canonical_files(objects_root):
            content = path.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(content)
            cursor.execute(
                """
                INSERT INTO objects (id, type, title, status, updated_at, archived, tags, body)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frontmatter.get("id"),
                    frontmatter.get("type"),
                    frontmatter.get("title"),
                    frontmatter.get("status"),
                    frontmatter.get("updated_at"),
                    1 if frontmatter.get("archived") else 0,
                    ",".join(frontmatter.get("tags", [])),
                    body,
                ),
            )

        cursor.execute(
            """
            INSERT INTO objects_fts (rowid, id, title, body)
            SELECT rowid, id, title, body FROM objects
            """
        )

        conn.commit()
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
    normalized = _normalize_query(query)
    if not normalized or limit <= 0:
        return []

    fetch_limit = max(limit * 5, limit)
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

    candidates: list[DecisionCandidate] = []
    for object_id, title, body, rank, snippet in rows:
        if object_id is None or title is None:
            continue
        rank_value = float(rank) if rank is not None else 0.0
        if rank_value < 0:
            rank_value = 0.0
        score = 1.0 / (1.0 + rank_value)
        if score < score_threshold:
            continue
        candidates.append(
            DecisionCandidate(
                object_id=str(object_id),
                title=str(title),
                snippet=_fallback_snippet(snippet, body, str(title)),
                score=score,
            )
        )
        if len(candidates) >= limit:
            break

    return candidates
