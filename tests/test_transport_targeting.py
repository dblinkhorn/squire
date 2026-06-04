from __future__ import annotations

from datetime import datetime, timezone

from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.state import ResultCursor, RuntimeStateStore
from squire_core.transport.targeting import resolve_command_target, result_cursor_key


def _context(*, user_id: str, channel_id: str, thread_id: str | None = None) -> TransportMessageContext:
    return TransportMessageContext(
        source="discord",
        user_id=user_id,
        channel_id=channel_id,
        thread_id=thread_id,
        message_id="M_1",
        content="",
        is_dm=True,
        created_at=datetime.now(timezone.utc),
    )


def test_result_cursor_key_uses_root_channel_id() -> None:
    assert result_cursor_key(_context(user_id="11", channel_id="22")) == 22
    assert result_cursor_key(_context(user_id="11", channel_id="77", thread_id="22")) == 22


def test_resolve_command_target_uses_conversation_root_cursor() -> None:
    state = RuntimeStateStore()
    state.result_cursors[22] = ResultCursor(
        object_ids=["A_1", "A_2"],
        source_view="status",
    )

    resolution = resolve_command_target(
        _context(user_id="11", channel_id="77", thread_id="22"),
        "2",
        state_store=state,
    )

    assert resolution.target_id == "A_2"
    assert resolution.error is None
    assert resolution.row_number == 2
    assert resolution.source_view == "status"


def test_resolve_command_target_out_of_range_returns_guidance() -> None:
    state = RuntimeStateStore()
    state.result_cursors[22] = ResultCursor(
        object_ids=["A_1"],
    )

    resolution = resolve_command_target(
        _context(user_id="11", channel_id="22"),
        "2",
        state_store=state,
    )

    assert resolution.target_id is None
    assert resolution.reason == "out_of_range"
    assert resolution.error == "That number is out of range for your last list."
