from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from squire_core.transport import routing as transport_routing
from squire_core.transport.discord.command_contract import NL_OUT_OF_SCOPE_CLARIFICATION_COPY
from squire_core.transport.discord.context import build_transport_context
from squire_core.transport.discord import message_entry
from squire_core.transport.state import (
    RuntimeStateStore,
    get_nl_clarification_context,
    store_nl_clarification_context,
)
from squire_core.transport.targeting import cursor_key
from squire_core.transport.discord import runtime_adapter_command as command_adapter
from squire_core.transport.discord import runtime_adapter_routing as routing_adapter
from squire_core.config_utils import load_nl_command_routing_config


class _Author:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class _Channel:
    def __init__(self, channel_id: int, *, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _Message:
    def __init__(self, content: str = "", *, user_id: int = 1, channel_id: int = 2) -> None:
        self.author = _Author(user_id)
        self.channel = _Channel(channel_id)
        self.content = content


def _mutation_plan_payload(*, operations: list[dict[str, object]], confidence: float = 0.9) -> dict[str, object]:
    return {
        "schema_version": 1,
        "route": "mutation_plan",
        "intent": "fix",
        "risk_tier": "mutation",
        "confidence": confidence,
        "ambiguities": [],
        "read_command": None,
        "mutation_plan": {
            "schema_version": 1,
            "operations": operations,
            "raw_user_phrases": {},
            "confidence": confidence,
            "object_type_hint": None,
            "requires_clarification": False,
            "clarification_reason": None,
        },
        "clarification": None,
        "capture": {"object_type": "unknown", "confidence": 0.0},
    }


def test_clarification_reply_out_of_scope_is_blocked(monkeypatch) -> None:
    calls: list[str] = []
    state = RuntimeStateStore()
    message = _Message("what now", user_id=10, channel_id=20)
    store_nl_clarification_context(
        cursor_key(message),
        raw_event_id="R_prev",
        unresolved_scope={"op_1": {"action_type": "set_fields", "target_tokens": ["1"], "reason_code": "field_ambiguous"}},
        base_plan_input={"operations": []},
        ttl_seconds=600,
        state_store=state,
    )

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived={
                "schema_version": 1,
                "route": "read_command",
                "intent": "status",
                "risk_tier": "read",
                "confidence": 0.95,
                "ambiguities": [],
                "read_command": {"intent": "status", "args": {}},
                "mutation_plan": None,
                "clarification": None,
                "capture": {"object_type": "unknown", "confidence": 0.0},
            },
            raw_text="{}",
        )

    def _fake_load_prompt(path):
        return "prompt"

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(routing_adapter, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(routing_adapter, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(message_entry._discord_io, "swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(message_entry._discord_io, "send_response", _fake_send_response)

    handled = asyncio.run(
        message_entry.triage_message(
            message=message,
            content=message.content,
            raw_id="R_now",
            config={},
            provider=object(),
            model="gpt-5-mini",
            runtime_state=state,
        )
    )

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any(NL_OUT_OF_SCOPE_CLARIFICATION_COPY in call for call in calls)
    assert any("Unresolved operations: op_1 (field_ambiguous)" in call for call in calls)
    assert get_nl_clarification_context(cursor_key(message), state_store=state) is None


def test_clarification_reply_in_scope_merges_and_disables_second_turn(monkeypatch) -> None:
    captured: dict[str, object] = {}
    state = RuntimeStateStore()
    message = _Message("clarify", user_id=11, channel_id=21)
    base_plan = {
        "operations": [
            {
                "operation_id": "op_1",
                "action_type": "mark_done",
                "target_refs": [{"target_ref": {"kind": "row_number", "value": 1}, "target_token": "1"}],
                "field_updates": [],
                "append_text": None,
                "raw_user_phrases": {},
                "confidence": 0.9,
                "requires_clarification": False,
                "clarification_reason": None,
            },
            {
                "operation_id": "op_2",
                "action_type": "set_fields",
                "target_refs": [{"target_ref": {"kind": "row_number", "value": 2}, "target_token": "2"}],
                "field_updates": [],
                "append_text": None,
                "raw_user_phrases": {},
                "confidence": 0.6,
                "requires_clarification": True,
                "clarification_reason": "field_ambiguous",
            },
        ],
        "confidence": 0.8,
        "requires_clarification": True,
        "clarification_reason": "field_ambiguous",
        "object_type_hint": None,
        "raw_user_phrases": {},
    }
    store_nl_clarification_context(
        cursor_key(message),
        raw_event_id="R_prev",
        unresolved_scope={"op_2": {"action_type": "set_fields", "target_tokens": ["2"], "reason_code": "field_ambiguous"}},
        base_plan_input=base_plan,
        ttl_seconds=600,
        state_store=state,
    )

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_mutation_plan_payload(
                operations=[
                    {
                        "operation_id": "op_2",
                        "action_type": "set_fields",
                        "target_refs": [{"kind": "row_number", "value": 2}],
                        "field_updates": [
                            {
                                "value_text": "high",
                                "source_phrase": "priority",
                                "field_candidates": {
                                    "primary": {"field_id": "priority", "confidence": 0.9},
                                    "alternates": [],
                                },
                            }
                        ],
                        "append_text": None,
                        "raw_user_phrases": {},
                        "confidence": 0.9,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    }
                ]
            ),
            raw_text="{}",
        )

    def _fake_load_prompt(path):
        return "prompt"

    async def _fake_queue_nl_mutation_confirmation(
        *,
        runtime,
        context,
        raw_id,
        config,
        plan_input,
        confidence,
        routing,
        source_view=None,
        allow_clarification=True,
    ):
        captured["allow_clarification"] = allow_clarification
        captured["operation_ids"] = [operation["operation_id"] for operation in plan_input["operations"]]
        return True

    monkeypatch.setattr(routing_adapter, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(routing_adapter, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(transport_routing, "queue_nl_mutation_confirmation", _fake_queue_nl_mutation_confirmation)

    handled = asyncio.run(
        message_entry.triage_message(
            message=message,
            content=message.content,
            raw_id="R_now",
            config={},
            provider=object(),
            model="gpt-5-mini",
            runtime_state=state,
        )
    )

    assert handled is True
    assert captured["allow_clarification"] is False
    assert captured["operation_ids"] == ["op_1", "op_2"]


def test_multi_operation_conflict_marks_operation_conflict(monkeypatch) -> None:
    trace_payload: dict[str, object] = {}
    responses: list[str] = []
    state = RuntimeStateStore()
    message = _Message("conflict", user_id=12, channel_id=22)

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        responses.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        responses.append(content)

    def _fake_find_object_path(objects_root, target_id):
        return Path(f"/tmp/{target_id}.md")

    def _fake_load_frontmatter(path):
        return {"type": "admin", "title": "Task 1"}

    def _fake_write_trace(*, config, raw_event_id, payload):
        trace_payload.update(payload)

    monkeypatch.setattr(message_entry._discord_io, "swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(message_entry._discord_io, "send_response", _fake_send_response)
    monkeypatch.setattr(routing_adapter, "find_object_path", _fake_find_object_path)
    monkeypatch.setattr(routing_adapter, "load_frontmatter", _fake_load_frontmatter)
    monkeypatch.setattr(routing_adapter, "_write_nl_mutation_normalized_trace", _fake_write_trace)

    plan_input = {
        "operations": [
            {
                "operation_id": "op_1",
                "action_type": "set_fields",
                "target_refs": [{"target_ref": {"kind": "object_id", "value": "A_1"}, "target_token": "A_1"}],
                "field_updates": [
                    {
                        "value_text": "First title",
                        "source_phrase": "title",
                        "field_candidates": {"primary": {"field_id": "title", "confidence": 0.9}, "alternates": []},
                    }
                ],
                "append_text": None,
                "raw_user_phrases": {},
                "confidence": 0.9,
                "requires_clarification": False,
                "clarification_reason": None,
            },
            {
                "operation_id": "op_2",
                "action_type": "set_fields",
                "target_refs": [{"target_ref": {"kind": "object_id", "value": "A_1"}, "target_token": "A_1"}],
                "field_updates": [
                    {
                        "value_text": "Second title",
                        "source_phrase": "title",
                        "field_candidates": {"primary": {"field_id": "title", "confidence": 0.9}, "alternates": []},
                    }
                ],
                "append_text": None,
                "raw_user_phrases": {},
                "confidence": 0.9,
                "requires_clarification": False,
                "clarification_reason": None,
            },
        ],
        "confidence": 0.9,
        "requires_clarification": False,
        "clarification_reason": None,
        "object_type_hint": None,
        "raw_user_phrases": {},
    }

    runtime = routing_adapter._DiscordRoutingRuntime(
        message,
        state,
        command_runtime_factory=command_adapter._DiscordCommandRuntime,
    )
    context = build_transport_context(message)
    handled = asyncio.run(
        transport_routing.queue_nl_mutation_confirmation(
            runtime=runtime,
            context=context,
            raw_id="R_conflict",
            config={
                "llm": {"provider": "openai", "model": "gpt-5-mini"},
                "matching": {"semantic_weight": 0},
            },
            plan_input=plan_input,
            confidence=0.9,
            routing=load_nl_command_routing_config({}),
            allow_clarification=False,
        )
    )

    assert handled is True
    operations = trace_payload.get("operations")
    assert isinstance(operations, list)
    reason_codes = {item["reason_code"] for item in operations if isinstance(item, dict)}
    assert "operation_conflict" in reason_codes
    assert trace_payload.get("validation_outcome") == "blocked"
    assert any("no changes were made" in response.lower() for response in responses)
