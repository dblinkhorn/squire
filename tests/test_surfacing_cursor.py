from __future__ import annotations

from datetime import datetime, timedelta, timezone

from squire_core.transport.state import ResultCursor, RuntimeStateStore
from squire_core.transport.targeting import cursor_key, resolve_result_cursor, store_result_cursor


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Channel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _Message:
    def __init__(self, user_id: int = 1, channel_id: int = 2) -> None:
        self.author = _Author(user_id)
        self.channel = _Channel(channel_id)


def test_result_cursor_store_and_resolve() -> None:
    state = RuntimeStateStore()
    message = _Message()

    config = {"surfacing": {"pull": {"cursor_ttl_minutes": 45}}}
    store_result_cursor(message, config, ["A_1", "A_2"], state_store=state)

    assert resolve_result_cursor(message, 1, state_store=state) == "A_1"
    assert resolve_result_cursor(message, 2, state_store=state) == "A_2"
    assert resolve_result_cursor(message, 3, state_store=state) is None



def test_result_cursor_expires() -> None:
    state = RuntimeStateStore()
    message = _Message()
    key = cursor_key(message)

    state.result_cursors[key] = ResultCursor(
        object_ids=["A_1"],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert resolve_result_cursor(message, 1, state_store=state) is None
    assert key not in state.result_cursors
