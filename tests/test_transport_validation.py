from __future__ import annotations

from squire_core.transport import validation


def test_validate_fix_updates_accepts_valid_admin_fields() -> None:
    validated, error = validation.validate_fix_updates(
        "admin",
        {
            "status": "done",
            "due_at": "2026-02-23T18:30:00+00:00",
        },
    )

    assert error is None
    assert validated == {
        "status": "done",
        "due_at": "2026-02-23T18:30:00+00:00",
    }


def test_validate_fix_updates_rejects_invalid_enum() -> None:
    validated, error = validation.validate_fix_updates(
        "admin",
        {
            "priority": "urgent",
        },
    )

    assert validated is None
    assert error == "Invalid value for `priority`. Allowed values: high, low, normal"
