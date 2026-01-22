from __future__ import annotations

from pathlib import Path

from squire_core.canonical_store import load_frontmatter
from squire_core.operation_apply import apply_operations


def test_apply_operations_sets_last_decision_id_and_source_event_ids(tmp_path) -> None:
    derived = {
        "object_type": "admin",
        "raw_event_id": "R_1",
        "extracted_fields": {},
        "proposed_operations": [
            {
                "op": "create",
                "target_id": None,
                "fields": {
                    "title": "Follow up",
                    "status": "open",
                    "next_action": "Email Chris",
                },
            }
        ],
    }
    result = apply_operations(
        derived,
        objects_root=tmp_path,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=None,
        last_decision_id="R_1/decision_v1_20260101T000000Z.json",
    )
    assert len(result.written_paths) == 1
    frontmatter = load_frontmatter(result.written_paths[0])
    assert frontmatter["last_decision_id"] == "R_1/decision_v1_20260101T000000Z.json"
    assert "R_1" in frontmatter["source_event_ids"]
