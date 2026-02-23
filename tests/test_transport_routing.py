from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from squire_core.transport import routing


class _Runtime:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.queued: dict[str, object] | None = None
        self.executed_command: str | None = None

    def load_prompt(self, path: str) -> str:
        assert path == "config/prompts/nl_command_routing_v1.txt"
        return "prompt"

    async def interpret_text_async(self, **kwargs):
        return SimpleNamespace(derived=self._payload, raw_text="{}")

    async def handle_command(self, message, content: str, raw_id: str, config: dict[str, object]) -> bool:
        self.executed_command = content
        return True

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
        return None


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


def test_maybe_route_nl_command_queues_mutation_plan() -> None:
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
        }
    )

    handled = asyncio.run(
        routing.maybe_route_nl_command(
            runtime=runtime,
            context=SimpleNamespace(user_id="1", channel_id="2", thread_id=None, message_id="3", content="mark 2 done", source="discord", is_dm=True, created_at=datetime(2026, 2, 22, 0, 0, tzinfo=timezone.utc)),
            content="mark 2 done",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert runtime.queued is not None
    plan_input = runtime.queued["plan_input"]
    assert plan_input["operations"][0]["action_type"] == "mark_done"
