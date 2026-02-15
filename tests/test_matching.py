from __future__ import annotations

from pathlib import Path

from squire_core.config_utils import load_matching_config
from squire_core.indexer import rebuild_index
from squire_core.matching import build_matching_candidates, sync_semantic_index


class _FakeEmbeddingProvider:
    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            if any(token in lowered for token in ("dentist", "tooth", "dental")):
                vectors.append([1.0, 0.0])
            elif "tax" in lowered:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors


def _write_object(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sync_semantic_index_is_incremental_and_removes_archived(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    db_path = tmp_path / "index.sqlite"
    provider = _FakeEmbeddingProvider()
    matching = load_matching_config(
        {
            "matching": {
                "semantic_weight": 0.5,
                "semantic_text_schema_version": 1,
            }
        }
    )
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Call dentist
status: open
updated_at: "2026-02-14T00:00:00Z"
archived: false
---
Call dentist tomorrow.
""",
    )
    rebuild_index(objects_root, db_path)

    first = sync_semantic_index(
        objects_root=objects_root,
        db_path=db_path,
        matching_config=matching,
        embedding_provider=provider,
    )
    assert first.indexed_count == 1
    assert first.removed_count == 0

    second = sync_semantic_index(
        objects_root=objects_root,
        db_path=db_path,
        matching_config=matching,
        embedding_provider=provider,
    )
    assert second.indexed_count == 0
    assert second.unchanged_count == 1

    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Call dentist
status: open
updated_at: "2026-02-15T00:00:00Z"
archived: true
---
Call dentist tomorrow.
""",
    )
    rebuild_index(objects_root, db_path)
    third = sync_semantic_index(
        objects_root=objects_root,
        db_path=db_path,
        matching_config=matching,
        embedding_provider=provider,
    )
    assert third.removed_count == 1


def test_build_matching_candidates_hybrid_mode_uses_semantic_signal(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    db_path = tmp_path / "index.sqlite"
    provider = _FakeEmbeddingProvider()
    matching = load_matching_config(
        {
            "matching": {
                "semantic_weight": 0.6,
                "lexical_weight": 0.4,
                "candidate_limit": 5,
            }
        }
    )
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Call dentist
status: open
updated_at: "2026-02-14T00:00:00Z"
archived: false
---
Need to schedule a dentist appointment.
""",
    )
    _write_object(
        objects_root / "admin" / "A_2.md",
        """---
id: A_2
type: admin
title: File taxes
status: open
updated_at: "2026-02-14T00:00:00Z"
archived: false
---
Complete tax filing checklist.
""",
    )
    rebuild_index(objects_root, db_path)
    sync_semantic_index(
        objects_root=objects_root,
        db_path=db_path,
        matching_config=matching,
        embedding_provider=provider,
    )

    result = build_matching_candidates(
        db_path=db_path,
        queries=["tooth doctor follow up"],
        object_type="admin",
        matching_config=matching,
        score_threshold=0.0,
        affinity_scores={},
        embedding_provider=provider,
    )

    assert result.retrieval_mode == "hybrid"
    assert result.candidates
    assert result.candidates[0].object_id == "A_1"


def test_build_matching_candidates_returns_none_when_retrieval_unavailable(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.sqlite"
    matching = load_matching_config({"matching": {"semantic_weight": 0.4}})
    result = build_matching_candidates(
        db_path=db_path,
        queries=["pay rent"],
        object_type="admin",
        matching_config=matching,
        score_threshold=0.0,
        affinity_scores={},
        embedding_provider=None,
    )
    assert result.retrieval_mode == "none"
    assert result.candidates == []
