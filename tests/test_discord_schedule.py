from __future__ import annotations

import asyncio
from datetime import timedelta
from datetime import datetime, time, timezone
from types import SimpleNamespace

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


def test_next_midnight_run_rolls_forward() -> None:
    now = datetime(2026, 1, 22, 23, 45, tzinfo=timezone.utc)
    target = discord_bot._next_midnight_run(now)
    assert target == datetime(2026, 1, 23, 0, 0, tzinfo=timezone.utc)


def test_parse_due_time_reminder_offsets() -> None:
    assert discord_bot._parse_due_time_reminder_offsets([120, 15, 120, "15", "abc", -1, 0]) == (120, 15)
    assert discord_bot._parse_due_time_reminder_offsets(None) == ()
    assert discord_bot._parse_due_time_reminder_offsets("120") == ()


def test_parse_due_time_reminder_offsets_ignores_booleans() -> None:
    assert discord_bot._parse_due_time_reminder_offsets([True, False, 15]) == (15,)


def test_load_due_time_reminder_schedule_config_defaults() -> None:
    schedule = discord_bot._load_due_time_reminder_schedule_config({})
    assert schedule.offsets_minutes == (90, 15)
    assert schedule.late_grace_minutes == 10
    assert schedule.reconcile_minutes == 60
    assert schedule.enabled is True


def test_load_due_time_reminder_schedule_config_explicit_empty_disables() -> None:
    schedule = discord_bot._load_due_time_reminder_schedule_config({"due_time_reminder_offsets_minutes": []})
    assert schedule.offsets_minutes == ()
    assert schedule.enabled is False


def test_notify_due_time_reminder_schedule_changed_calls_runtime_callback() -> None:
    calls: list[str] = []

    def _callback(*, clear_state: bool = False) -> None:
        calls.append(f"clear:{clear_state}")

    config = {discord_bot._DUE_TIME_REMINDER_NOTIFY_CONFIG_KEY: _callback}
    discord_bot._notify_due_time_reminder_schedule_changed(config)
    discord_bot._notify_due_time_reminder_schedule_changed(config, clear_state=True)

    assert calls == ["clear:False", "clear:True"]


def test_due_time_reminder_dispatch_requeues_on_send_failure(monkeypatch) -> None:
    async def _run() -> None:
        config = {
            "timezone": "UTC",
            "paths": {"objects_root": "/tmp/objects", "events_derived": "/tmp/events/derived"},
            "schedule": {"due_time_reminder_offsets_minutes": [15]},
        }
        bot = discord_bot.SquireBot(config=config)
        now = datetime.now(timezone.utc)
        event = discord_bot.DueTimeReminderEvent(
            object_id="A_1",
            title="Call vet",
            due_at=now + timedelta(minutes=15),
            offset_minutes=15,
            fire_at=now - timedelta(seconds=1),
        )
        bot._due_time_reminder_heap = [bot._due_time_reminder_heap_item(event)]

        async def _fake_resolve_channel():
            class _Channel:
                async def send(self, content):
                    raise discord_bot.discord.HTTPException(SimpleNamespace(status=500, reason="failed"), "failed")

            return _Channel()

        monkeypatch.setattr(bot, "_resolve_due_time_reminder_channel", _fake_resolve_channel)
        monkeypatch.setattr(bot, "_due_time_reminder_recheck_event", lambda event: (True, None))
        monkeypatch.setattr(discord_bot, "render_due_time_reminder_message", lambda *args, **kwargs: "msg")

        await bot._dispatch_due_time_reminders()

        assert len(bot._due_time_reminder_heap) == 1

    asyncio.run(_run())


