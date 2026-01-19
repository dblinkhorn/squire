from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from squire_core.config_utils import load_config, normalize_archive_config


def _parse_query(query: str) -> tuple[str, str | None]:
    tag = None
    terms = []
    for token in query.split():
        if token.startswith("tag:"):
            tag = token.split(":", 1)[1]
        else:
            terms.append(token)
    return " ".join(terms), tag


def _search(db_path: str | Path, query: str, tag: str | None, limit: int) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        sql = (
            "SELECT o.id, o.type, o.title "
            "FROM objects_fts "
            "JOIN objects o ON o.id = objects_fts.id "
            "WHERE objects_fts MATCH ? "
        )
        params: list[Any] = [query]
        if tag:
            sql += "AND o.tags LIKE ? "
            params.append(f"%{tag}%")
        sql += "LIMIT ?"
        params.append(limit)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the SQLite index")
    parser.add_argument("query", help="Search query (supports tag:foo)")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    config = normalize_archive_config(config)
    db_path = config.get("paths", {}).get("index_db", "index/sb.sqlite")

    search_terms, tag = _parse_query(args.query)
    if not search_terms:
        print("No search terms provided.")
        return 1

    results = _search(db_path, search_terms, tag, args.limit)
    if not results:
        print("No matches.")
        return 0

    for item_id, item_type, title in results:
        print(f"{item_id} [{item_type}] {title}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
