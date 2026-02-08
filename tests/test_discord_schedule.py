from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone

from squire_core import discord_bot


def test_parse_weekly_review_day() -> None:
    assert discord_bot._parse_weekly_review_day("SUN") == 6
    assert discord_bot._parse_weekly_review_day("monday") == 0
    assert discord_bot._parse_weekly_review_day(3) == 3
    assert discord_bot._parse_weekly_review_day("nope") is None
    assert discord_bot._parse_weekly_review_day(8) is None


def test_next_weekly_run_future_weekday() -> None:
    now = datetime(2026, 1, 22, 9, 0, tzinfo=timezone.utc)  # Thursday
    target = discord_bot._next_weekly_run(now, 6, time(10, 0))  # Sunday
    assert target == datetime(2026, 1, 25, 10, 0, tzinfo=timezone.utc)


def test_next_weekly_run_rolls_to_next_week_when_time_passed() -> None:
    now = datetime(2026, 1, 25, 11, 0, tzinfo=timezone.utc)  # Sunday
    target = discord_bot._next_weekly_run(now, 6, time(10, 0))  # Sunday
    assert target == datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)


def test_handle_command_weekly(monkeypatch) -> None:
    calls: list[str] = []

    class _Review:
        def render(self) -> str:
            return "Weekly review test output"

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    def _fake_build_weekly_review(objects_root, config):
        calls.append(f"build:{objects_root}")
        return _Review()

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_weekly_review", _fake_build_weekly_review)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(object(), "!weekly", "R_1", config))

    assert handled is True
    assert "build:/tmp/objects" in calls
    assert "swap:⏳:✅" in calls
    assert "send:Weekly review test output" in calls
