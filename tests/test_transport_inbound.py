from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from squire_core.interpreter import InterpretationValidationError
from squire_core.transport import inbound
from squire_core.transport import matching_pipeline
from squire_core.transport.contracts import TransportMessageContext


class _Runtime:
    def __init__(self) -> None:
        self.route_handled = False
        self.triage_calls = 0
        self.interpret_calls = 0
        self.swaps: list[tuple[str, str]] = []
        self.responses: list[str] = []
        self.unrecognized_calls = 0

    async def triage_message(self, *, context, content, raw_id, config, provider, model):
        del context
        del content, raw_id, config, provider, model
        self.triage_calls += 1
        return SimpleNamespace(
            handled=self.route_handled,
            triage=SimpleNamespace(
                route="read_command" if self.route_handled else "capture_fallthrough",
                intent="recent" if self.route_handled else "none",
                risk_tier="read" if self.route_handled else "none",
                confidence=0.96 if self.route_handled else 0.1,
                ambiguities=[],
                read_command={"intent": "recent", "args": {}} if self.route_handled else None,
                mutation_plan=None,
                clarification=None,
                capture=SimpleNamespace(object_type="unknown", confidence=0.1),
            ),
        )

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        del context
        self.swaps.append((remove_emoji, add_emoji))

    async def send_response(
        self,
        context: TransportMessageContext,
        content: str,
        *,
        thread_title: str | None = None,
        view: Any = None,
    ) -> None:
        del context, thread_title, view
        self.responses.append(content)

    async def send_unrecognized_category(self, context: TransportMessageContext) -> None:
        del context
        self.unrecognized_calls += 1

    def load_prompt(self, path: str) -> str:
        return f"prompt:{path}"

    async def interpret_text_async(self, **kwargs):
        self.interpret_calls += 1
        raise AssertionError("extract should not run for low-confidence capture triage")

    def now_iso(self) -> str:
        return "2026-02-23T00:00:00+00:00"

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        del context
        return (1, 2)

    def load_affinity_scores(self, key: tuple[int, int], *, matching: Any) -> dict[str, float]:
        del key, matching
        return {}

    def write_matching_trace(self, *, derived_root: str | Path, raw_event_id: str, trace_payload: dict[str, Any]) -> None:
        del derived_root, raw_event_id, trace_payload
        raise AssertionError("matching trace should not be written in this scenario")

    def apply_operations(self, **kwargs):
        raise AssertionError("apply should not run in this scenario")

    async def refresh_index_async(self, objects_root: str | Path, index_db: str | Path, *, matching: Any = None) -> None:
        del objects_root, index_db, matching
        raise AssertionError("refresh should not run in this scenario")

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        del clear_state
        raise AssertionError("reminder updates should not run in this scenario")

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        del derived
        return []

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        del paths
        return []

    def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
        del key, object_ids, matching
        raise AssertionError("affinity touches should not run in this scenario")

    def author_id(self, context: TransportMessageContext) -> int:
        del context
        return 1

    def create_pending_action_view(self, **kwargs) -> Any:
        del kwargs
        raise AssertionError("pending view should not be created in this scenario")

    def format_pending_message(self, pending_id: str, decision_payload: dict[str, Any]) -> str:
        del pending_id, decision_payload
        return ""

    def create_auto_apply_feedback_view(self, *, author_id: int, target_id: str) -> Any:
        del author_id, target_id
        return None


def _context() -> TransportMessageContext:
    return TransportMessageContext(
        source="discord",
        user_id="1",
        channel_id="2",
        thread_id=None,
        message_id="3",
        content="test",
        is_dm=True,
        created_at=datetime.now(timezone.utc),
    )


def test_inbound_returns_early_when_nl_route_handles() -> None:
    runtime = _Runtime()
    runtime.route_handled = True
    context = _context()

    asyncio.run(
        inbound.handle_non_command_message(
            runtime=runtime,
            context=context,
            content="show my notes",
            raw_id="R_1",
            config={},
            provider=SimpleNamespace(),
            model="gpt-5-mini",
            schema_map={"admin": Path("config/schemas/derived_event_admin_v1.json")},
        )
    )

    assert runtime.triage_calls == 1
    assert runtime.interpret_calls == 0
    assert runtime.swaps == []
    assert runtime.responses == []


def test_inbound_low_confidence_capture_sends_clarification(tmp_path: Path) -> None:
    runtime = _Runtime()
    context = _context()
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "message_triage_prompt_path": "config/prompts/message_triage_v1.txt",
            "interpreter_prompt_path": "config/prompts/extract.txt",
        },
        "matching": {
            "semantic_weight": 0,
        },
        "paths": {
            "events_derived": str(tmp_path / "events" / "derived"),
        },
        "confidence": {
            "create_threshold": 0.6,
        },
    }

    asyncio.run(
        inbound.handle_non_command_message(
            runtime=runtime,
            context=context,
            content="unclear note",
            raw_id="R_2",
            config=config,
            provider=SimpleNamespace(),
            model="gpt-5-mini",
            schema_map={"admin": Path("config/schemas/derived_event_admin_v1.json")},
        )
    )

    assert runtime.triage_calls == 1
    assert runtime.interpret_calls == 0
    assert runtime.swaps == [("⏳", "❓")]
    assert runtime.responses == [
        "I couldn't confidently classify that. Please clarify or use a prefix (admin:, project:, idea:, person:)."
    ]


