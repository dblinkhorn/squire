from __future__ import annotations

from pathlib import Path

from squire_core.canonical_store import load_frontmatter
from squire_core.operation_apply import apply_operations


def _create_admin_with_due(tmp_path, *, object_id: str, due_field: str, due_value: str) -> None:
    fields = {
        "id": object_id,
        "title": "Call dentist",
        "status": "open",
        "next_action": "Call dentist",
    }
    fields[due_field] = due_value
    apply_operations(
        {
            "object_type": "admin",
            "raw_event_id": "R_CREATE",
            "extracted_fields": {},
            "proposed_operations": [{"op": "create", "target_id": None, "fields": fields}],
        },
        objects_root=tmp_path,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=None,
    )


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


def test_apply_operations_update_due_at_clears_existing_due_date(tmp_path) -> None:
    _create_admin_with_due(
        tmp_path,
        object_id="A_SWITCH_1",
        due_field="due_date",
        due_value="2026-02-08",
    )

    result = apply_operations(
        {
            "object_type": "admin",
            "raw_event_id": "R_UPDATE_1",
            "extracted_fields": {},
            "proposed_operations": [
                {
                    "op": "update",
                    "target_id": "A_SWITCH_1",
                    "fields": {"due_at": "2026-02-08T16:00:00-08:00"},
                }
            ],
        },
        objects_root=tmp_path,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=None,
    )

    frontmatter = load_frontmatter(result.written_paths[0])
    assert frontmatter["due_at"] == "2026-02-08T16:00:00-08:00"
    assert "due_date" not in frontmatter


def test_apply_operations_update_due_date_clears_existing_due_at(tmp_path) -> None:
    _create_admin_with_due(
        tmp_path,
        object_id="A_SWITCH_2",
        due_field="due_at",
        due_value="2026-02-08T16:00:00-08:00",
    )

    result = apply_operations(
        {
            "object_type": "admin",
            "raw_event_id": "R_UPDATE_2",
            "extracted_fields": {},
            "proposed_operations": [
                {
                    "op": "update",
                    "target_id": "A_SWITCH_2",
                    "fields": {"due_date": "2026-02-09"},
                }
            ],
        },
        objects_root=tmp_path,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=None,
    )

    frontmatter = load_frontmatter(result.written_paths[0])
    assert frontmatter["due_date"] == "2026-02-09"
    assert "due_at" not in frontmatter


def test_apply_operations_project_create_falls_back_next_action_to_title(tmp_path) -> None:
    result = apply_operations(
        {
            "object_type": "projects",
            "raw_event_id": "R_PROJECT_1",
            "extracted_fields": {},
            "proposed_operations": [
                {
                    "op": "create",
                    "target_id": None,
                    "fields": {
                        "title": "Replace moldy baseboard in bathrooms",
                        "status": "open",
                    },
                }
            ],
        },
        objects_root=tmp_path,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=None,
    )

    frontmatter = load_frontmatter(result.written_paths[0])
    assert frontmatter["title"] == "Replace moldy baseboard in bathrooms"
    assert frontmatter["status"] == "open"
    assert frontmatter["next_action"] == "Replace moldy baseboard in bathrooms"


def test_apply_operations_project_create_defaults_status_to_open(tmp_path) -> None:
    result = apply_operations(
        {
            "object_type": "projects",
            "raw_event_id": "R_PROJECT_2",
            "extracted_fields": {},
            "proposed_operations": [
                {
                    "op": "create",
                    "target_id": None,
                    "fields": {
                        "title": "Repaint house",
                        "next_action": "Repaint house",
                    },
                }
            ],
        },
        objects_root=tmp_path,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=None,
    )

    frontmatter = load_frontmatter(result.written_paths[0])
    assert frontmatter["title"] == "Repaint house"
    assert frontmatter["status"] == "open"
    assert frontmatter["next_action"] == "Repaint house"