def test_due_time_reminder_loop_flushes_empty_ledger_on_clear_state(monkeypatch) -> None:
    async def _run() -> None:
        config = {
            "timezone": "UTC",
            "paths": {"objects_root": "/tmp/objects", "events_derived": "/tmp/events/derived"},
            "schedule": {"due_time_reminder_offsets_minutes": [15]},
        }
        bot = discord_bot.SquireBot(config=config)
        bot._due_time_reminder_sent_ledger["old"] = discord_bot._DueTimeReminderSentLedgerEntry(
            key="old",
            object_id="A_1",
            due_at=datetime(2026, 1, 22, 13, 0, tzinfo=timezone.utc),
            offset_minutes=15,
            fire_at=datetime(2026, 1, 22, 12, 45, tzinfo=timezone.utc),
            sent_at=datetime(2026, 1, 22, 12, 45, tzinfo=timezone.utc),
            expires_at=datetime(2026, 1, 24, 12, 45, tzinfo=timezone.utc),
        )

        flushed: list[int] = []
        stop_after_flush = asyncio.Event()

        async def _fake_load_ledger():
            return None

        async def _fake_rebuild(*, source):
            return 0

        async def _fake_flush():
            flushed.append(len(bot._due_time_reminder_sent_ledger))
            stop_after_flush.set()

        monkeypatch.setattr(bot, "_load_due_time_reminder_sent_ledger", _fake_load_ledger)
        monkeypatch.setattr(bot, "_rebuild_due_time_reminder_schedule", _fake_rebuild)
        monkeypatch.setattr(bot, "_flush_due_time_reminder_sent_ledger", _fake_flush)

        bot._due_time_reminder_reset_requested = True
        bot._due_time_reminder_schedule_changed.set()

        task = asyncio.create_task(bot._due_time_reminder_loop())
        await asyncio.wait_for(stop_after_flush.wait(), timeout=1.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert flushed and flushed[0] == 0
        assert bot._due_time_reminder_sent_ledger == {}

    asyncio.run(_run())


def test_due_time_reminder_dispatch_requeues_when_no_channel(monkeypatch) -> None:
    async def _run() -> None:
        config = {
            "timezone": "UTC",
            "paths": {"objects_root": "/tmp/objects", "events_derived": "/tmp/events/derived"},
            "schedule": {"due_time_reminder_offsets_minutes": [15]},
        }
        bot = discord_bot.SquireBot(config=config)
        now = datetime.now(timezone.utc)
        event = discord_bot.DueTimeReminderEvent(
            object_id="A_1",
            title="Call vet",
            due_at=now + timedelta(minutes=15),
            offset_minutes=15,
            fire_at=now - timedelta(seconds=1),
        )
        bot._due_time_reminder_heap = [bot._due_time_reminder_heap_item(event)]

        async def _fake_resolve_channel():
            return None

        monkeypatch.setattr(bot, "_resolve_due_time_reminder_channel", _fake_resolve_channel)
        monkeypatch.setattr(bot, "_due_time_reminder_recheck_event", lambda event: (True, None))
        monkeypatch.setattr(discord_bot, "render_due_time_reminder_message", lambda *args, **kwargs: "msg")

        await bot._dispatch_due_time_reminders()

        assert len(bot._due_time_reminder_heap) == 1

    asyncio.run(_run())


def test_due_time_reminder_dispatch_skips_duplicate_from_sent_ledger(monkeypatch) -> None:
    async def _run() -> None:
        config = {
            "timezone": "UTC",
            "paths": {"objects_root": "/tmp/objects", "events_derived": "/tmp/events/derived"},
            "schedule": {"due_time_reminder_offsets_minutes": [15]},
        }
        bot = discord_bot.SquireBot(config=config)
        now = datetime.now(timezone.utc)
        event = discord_bot.DueTimeReminderEvent(
            object_id="A_1",
            title="Call vet",
            due_at=now + timedelta(minutes=15),
            offset_minutes=15,
            fire_at=now - timedelta(seconds=1),
        )
        bot._due_time_reminder_heap = [bot._due_time_reminder_heap_item(event)]

        duplicate_key = discord_bot._due_time_reminder_key(event)
        bot._due_time_reminder_sent_ledger[duplicate_key] = discord_bot._DueTimeReminderSentLedgerEntry(
            key=duplicate_key,
            object_id=event.object_id,
            due_at=event.due_at.astimezone(timezone.utc),
            offset_minutes=event.offset_minutes,
            fire_at=event.fire_at.astimezone(timezone.utc),
            sent_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )

        async def _unexpected_resolve_channel():
            raise AssertionError("channel should not resolve for duplicate reminders")

        def _unexpected_recheck(_event):
            raise AssertionError("eligibility recheck should not run for duplicate reminders")

        monkeypatch.setattr(bot, "_resolve_due_time_reminder_channel", _unexpected_resolve_channel)
        monkeypatch.setattr(bot, "_due_time_reminder_recheck_event", _unexpected_recheck)

        await bot._dispatch_due_time_reminders()

        assert len(bot._due_time_reminder_heap) == 0
        assert duplicate_key in bot._due_time_reminder_sent_ledger

    asyncio.run(_run())


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
