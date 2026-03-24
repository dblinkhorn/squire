from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from squire_core.config_utils import NLCommandRoutingConfig
from squire_core.transport import routing as transport_routing


def _routing_config() -> NLCommandRoutingConfig:
    return NLCommandRoutingConfig(
        enabled=True,
        clarify_on_ambiguous=True,
        allow_nl_mutations=True,
        plan_trace_enabled=True,
        read_auto_min_confidence=0.85,
        mutation_confirm_min_confidence=0.75,
        max_recent_limit=25,
    )


def test_normalize_set_fields_requires_canonical_field_candidates() -> None:
    fields, reason, notes = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "feb 18",
                "source_phrase": "date",
                "field_candidates": {
                    "primary": {"field_id": "due_date", "confidence": 0.8},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=timezone.utc,
    )

    assert reason is None
    assert fields == {"due_date": "2026-02-18"}
    assert notes == []


def test_normalize_set_fields_prefers_due_at_when_time_hint_present() -> None:
    fields, reason, _ = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "feb 18 at 3pm",
                "source_phrase": "date",
                "field_candidates": {
                    "primary": {"field_id": "due_date", "confidence": 0.7},
                    "alternates": [{"field_id": "due_at", "confidence": 0.6}],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=timezone.utc,
    )

    assert reason is None
    assert fields is not None
    assert fields["due_at"].startswith("2026-02-18T15:00:00")


def test_normalize_set_fields_allows_time_only_due_at_with_existing_due_at_anchor() -> None:
    la_tz = ZoneInfo("America/Los_Angeles")
    fields, reason, notes = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "4:20pm",
                "source_phrase": "due time",
                "field_candidates": {
                    "primary": {"field_id": "due_at", "confidence": 0.95},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=la_tz,
        existing_frontmatter={"due_at": "2026-03-22T21:20:01+00:00"},
    )

    assert reason is None
    assert fields == {"due_at": "2026-03-22T16:20:00-07:00"}
    assert notes == []


def test_normalize_set_fields_allows_time_only_due_at_with_existing_due_date_anchor() -> None:
    la_tz = ZoneInfo("America/Los_Angeles")
    fields, reason, notes = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "4:20pm",
                "source_phrase": "due time",
                "field_candidates": {
                    "primary": {"field_id": "due_at", "confidence": 0.95},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=la_tz,
        existing_frontmatter={"due_date": "2026-02-18"},
    )

    assert reason is None
    assert fields == {"due_at": "2026-02-18T16:20:00-08:00"}
    assert notes == []


def test_normalize_set_fields_rejects_time_only_due_at_without_anchor() -> None:
    fields, reason, notes = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "4:00pm",
                "source_phrase": "due time",
                "field_candidates": {
                    "primary": {"field_id": "due_at", "confidence": 0.95},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=timezone.utc,
    )

    assert fields is None
    assert reason == "value_parse_failed"
    assert notes == []


def test_normalize_set_fields_infers_meridiem_from_existing_due_at_anchor() -> None:
    la_tz = ZoneInfo("America/Los_Angeles")
    fields, reason, notes = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "1",
                "source_phrase": "due time",
                "field_candidates": {
                    "primary": {"field_id": "due_at", "confidence": 0.95},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 3, 23, 21, 0, tzinfo=timezone.utc),
        tz=la_tz,
        existing_frontmatter={"due_at": "2026-03-24T14:00:00-07:00"},
    )

    assert reason is None
    assert fields == {"due_at": "2026-03-24T13:00:00-07:00"}
    assert notes == []


def test_normalize_set_fields_rejects_ambiguous_time_only_due_at_with_existing_due_date_anchor() -> None:
    la_tz = ZoneInfo("America/Los_Angeles")
    fields, reason, notes = transport_routing.normalize_set_fields(
        object_type="admin",
        field_updates=[
            {
                "value_text": "1",
                "source_phrase": "due time",
                "field_candidates": {
                    "primary": {"field_id": "due_at", "confidence": 0.95},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 3, 23, 21, 0, tzinfo=timezone.utc),
        tz=la_tz,
        existing_frontmatter={"due_date": "2026-03-24"},
    )

    assert fields is None
    assert reason == "time_of_day_ambiguous"
    assert notes == []


def test_normalize_nl_mutation_plan_input_accepts_object_id_target() -> None:
    plan, error = transport_routing.normalize_nl_mutation_plan_input(
        {
            "schema_version": 1,
            "operations": [
                {
                    "operation_id": "op_1",
                    "action_type": "append_body",
                    "target_refs": [{"kind": "object_id", "value": "A_123"}],
                    "field_updates": [],
                    "append_text": "hello",
                    "raw_user_phrases": {},
                    "confidence": 0.9,
                    "requires_clarification": False,
                    "clarification_reason": None,
                }
            ],
            "raw_user_phrases": {},
            "confidence": 0.9,
            "object_type_hint": None,
            "requires_clarification": False,
            "clarification_reason": None,
        }
    )

    assert error is None
    assert plan is not None
    operations = plan["operations"]
    assert operations[0]["target_refs"][0]["target_token"] == "A_123"


def test_normalize_nl_mutation_plan_input_supports_multi_operation_targets() -> None:
    plan, error = transport_routing.normalize_nl_mutation_plan_input(
        {
            "schema_version": 1,
            "operations": [
                {
                    "operation_id": "op_1",
                    "action_type": "mark_done",
                    "target_refs": [{"kind": "row_number", "value": 1}, {"kind": "row_number", "value": 2}],
                    "field_updates": [],
                    "append_text": None,
                    "raw_user_phrases": {},
                    "confidence": 0.8,
                    "requires_clarification": False,
                    "clarification_reason": None,
                },
                {
                    "operation_id": "op_2",
                    "action_type": "append_body",
                    "target_refs": [{"kind": "object_id", "value": "A_8"}],
                    "field_updates": [],
                    "append_text": "follow up",
                    "raw_user_phrases": {},
                    "confidence": 0.8,
                    "requires_clarification": False,
                    "clarification_reason": None,
                },
            ],
            "raw_user_phrases": {},
            "confidence": 0.8,
            "object_type_hint": None,
            "requires_clarification": False,
            "clarification_reason": None,
        }
    )

    assert error is None
    assert plan is not None
    assert len(plan["operations"]) == 2
    assert [target["target_token"] for target in plan["operations"][0]["target_refs"]] == ["1", "2"]


def test_normalize_set_fields_returns_field_unknown_for_invalid_field() -> None:
    fields, reason, _ = transport_routing.normalize_set_fields(
        object_type="people",
        field_updates=[
            {
                "value_text": "tomorrow",
                "source_phrase": "deadline",
                "field_candidates": {
                    "primary": {"field_id": "deadline", "confidence": 0.9},
                    "alternates": [],
                },
            }
        ],
        routing=_routing_config(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=timezone.utc,
    )

    assert fields is None
    assert reason == "field_unknown"
