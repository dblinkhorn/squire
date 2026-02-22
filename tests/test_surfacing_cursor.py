from __future__ import annotations

from datetime import datetime, timedelta, timezone

from squire_core import runtime as discord_bot


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
    discord_bot._RESULT_CURSORS.clear()
    message = _Message()

    config = {"surfacing": {"pull": {"cursor_ttl_minutes": 45}}}
    discord_bot._store_result_cursor(message, config, ["A_1", "A_2"])

    assert discord_bot._resolve_result_cursor(message, 1) == "A_1"
    assert discord_bot._resolve_result_cursor(message, 2) == "A_2"
    assert discord_bot._resolve_result_cursor(message, 3) is None



def test_result_cursor_expires() -> None:
    discord_bot._RESULT_CURSORS.clear()
    message = _Message()
    key = discord_bot._cursor_key(message)

    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_1"],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert discord_bot._resolve_result_cursor(message, 1) is None
    assert key not in discord_bot._RESULT_CURSORS
