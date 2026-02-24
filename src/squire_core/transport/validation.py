"""Shared validation helpers for mutation normalization and explicit fix commands."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


FIX_IMMUTABLE_FIELDS = {
    "id",
    "type",
    "created_at",
    "updated_at",
    "source_event_ids",
    "last_decision_id",
}

FIX_ALLOWED_FIELDS = {
    "admin": {
        "title",
        "status",
        "next_action",
        "due_date",
        "due_at",
        "priority",
        "blocked_reason",
        "completed_at",
        "gcal_event_id",
    },
    "projects": {
        "title",
        "status",
        "next_action",
        "goal",
        "due",
        "blocked_reason",
    },
    "people": {
        "title",
        "name",
        "context",
        "follow_ups",
        "last_contacted",
        "next_contact",
    },
    "ideas": {
        "title",
        "one_liner",
        "status",
        "next_step",
    },
}

FIX_ENUM_VALUES = {
    ("admin", "status"): {"open", "done", "blocked"},
    ("admin", "priority"): {"low", "normal", "high"},
    ("projects", "status"): {"planning", "in_progress", "blocked", "completed", "on_hold"},
    ("ideas", "status"): {"seed", "incubating", "active", "parked", "done"},
}

FIX_DATE_FIELDS = {
    ("admin", "due_date"),
    ("people", "last_contacted"),
    ("people", "next_contact"),
}

FIX_DATETIME_FIELDS = {
    ("admin", "due_at"),
    ("admin", "completed_at"),
}


def is_iso_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def is_iso_datetime(value: str, *, require_timezone: bool) -> bool:
    if "T" not in value:
        return False
    candidate = value
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    if require_timezone and parsed.tzinfo is None:
        return False
    return True


def validate_fix_updates(object_type: str, updates: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    allowed_fields = FIX_ALLOWED_FIELDS.get(object_type)
    if not allowed_fields:
        return None, f"Unsupported object type for !fix: {object_type}"

    validated: dict[str, Any] = {}
    for raw_key, raw_value in updates.items():
        key = str(raw_key).strip()
        if not key:
            return None, "Field name cannot be empty."
        if key in FIX_IMMUTABLE_FIELDS:
            return None, f"Field `{key}` is not editable."
        if key not in allowed_fields:
            allowed = ", ".join(sorted(allowed_fields))
            return None, f"Field `{key}` is not allowed for {object_type}. Allowed fields: {allowed}"
        if not isinstance(raw_value, str):
            return None, f"Field `{key}` must be provided as text."
        value = raw_value.strip()
        if not value:
            return None, f"Field `{key}` cannot be empty."

        enum_values = FIX_ENUM_VALUES.get((object_type, key))
        if enum_values and value not in enum_values:
            allowed = ", ".join(sorted(enum_values))
            return None, f"Invalid value for `{key}`. Allowed values: {allowed}"

        if (object_type, key) in FIX_DATE_FIELDS and not is_iso_date(value):
            return None, f"Invalid value for `{key}`. Use YYYY-MM-DD."
        if (object_type, key) in FIX_DATETIME_FIELDS and not is_iso_datetime(value, require_timezone=True):
            return None, f"Invalid value for `{key}`. Use ISO datetime with timezone offset."
        if (object_type, key) == ("projects", "due"):
            if not is_iso_date(value) and not is_iso_datetime(value, require_timezone=False):
                return None, "Invalid value for `due`. Use YYYY-MM-DD or ISO datetime."

        validated[key] = value

    if not validated:
        return None, "No valid fields provided."
    return validated, None
