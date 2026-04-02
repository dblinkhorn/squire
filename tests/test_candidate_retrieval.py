from __future__ import annotations

from pathlib import Path

from squire_core.indexer import find_candidates, rebuild_index


def _write_object(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_find_candidates_returns_matches(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    db_path = tmp_path / "index.sqlite"

    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Pay rent
status: open
updated_at: "2026-01-01T00:00:00Z"
tags:
  - home
---
Remember to pay rent on Friday.
""",
    )
    _write_object(
        objects_root / "projects" / "PR_1.md",
        """---
id: PR_1
type: projects
title: Renovate kitchen
status: open
updated_at: "2026-01-01T00:00:00Z"
tags: []
---
Collect contractor estimates.
""",
    )

    rebuild_index(objects_root, db_path)

    candidates = find_candidates(
        db_path,
        "rent",
        object_type="admin",
        limit=3,
        score_threshold=0.0,
    )

    assert len(candidates) == 1
    assert candidates[0].object_id == "A_1"
    assert "rent" in candidates[0].snippet.lower()


def test_find_candidates_filters_type(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    db_path = tmp_path / "index.sqlite"

    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Pay rent
status: open
updated_at: "2026-01-01T00:00:00Z"
tags: []
---
Remember to pay rent on Friday.
""",
    )

    rebuild_index(objects_root, db_path)

    candidates = find_candidates(
        db_path,
        "rent",
        object_type="projects",
        limit=3,
        score_threshold=0.0,
    )

    assert candidates == []
