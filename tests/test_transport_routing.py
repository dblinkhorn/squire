from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from squire_core.pending_actions import load_pending_action
from squire_core.transport import routing


class _Runtime:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.queued: dict[str, object] | None = None
        self.executed_command: str | None = None
        self.responses: list[str] = []
        self.recent_affinity_ids: list[str] = []
        self.frontmatter_by_id: dict[str, dict[str, object]] = {}

    def load_prompt(self, path: str) -> str:
        assert path == "config/prompts/message_triage_v1.txt"
        return "prompt"

    async def interpret_text_async(self, **kwargs):
        return SimpleNamespace(derived=self._payload, raw_text="{}")

    async def handle_command(self, message, content: str, raw_id: str, config: dict[str, object]) -> bool:
        self.executed_command = content
        return True

    def load_recent_affinity_ids(self, context, config: dict[str, object]) -> list[str]:
        return list(self.recent_affinity_ids)

    async def queue_nl_mutation_confirmation(self, **kwargs) -> bool:
        self.queued = kwargs
        return True

    def load_nl_clarification_context(self, message):
        return None

    def clear_nl_clarification_context(self, message) -> None:  # pragma: no cover - not hit in this test
        raise AssertionError("unexpected")

    def store_nl_clarification_context(self, **kwargs) -> None:  # pragma: no cover - not hit in this test
        raise AssertionError("unexpected")

    async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
        return None

    async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
        self.responses.append(content)
        return None

    def find_object_path(self, objects_root: str | Path, target_id: str) -> Path | None:
        if target_id not in self.frontmatter_by_id:
            return None
        return Path(f"/tmp/{target_id}.md")

    def load_frontmatter(self, path: str | Path) -> dict[str, object]:
        return self.frontmatter_by_id[Path(path).stem]


class _QueueRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.responses: list[str] = []
        self.stored_clarification: dict[str, object] | None = None
        self.trace_payload: dict[str, object] | None = None
        self.pending_view_created = False

    async def swap_reaction(self, context, remove_emoji: str, add_emoji: str) -> None:
        return None

    async def send_response(self, context, content: str, *, thread_title=None, view=None) -> None:
        self.responses.append(content)

    def resolve_command_target(self, context, target_token: str):
        assert target_token == "A_1"
        return SimpleNamespace(target_id="A_1", error=None, reason=None, row_number=None, source_view=None)

    def map_target_resolution_reason_to_plan_reason(self, reason: str | None) -> str:
        return reason or "target_missing"

    def log_numbered_mutation_resolution_failed(self, **kwargs) -> None:
        return None

    def log_numbered_mutation_resolved(self, **kwargs) -> None:
        return None

    def find_object_path(self, objects_root: str | Path, target_id: str) -> Path | None:
        assert target_id == "A_1"
        return self.root / "A_1.md"

    def load_frontmatter(self, path: str | Path) -> dict[str, object]:
        return {
            "type": "admin",
            "title": "Dentist appointment",
            "due_at": "2026-03-24T14:00:00-07:00",
        }

    def write_nl_mutation_normalized_trace(self, *, config, raw_event_id: str, payload: dict[str, object]) -> None:
        self.trace_payload = payload

    def create_mutation_pending_view(self, **kwargs):
        self.pending_view_created = True
        return object()

    def cursor_key(self, context) -> tuple[int, int]:
        return (1, 2)

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        return None

    def now_iso(self) -> str:
        return "2026-03-23T22:00:00-07:00"

    def store_nl_clarification_context(
        self,
        *,
        context,
        raw_event_id: str,
        unresolved_scope: dict[str, dict[str, object]],
        base_plan_input: dict[str, object],
    ) -> None:
        self.stored_clarification = {
            "raw_event_id": raw_event_id,
            "unresolved_scope": unresolved_scope,
            "base_plan_input": base_plan_input,
        }


