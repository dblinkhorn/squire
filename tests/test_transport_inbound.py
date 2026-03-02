from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from squire_core.transport import inbound
from squire_core.transport.contracts import TransportMessageContext


class _Runtime:
    def __init__(self) -> None:
        self.route_handled = False
        self.interpret_calls = 0
        self.swaps: list[tuple[str, str]] = []
        self.responses: list[str] = []
        self.unrecognized_calls = 0

    async def maybe_route_nl_command(self, **kwargs) -> bool:
        del kwargs
        return self.route_handled

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
        if self.interpret_calls == 1:
            return SimpleNamespace(
                derived={"object_type": "unknown", "confidence": 0.1},
                raw_text="{}",
            )
        raise AssertionError("extract stage should not run for low-confidence classification")

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

    assert runtime.interpret_calls == 0
    assert runtime.swaps == []
    assert runtime.responses == []


def test_inbound_low_confidence_classification_sends_clarification(tmp_path: Path) -> None:
    runtime = _Runtime()
    context = _context()
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "classify_prompt_path": "config/prompts/classify.txt",
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

    assert runtime.interpret_calls == 1
    assert runtime.swaps == [("⏳", "❓")]
    assert runtime.responses == [
        "I couldn't confidently classify that. Please clarify or use a prefix (admin:, project:, idea:, person:)."
    ]
