from __future__ import annotations

from squire_core.transport import tracing


def test_build_unresolved_scope_groups_by_operation_id() -> None:
    scope = tracing.build_unresolved_scope_from_entries(
        [
            {
                "operation_id": "op_1",
                "action_type": "set_fields",
                "target_token": "2",
                "op_status": "unresolved",
                "reason_code": "field_unknown",
            },
            {
                "operation_id": "op_1",
                "action_type": "set_fields",
                "target_token": "A_7",
                "op_status": "unresolved",
                "reason_code": "field_unknown",
            },
            {
                "operation_id": "op_2",
                "action_type": "append_body",
                "target_token": "3",
                "op_status": "resolved",
            },
        ]
    )

    assert scope == {
        "op_1": {
            "action_type": "set_fields",
            "target_tokens": ["2", "A_7"],
            "reason_code": "field_unknown",
        }
    }


def test_summarize_unresolved_scope_is_stable() -> None:
    summary = tracing.summarize_unresolved_scope(
        {
            "op_9": {
                "reason_code": "target_missing",
            }
        }
    )

    assert summary == "Unresolved operations: op_9 (target_missing)"
