from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml


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
