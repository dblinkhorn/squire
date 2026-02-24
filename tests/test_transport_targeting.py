from __future__ import annotations

from datetime import datetime, timedelta, timezone

from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.state import ResultCursor, RuntimeStateStore
from squire_core.transport.targeting import cursor_key, resolve_command_target


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


def test_cursor_key_coerces_numeric_string_ids() -> None:
    key = cursor_key(_context(user_id="11", channel_id="22"))
    assert key == (11, 22)


def test_resolve_command_target_uses_parent_thread_cursor_fallback() -> None:
    state = RuntimeStateStore()
    state.result_cursors[(11, 22)] = ResultCursor(
        object_ids=["A_1", "A_2"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
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
    state.result_cursors[(11, 22)] = ResultCursor(
        object_ids=["A_1"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    resolution = resolve_command_target(
        _context(user_id="11", channel_id="22"),
        "2",
        state_store=state,
    )

    assert resolution.target_id is None
    assert resolution.reason == "out_of_range"
    assert resolution.error == "That number is out of range for your last list."