def test_normalize_nl_mutation_plan_input_multi_targets() -> None:
    plan, error = routing.normalize_nl_mutation_plan_input(
        {
            "schema_version": 1,
            "operations": [
                {
                    "operation_id": "op_1",
                    "action_type": "mark_done",
                    "target_refs": [{"kind": "row_number", "value": 1}, {"kind": "object_id", "value": "A_2"}],
                    "field_updates": [],
                    "append_text": None,
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
    refs = plan["operations"][0]["target_refs"]
    assert [target["target_token"] for target in refs] == ["1", "A_2"]


def test_normalize_set_fields_prefers_due_at_for_time_hint() -> None:
    fields, reason, notes = routing.normalize_set_fields(
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
        routing=SimpleNamespace(),
        now=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
        tz=timezone.utc,
    )

    assert reason is None
    assert fields is not None
    assert fields["due_at"].startswith("2026-02-18T15:00:00")
    assert notes == ["due_choice:due_at"]


def test_triage_message_queues_mutation_plan() -> None:
    runtime = _Runtime(
        {
            "schema_version": 1,
            "route": "mutation_plan",
            "intent": "done",
            "risk_tier": "mutation",
            "confidence": 0.95,
            "ambiguities": [],
            "read_command": None,
            "mutation_plan": {
                "schema_version": 1,
                "operations": [
                    {
                        "operation_id": "op_1",
                        "action_type": "mark_done",
                        "target_refs": [{"kind": "row_number", "value": 2}],
                        "field_updates": [],
                        "append_text": None,
                        "raw_user_phrases": {},
                        "confidence": 0.95,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    }
                ],
                "raw_user_phrases": {},
                "confidence": 0.95,
                "object_type_hint": None,
                "requires_clarification": False,
                "clarification_reason": None,
            },
            "clarification": None,
            "capture": {"object_type": "unknown", "confidence": 0.0},
        }
    )

    outcome = asyncio.run(
        routing.triage_message(
            runtime=runtime,
            context=SimpleNamespace(user_id="1", channel_id="2", thread_id=None, message_id="3", content="mark 2 done", source="discord", is_dm=True, created_at=datetime(2026, 2, 22, 0, 0, tzinfo=timezone.utc)),
            content="mark 2 done",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert outcome.handled is True
    assert runtime.queued is not None
    plan_input = runtime.queued["plan_input"]
    assert plan_input["operations"][0]["action_type"] == "mark_done"


def test_triage_message_medium_confidence_mutation_queues_runtime_confirmation() -> None:
    runtime = _Runtime(
        {
            "schema_version": 1,
            "route": "mutation_plan",
            "intent": "fix",
            "risk_tier": "mutation",
            "confidence": 0.6,
            "ambiguities": [],
            "read_command": None,
            "mutation_plan": {
                "schema_version": 1,
                "operations": [
                    {
                        "operation_id": "op_1",
                        "action_type": "set_fields",
                        "target_refs": [{"kind": "object_id", "value": "A_1"}],
                        "field_updates": [
                            {
                                "value_text": "1",
                                "source_phrase": "due time",
                                "field_candidates": {
                                    "primary": {"field_id": "due_at", "confidence": 0.95},
                                    "alternates": [],
                                },
                            }
                        ],
                        "append_text": None,
                        "raw_user_phrases": {},
                        "confidence": 0.6,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    }
                ],
                "raw_user_phrases": {},
                "confidence": 0.6,
                "object_type_hint": None,
                "requires_clarification": False,
                "clarification_reason": None,
            },
            "clarification": {
                "question": "stale model clarification",
                "options": ["1:00 AM today", "1:00 PM today"],
            },
            "capture": {"object_type": "unknown", "confidence": 0.0},
        }
    )

    outcome = asyncio.run(
        routing.triage_message(
            runtime=runtime,
            context=SimpleNamespace(
                user_id="1",
                channel_id="2",
                thread_id=None,
                message_id="3",
                content="actually the dentist appointment is at 1",
                source="discord",
                is_dm=True,
                created_at=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
            ),
            content="actually the dentist appointment is at 1",
            raw_id="R_2",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert outcome.handled is True
    assert runtime.queued is not None
    assert runtime.responses == []


def test_triage_message_infers_recent_target_for_missing_mutation_target() -> None:
    runtime = _Runtime(
        {
            "schema_version": 1,
            "route": "mutation_plan",
            "intent": "fix",
            "risk_tier": "mutation",
            "confidence": 0.6,
            "ambiguities": [],
            "read_command": None,
            "mutation_plan": {
                "schema_version": 1,
                "operations": [
                    {
                        "operation_id": "op_1",
                        "action_type": "set_fields",
                        "target_refs": [],
                        "field_updates": [
                            {
                                "value_text": "1",
                                "source_phrase": "due time",
                                "field_candidates": {
                                    "primary": {"field_id": "due_at", "confidence": 0.95},
                                    "alternates": [],
                                },
                            }
                        ],
                        "append_text": None,
                        "raw_user_phrases": {},
                        "confidence": 0.6,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    }
                ],
                "raw_user_phrases": {},
                "confidence": 0.6,
                "object_type_hint": "admin",
                "requires_clarification": False,
                "clarification_reason": None,
            },
            "clarification": {
                "question": "stale model clarification",
                "options": [
                    "no target reference specified (which note/item should be updated?)",
                    "time ambiguous: '1' could mean 1:00 AM/PM and no date provided",
                ],
            },
            "capture": {"object_type": "unknown", "confidence": 0.0},
        }
    )
    runtime.recent_affinity_ids = ["A_1"]
    runtime.frontmatter_by_id = {
        "A_1": {
            "type": "admin",
            "title": "Dentist appointment",
            "due_at": "2026-03-24T14:00:00-07:00",
        }
    }

    outcome = asyncio.run(
        routing.triage_message(
            runtime=runtime,
            context=SimpleNamespace(
                user_id="1",
                channel_id="2",
                thread_id=None,
                message_id="3",
                content="actually the dentist appt is at 1",
                source="discord",
                is_dm=True,
                created_at=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
            ),
            content="actually the dentist appt is at 1",
            raw_id="R_4",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert outcome.handled is True
    assert runtime.queued is not None
    plan_input = runtime.queued["plan_input"]
    assert plan_input["operations"][0]["target_refs"][0]["target_token"] == "A_1"
    assert runtime.responses == []


def test_triage_message_missing_mutation_target_requires_overlap_instead_of_pure_recency() -> None:
    runtime = _Runtime(
        {
            "schema_version": 1,
            "route": "mutation_plan",
            "intent": "fix",
            "risk_tier": "mutation",
            "confidence": 0.6,
            "ambiguities": [],
            "read_command": None,
            "mutation_plan": {
                "schema_version": 1,
                "operations": [
                    {
                        "operation_id": "op_1",
                        "action_type": "set_fields",
                        "target_refs": [],
                        "field_updates": [
                            {
                                "value_text": "1",
                                "source_phrase": "due time",
                                "field_candidates": {
                                    "primary": {"field_id": "due_at", "confidence": 0.95},
                                    "alternates": [],
                                },
                            }
                        ],
                        "append_text": None,
                        "raw_user_phrases": {},
                        "confidence": 0.6,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    }
                ],
                "raw_user_phrases": {},
                "confidence": 0.6,
                "object_type_hint": "admin",
                "requires_clarification": False,
                "clarification_reason": None,
            },
            "clarification": {
                "question": "Which note should I update?",
                "options": [
                    "Tell me which note/item should be updated.",
                    "Tell me the exact date/time.",
                ],
            },
            "capture": {"object_type": "unknown", "confidence": 0.0},
        }
    )
    runtime.recent_affinity_ids = ["A_1"]
    runtime.frontmatter_by_id = {
        "A_1": {
            "type": "admin",
            "title": "Dentist appointment",
            "due_at": "2026-03-24T14:00:00-07:00",
        }
    }

    outcome = asyncio.run(
        routing.triage_message(
            runtime=runtime,
            context=SimpleNamespace(
                user_id="1",
                channel_id="2",
                thread_id=None,
                message_id="3",
                content="actually it's at 1",
                source="discord",
                is_dm=True,
                created_at=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
            ),
            content="actually it's at 1",
            raw_id="R_4b",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert outcome.handled is True
    assert runtime.queued is None
    assert runtime.responses
    assert "Which note should I update?" in runtime.responses[0]


def test_triage_message_clarify_fix_recovers_to_runtime_mutation_plan() -> None:
    runtime = _Runtime(
        {
            "schema_version": 1,
            "route": "clarify",
            "intent": "fix",
            "risk_tier": "mutation",
            "confidence": 0.95,
            "ambiguities": [],
            "read_command": None,
            "mutation_plan": None,
            "clarification": {
                "question": "Do you mean update the dentist appointment time?",
                "options": [
                    "Update the most recent note about the dentist to 1:00 PM (today).",
                    "Update the most recent note about the dentist to 1:00 AM (today).",
                ],
            },
            "capture": {"object_type": "unknown", "confidence": 0.0},
        }
    )
    runtime.recent_affinity_ids = ["A_1"]
    runtime.frontmatter_by_id = {
        "A_1": {
            "type": "admin",
            "title": "Dentist appointment",
            "due_at": "2026-03-24T14:00:00-07:00",
        }
    }

    outcome = asyncio.run(
        routing.triage_message(
            runtime=runtime,
            context=SimpleNamespace(
                user_id="1",
                channel_id="2",
                thread_id=None,
                message_id="3",
                content="actually the dentist appt is at 1",
                source="discord",
                is_dm=True,
                created_at=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
            ),
            content="actually the dentist appt is at 1",
            raw_id="R_5",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert outcome.handled is True
    assert runtime.queued is not None
    plan_input = runtime.queued["plan_input"]
    assert plan_input["operations"][0]["target_refs"][0]["target_token"] == "A_1"
    assert plan_input["operations"][0]["field_updates"][0]["value_text"] == "1"
    assert runtime.responses == []


def test_triage_message_clarify_fix_requires_overlap_instead_of_pure_recency() -> None:
    runtime = _Runtime(
        {
            "schema_version": 1,
            "route": "clarify",
            "intent": "fix",
            "risk_tier": "mutation",
            "confidence": 0.95,
            "ambiguities": [],
            "read_command": None,
            "mutation_plan": None,
            "clarification": {
                "question": "Do you mean update the appointment time?",
                "options": [
                    "Update the most recent note to 1:00 PM.",
                    "Update the most recent note to 1:00 AM.",
                ],
            },
            "capture": {"object_type": "unknown", "confidence": 0.0},
        }
    )
    runtime.recent_affinity_ids = ["A_1"]
    runtime.frontmatter_by_id = {
        "A_1": {
            "type": "admin",
            "title": "Dentist appointment",
            "due_at": "2026-03-24T14:00:00-07:00",
        }
    }

    outcome = asyncio.run(
        routing.triage_message(
            runtime=runtime,
            context=SimpleNamespace(
                user_id="1",
                channel_id="2",
                thread_id=None,
                message_id="3",
                content="actually it's at 1",
                source="discord",
                is_dm=True,
                created_at=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
            ),
            content="actually it's at 1",
            raw_id="R_6",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert outcome.handled is True
    assert runtime.queued is None
    assert runtime.responses
    assert "Options:" in runtime.responses[0]


def test_queue_nl_mutation_confirmation_infers_meridiem_from_existing_note(tmp_path: Path) -> None:
    runtime = _QueueRuntime(tmp_path)
    pending_root = tmp_path / "pending"
    handled = asyncio.run(
        routing.queue_nl_mutation_confirmation(
            runtime=runtime,
            context=SimpleNamespace(user_id="1", channel_id="2"),
            raw_id="R_3",
            config={
                "timezone": "America/Los_Angeles",
                "llm": {"provider": "openai", "model": "gpt-5-mini"},
                "matching": {"semantic_weight": 0},
                "paths": {"pending_actions": str(pending_root), "objects_root": str(tmp_path), "index_db": str(tmp_path / "index.sqlite")},
            },
            plan_input={
                "operations": [
                    {
                        "operation_id": "op_1",
                        "action_type": "set_fields",
                        "target_refs": [{"target_ref": {"kind": "object_id", "value": "A_1"}, "target_token": "A_1"}],
                        "field_updates": [
                            {
                                "value_text": "1",
                                "source_phrase": "due time",
                                "field_candidates": {
                                    "primary": {"field_id": "due_at", "confidence": 0.95},
                                    "alternates": [],
                                },
                            }
                        ],
                        "append_text": None,
                        "raw_user_phrases": {},
                        "confidence": 0.6,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    }
                ],
                "confidence": 0.6,
                "requires_clarification": False,
                "clarification_reason": None,
                "object_type_hint": None,
                "raw_user_phrases": {},
            },
            confidence=0.6,
            routing=SimpleNamespace(plan_trace_enabled=True),
        )
    )

    assert handled is True
    assert runtime.stored_clarification is None
    assert runtime.pending_view_created is True
    assert runtime.responses
    pending_files = list(pending_root.glob("*.json"))
    assert len(pending_files) == 1
    pending = load_pending_action(pending_root, pending_files[0].stem)
    assert pending is not None
    operations = pending.derived["proposed_operations"]
    assert operations[0]["fields"]["due_at"] == "2026-03-24T13:00:00-07:00"
