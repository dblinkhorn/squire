from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from squire_core.indexer import rebuild_index


def _write_object(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rebuild_index_skips_bad_files_and_logs_warning(tmp_path: Path, caplog) -> None:
    objects_root = tmp_path / "objects"
    db_path = tmp_path / "index.sqlite"

    _write_object(
        objects_root / "admin" / "A_GOOD.md",
        """---
id: A_GOOD
type: admin
title: Call dentist
status: open
updated_at: "2026-03-18T00:00:00Z"
tags: []
---
Call dentist tomorrow.
""",
    )
    _write_object(
        objects_root / "admin" / "A_BAD.md",
        """---
id: A_BAD
type: admin
title: Investigate bug: broken yaml
status: open
updated_at: "2026-03-18T00:00:00Z"
---
This note has malformed frontmatter.
""",
    )
    _write_object(
        objects_root / "admin" / "A_MISSING_TITLE.md",
        """---
id: A_MISSING_TITLE
type: admin
status: open
updated_at: "2026-03-18T00:00:00Z"
tags: []
---
This note is parseable YAML but missing a required field.
""",
    )
    _write_object(
        objects_root / "admin" / "A_DUPLICATE.md",
        """---
id: A_GOOD
type: admin
title: Duplicate id
status: open
updated_at: "2026-03-18T01:00:00Z"
tags: []
---
This note conflicts on primary key.
""",
    )

    with caplog.at_level(logging.WARNING):
        stats = rebuild_index(objects_root, db_path)

    assert stats.indexed_count == 1
    assert stats.skipped_count == 3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id, title FROM objects ORDER BY id").fetchall()
    finally:
        conn.close()

    assert rows == [("A_GOOD", "Duplicate id")]
    assert "index_rebuild_skipped_file" in caplog.text
    assert "A_BAD.md" in caplog.text
    assert "A_MISSING_TITLE.md" in caplog.text
    assert "A_GOOD.md" in caplog.text
    assert "UNIQUE constraint failed: objects.id" in caplog.text
    assert "Missing required string field: title" in caplog.text
    assert "index_rebuild_completed_with_skips" in caplog.text
