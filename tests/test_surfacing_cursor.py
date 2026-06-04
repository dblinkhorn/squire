from __future__ import annotations

from squire_core.transport.state import RuntimeStateStore
from squire_core.transport.targeting import resolve_result_cursor, result_cursor_key, store_result_cursor


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Channel:
    def __init__(self, channel_id: int, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _Message:
    def __init__(self, user_id: int = 1, channel_id: int = 2, parent_id: int | None = None) -> None:
        self.author = _Author(user_id)
        self.channel = _Channel(channel_id, parent_id)


def test_result_cursor_store_and_resolve() -> None:
    state = RuntimeStateStore()
    message = _Message()

    store_result_cursor(message, ["A_1", "A_2"], state_store=state)

    assert resolve_result_cursor(message, 1, state_store=state) == "A_1"
    assert resolve_result_cursor(message, 2, state_store=state) == "A_2"
    assert resolve_result_cursor(message, 3, state_store=state) is None



def test_result_cursor_is_shared_by_parent_channel_and_thread() -> None:
    state = RuntimeStateStore()
    parent_message = _Message(user_id=1, channel_id=2)
    thread_message = _Message(user_id=1, channel_id=3, parent_id=2)

    store_result_cursor(thread_message, ["A_1"], state_store=state)

    assert result_cursor_key(parent_message) == result_cursor_key(thread_message) == 2
    assert resolve_result_cursor(parent_message, 1, state_store=state) == "A_1"


def test_result_cursor_is_replaced_for_conversation_root() -> None:
    state = RuntimeStateStore()
    first_user_message = _Message(user_id=1, channel_id=2)
    second_user_message = _Message(user_id=99, channel_id=2)

    store_result_cursor(first_user_message, ["A_1"], state_store=state)
    store_result_cursor(second_user_message, ["A_2"], state_store=state)

    assert resolve_result_cursor(first_user_message, 1, state_store=state) == "A_2"