def test_inbound_falls_back_to_plain_extract_when_fused_capture_is_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class _DecisionRuntime(_Runtime):
        async def triage_message(self, *, context, content, raw_id, config, provider, model):
            del context, content, raw_id, config, provider, model
            self.triage_calls += 1
            return SimpleNamespace(
                handled=False,
                triage=SimpleNamespace(
                    route="capture_fallthrough",
                    intent="none",
                    risk_tier="none",
                    confidence=0.92,
                    ambiguities=[],
                    read_command=None,
                    mutation_plan=None,
                    clarification=None,
                    capture=SimpleNamespace(object_type="admin", confidence=0.92),
                ),
            )

        async def interpret_text_async(self, **kwargs):
            self.interpret_calls += 1
            calls.append(kwargs["text"])
            if self.interpret_calls == 1:
                raise InterpretationValidationError(
                    "bad fused payload",
                    raw_text="{}",
                    payload={"schema_version": 1},
                )
            return SimpleNamespace(
                derived={
                    "schema_version": 1,
                    "raw_event_id": None,
                    "object_type": "admin",
                    "intent": "create",
                    "extracted_fields": {
                        "title": "Call dentist",
                        "status": "open",
                        "next_action": "Call dentist",
                        "due_date": None,
                        "due_at": None,
                        "priority": None,
                        "blocked_reason": None,
                    },
                    "confidence": 0.85,
                    "proposed_operations": [
                        {
                            "op": "create",
                            "target_id": None,
                            "fields": {
                                "title": "Call dentist",
                                "status": "open",
                                "next_action": "Call dentist",
                                "due_date": None,
                                "due_at": None,
                                "priority": None,
                                "blocked_reason": None,
                            },
                        }
                    ],
                    "model": "gpt-5-mini",
                    "prompt_version": "extract_v1",
                    "timestamp": "2026-03-22T10:00:00+00:00",
                },
                raw_text="{}",
            )

        def write_matching_trace(self, *, derived_root: str | Path, raw_event_id: str, trace_payload: dict[str, Any]) -> None:
            del derived_root, raw_event_id, trace_payload

        def apply_operations(self, derived, **kwargs):
            del derived, kwargs
            return SimpleNamespace(written_paths=[tmp_path / "objects" / "admin" / "A_1.md"])

        async def refresh_index_async(self, objects_root: str | Path, index_db: str | Path, *, matching: Any = None) -> None:
            del objects_root, index_db, matching

        def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
            del clear_state

        def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
            del paths
            return ["A_1"]

        def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
            del key, object_ids, matching

    runtime = _DecisionRuntime()
    context = _context()
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "message_triage_prompt_path": "config/prompts/message_triage_v1.txt",
            "interpreter_prompt_path": "config/prompts/extract_v1.txt",
            "decision_prompt_path": "config/prompts/decision_v1.txt",
        },
        "matching": {
            "semantic_weight": 0,
        },
        "decision": {
            "candidate_limit": 3,
            "candidate_score_threshold": 0.2,
            "auto_apply_threshold": 0.85,
            "confirm_threshold": 0.65,
        },
        "paths": {
            "events_derived": str(tmp_path / "events" / "derived"),
            "objects_root": str(tmp_path / "objects"),
            "index_db": str(tmp_path / "index.sqlite"),
        },
        "confidence": {
            "create_threshold": 0.6,
        },
    }

    async def _fake_build_matching_context(**kwargs):
        del kwargs
        return matching_pipeline.MatchingContext(
            candidates=[
                SimpleNamespace(
                    object_id="A_1",
                    title="Call dentist",
                    snippet="Call dentist next Tuesday",
                    score=0.93,
                )
            ],
            matching_trace={
                "schema_version": 1,
                "ranking": {"top_score": 0.93, "second_score": 0.2, "margin": 0.73},
                "gate": {},
            },
        )

    monkeypatch.setattr(matching_pipeline, "build_matching_context", _fake_build_matching_context)

    asyncio.run(
        inbound.handle_non_command_message(
            runtime=runtime,
            context=context,
            content="Call dentist tomorrow",
            raw_id="R_3",
            config=config,
            provider=SimpleNamespace(),
            model="gpt-5-mini",
            schema_map={"admin": Path("config/schemas/derived_event_admin_v1.json")},
        )
    )

    assert runtime.triage_calls == 1
    assert runtime.interpret_calls == 2
    assert calls[0] != "Call dentist tomorrow"
    assert calls[1] == "Call dentist tomorrow"
    assert runtime.responses == ['Saved "Call dentist" in Admin.']
