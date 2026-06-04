"""Discord scheduler integration helpers."""

from __future__ import annotations

import asyncio
import heapq
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from squire_core.canonical_store import find_object_path, load_frontmatter
from squire_core.surfacing import (
    DueTimeReminderEvent,
    build_daily_digest,
    build_due_time_reminder_events,
    build_weekly_review,
    render_due_time_reminder_message,
)
from squire_core.timezone_utils import resolve_timezone
from squire_core.transport.bootstrap import (
    next_daily_run,
    next_midnight_run,
    next_weekly_run,
    parse_daily_digest_time,
    parse_weekly_review_day,
)
from squire_core.transport.discord.command_contract import NUMBERED_COMMAND_TIP
from squire_core.transport.reminders import (
    due_time_reminder_key,
    due_time_reminder_ledger_path,
    flush_due_time_reminder_ledger_entries,
    load_due_time_reminder_ledger_entries,
    load_due_time_reminder_schedule_config,
)
from squire_core.transport.state import (
    DueTimeReminderSentLedgerEntry,
    RuntimeStateStore,
    render_numbered_daily_digest,
    render_numbered_weekly_review,
)

_DUE_TIME_REMINDER_LEDGER_RETENTION_HOURS = 48
_DUE_TIME_REMINDER_HORIZON_HOURS = 36
_DUE_TIME_REMINDER_EMPTY_QUEUE_WAIT_SECONDS = 300
_DUE_TIME_REMINDER_ALLOWED_STATUSES = {"open"}


def _coerce_timezone_datetime(value: Any, timezone_hint) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone_hint)
        return value.replace(tzinfo=timezone_hint)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone_hint)
    return parsed.replace(tzinfo=timezone_hint)


class DiscordSchedulerMixin:
    def _init_scheduler_state(
        self,
        config: dict[str, Any],
        *,
        runtime_state: RuntimeStateStore,
    ) -> None:
        self._config = config
        self._runtime_state = runtime_state
        schedule = config.get("schedule", {}) if isinstance(config.get("schedule"), dict) else {}
        self._digest_time = parse_daily_digest_time(schedule.get("daily_digest_time"))
        self._weekly_review_day = parse_weekly_review_day(schedule.get("weekly_review_day"))
        self._weekly_review_time = parse_daily_digest_time(schedule.get("weekly_review_time"))
        self._digest_channel_id = self._coerce_int(schedule.get("daily_digest_channel_id"))
        self._digest_user_id = self._coerce_int(schedule.get("daily_digest_user_id"))
        self._due_time_reminder_schedule = load_due_time_reminder_schedule_config(schedule)
        self._due_time_reminder_ledger_path = due_time_reminder_ledger_path(config)
        self._last_dm_channel_id: int | None = None
        self._last_dm_user_id: int | None = None
        self._timezone = resolve_timezone(config.get("timezone"))
        self._digest_task: asyncio.Task | None = None
        self._weekly_review_task: asyncio.Task | None = None
        self._due_time_reminder_task: asyncio.Task | None = None
        self._due_time_reminder_midnight_task: asyncio.Task | None = None
        self._due_time_reminder_reconcile_task: asyncio.Task | None = None
        self._due_time_reminder_schedule_changed = asyncio.Event()
        self._due_time_reminder_state_lock = asyncio.Lock()
        self._due_time_reminder_heap: list[tuple[datetime, datetime, str, int, DueTimeReminderEvent]] = []
        self._due_time_reminder_sent_ledger: dict[str, DueTimeReminderSentLedgerEntry] = {}
        self._due_time_reminder_reset_requested = False

    def _coerce_int(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed.isdigit():
                return int(trimmed)
        return None

    async def _start_scheduler_tasks(self) -> None:
        if self._digest_time and self._digest_task is None:
            self._digest_task = asyncio.create_task(self._daily_digest_loop())
        if self._weekly_review_day is not None and self._weekly_review_time and self._weekly_review_task is None:
            self._weekly_review_task = asyncio.create_task(self._weekly_review_loop())
        if self._due_time_reminder_schedule.enabled and self._due_time_reminder_task is None:
            self._due_time_reminder_task = asyncio.create_task(self._due_time_reminder_loop())
            self._due_time_reminder_midnight_task = asyncio.create_task(self._due_time_reminder_midnight_loop())
            self._due_time_reminder_reconcile_task = asyncio.create_task(self._due_time_reminder_reconcile_loop())

    def _on_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        if not self._due_time_reminder_schedule.enabled:
            return
        if clear_state:
            self._due_time_reminder_reset_requested = True
        self._due_time_reminder_schedule_changed.set()

    def request_due_time_reminder_schedule_refresh(self, *, clear_state: bool = False) -> None:
        self._on_due_time_reminder_schedule_changed(clear_state=clear_state)

    @staticmethod
    def _due_time_reminder_heap_item(
        event: DueTimeReminderEvent,
    ) -> tuple[datetime, datetime, str, int, DueTimeReminderEvent]:
        return (event.fire_at, event.due_at, event.object_id, event.offset_minutes, event)

    def _prune_due_time_reminder_sent_ledger(self, *, now: datetime) -> bool:
        expired_keys = [key for key, entry in self._due_time_reminder_sent_ledger.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._due_time_reminder_sent_ledger.pop(key, None)
        return bool(expired_keys)

    async def _load_due_time_reminder_sent_ledger(self) -> None:
        now = datetime.now(timezone.utc)
        loaded = await asyncio.to_thread(
            load_due_time_reminder_ledger_entries,
            self._due_time_reminder_ledger_path,
            now=now,
        )
        async with self._due_time_reminder_state_lock:
            self._due_time_reminder_sent_ledger = loaded

    async def _flush_due_time_reminder_sent_ledger(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._due_time_reminder_state_lock:
            entries = dict(self._due_time_reminder_sent_ledger)
        try:
            await asyncio.to_thread(
                flush_due_time_reminder_ledger_entries,
                self._due_time_reminder_ledger_path,
                entries=entries,
                now=now,
            )
        except Exception as exc:
            logging.warning("due_time_reminder_ledger_flush_failed error=%s", exc)

    async def _rebuild_due_time_reminder_schedule(self, *, source: str) -> int:
        now = datetime.now(self._timezone)
        events = await asyncio.to_thread(
            build_due_time_reminder_events,
            self._config.get("paths", {}).get("objects_root", "objects"),
            self._config,
            offsets_minutes=list(self._due_time_reminder_schedule.offsets_minutes),
            now=now,
            late_grace_minutes=self._due_time_reminder_schedule.late_grace_minutes,
            horizon_hours=_DUE_TIME_REMINDER_HORIZON_HOURS,
        )
        heap_items = [self._due_time_reminder_heap_item(event) for event in events]
        heapq.heapify(heap_items)
        async with self._due_time_reminder_state_lock:
            self._due_time_reminder_heap = heap_items
        logging.info(
            "due_time_reminder_schedule_built count=%s horizon_hours=%s source=%s",
            len(events),
            _DUE_TIME_REMINDER_HORIZON_HOURS,
            source,
        )
        return len(events)

    async def _peek_due_time_reminder_fire_at(self) -> datetime | None:
        async with self._due_time_reminder_state_lock:
            if not self._due_time_reminder_heap:
                return None
            return self._due_time_reminder_heap[0][0]

    async def _push_due_time_reminder_events(self, events: list[DueTimeReminderEvent]) -> None:
        if not events:
            return
        async with self._due_time_reminder_state_lock:
            for event in events:
                heapq.heappush(self._due_time_reminder_heap, self._due_time_reminder_heap_item(event))

    async def _pop_due_time_reminder_due_events(self, *, now: datetime) -> list[DueTimeReminderEvent]:
        events: list[DueTimeReminderEvent] = []
        async with self._due_time_reminder_state_lock:
            while self._due_time_reminder_heap and self._due_time_reminder_heap[0][0] <= now:
                events.append(heapq.heappop(self._due_time_reminder_heap)[4])
            self._prune_due_time_reminder_sent_ledger(now=now.astimezone(timezone.utc))
        return events

    async def _resolve_due_time_reminder_channel(self) -> discord.abc.Messageable | None:
        channel_id = self._due_time_reminder_schedule.channel_id
        if channel_id:
            channel = self.get_channel(channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("due_time_reminder_channel_unavailable id=%s", channel_id)
                return None
        user_id = self._due_time_reminder_schedule.user_id
        if user_id:
            user = self.get_user(user_id)
            if not user:
                try:
                    user = await self.fetch_user(user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logging.warning("due_time_reminder_user_unavailable id=%s", user_id)
                    user = None
            if user:
                if user.dm_channel:
                    return user.dm_channel
                try:
                    return await user.create_dm()
                except (discord.HTTPException, discord.Forbidden):
                    logging.warning("due_time_reminder_dm_create_failed user=%s", user_id)
                    return None
        return await self._resolve_digest_channel()

    def _due_time_reminder_is_stale(self, event: DueTimeReminderEvent, *, now: datetime) -> bool:
        grace = timedelta(minutes=self._due_time_reminder_schedule.late_grace_minutes)
        return now - event.fire_at > grace

    def _due_time_reminder_recheck_event(self, event: DueTimeReminderEvent) -> tuple[bool, str | None]:
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        path = find_object_path(objects_root, event.object_id)
        if not path:
            return False, "missing_object"
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            return False, "ineligible"
        status_value = str(frontmatter.get("status") or "").strip().lower()
        if status_value not in _DUE_TIME_REMINDER_ALLOWED_STATUSES:
            return False, "ineligible"
        due_at = _coerce_timezone_datetime(frontmatter.get("due_at"), self._timezone)
        if due_at is None or due_at != event.due_at:
            return False, "ineligible"
        return True, None

    async def _dispatch_due_time_reminders(self) -> None:
        now = datetime.now(self._timezone)
        due_events = await self._pop_due_time_reminder_due_events(now=now)
        if not due_events:
            return

        async with self._due_time_reminder_state_lock:
            active_sent_keys = set(self._due_time_reminder_sent_ledger.keys())

        sendable: list[DueTimeReminderEvent] = []
        for event in due_events:
            if self._due_time_reminder_is_stale(event, now=now):
                logging.info("due_time_reminder_event_skipped reason=stale")
                continue
            key = due_time_reminder_key(event)
            if key in active_sent_keys:
                logging.info("due_time_reminder_event_skipped reason=duplicate")
                continue
            eligible, reason = self._due_time_reminder_recheck_event(event)
            if not eligible:
                logging.info("due_time_reminder_event_skipped reason=%s", reason or "ineligible")
                continue
            sendable.append(event)

        if not sendable:
            return

        channel = await self._resolve_due_time_reminder_channel()
        if not channel:
            logging.warning("due_time_reminder_skipped reason=no_channel")
            await self._push_due_time_reminder_events(sendable)
            return

        content = render_due_time_reminder_message(sendable, self._config, now=now)
        if not content:
            return

        try:
            await channel.send(content=content)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("due_time_reminder_send_failed error=%s", exc)
            await self._push_due_time_reminder_events(sendable)
            return

        sent_at = datetime.now(timezone.utc)
        expires_at = sent_at + timedelta(hours=_DUE_TIME_REMINDER_LEDGER_RETENTION_HOURS)
        async with self._due_time_reminder_state_lock:
            for event in sendable:
                key = due_time_reminder_key(event)
                self._due_time_reminder_sent_ledger[key] = DueTimeReminderSentLedgerEntry(
                    key=key,
                    object_id=event.object_id,
                    due_at=event.due_at.astimezone(timezone.utc),
                    offset_minutes=event.offset_minutes,
                    fire_at=event.fire_at.astimezone(timezone.utc),
                    sent_at=sent_at,
                    expires_at=expires_at,
                )
                logging.info(
                    "due_time_reminder_event_dispatched object_id=%s due_at=%s offset=%s",
                    event.object_id,
                    event.due_at.isoformat(),
                    event.offset_minutes,
                )
            self._prune_due_time_reminder_sent_ledger(now=sent_at)
        await self._flush_due_time_reminder_sent_ledger()

    async def _due_time_reminder_loop(self) -> None:
        try:
            await self._load_due_time_reminder_sent_ledger()
            await self._rebuild_due_time_reminder_schedule(source="startup")
        except Exception:
            logging.exception("due_time_reminder_startup_failed")
        while not self.is_closed():
            if self._due_time_reminder_schedule_changed.is_set():
                self._due_time_reminder_schedule_changed.clear()
                if self._due_time_reminder_reset_requested:
                    async with self._due_time_reminder_state_lock:
                        self._due_time_reminder_heap.clear()
                        self._due_time_reminder_sent_ledger.clear()
                    self._due_time_reminder_reset_requested = False
                    await self._flush_due_time_reminder_sent_ledger()
                try:
                    await self._rebuild_due_time_reminder_schedule(source="event")
                except Exception:
                    logging.exception("due_time_reminder_rebuild_failed source=event")
                continue

            next_fire = await self._peek_due_time_reminder_fire_at()
            if next_fire is None:
                delay = float(_DUE_TIME_REMINDER_EMPTY_QUEUE_WAIT_SECONDS)
            else:
                delay = max(0.0, (next_fire - datetime.now(self._timezone)).total_seconds())
            try:
                await asyncio.wait_for(self._due_time_reminder_schedule_changed.wait(), timeout=delay)
                self._due_time_reminder_schedule_changed.clear()
                logging.info("due_time_reminder_wake reason=schedule_changed")
                if self._due_time_reminder_reset_requested:
                    async with self._due_time_reminder_state_lock:
                        self._due_time_reminder_heap.clear()
                        self._due_time_reminder_sent_ledger.clear()
                    self._due_time_reminder_reset_requested = False
                    await self._flush_due_time_reminder_sent_ledger()
                try:
                    await self._rebuild_due_time_reminder_schedule(source="event")
                except Exception:
                    logging.exception("due_time_reminder_rebuild_failed source=event")
                continue
            except asyncio.TimeoutError:
                logging.info("due_time_reminder_wake reason=timeout")
            try:
                await self._dispatch_due_time_reminders()
            except Exception:
                logging.exception("due_time_reminder_dispatch_failed")

    async def _due_time_reminder_midnight_loop(self) -> None:
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = next_midnight_run(now)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._rebuild_due_time_reminder_schedule(source="midnight")
            except Exception:
                logging.exception("due_time_reminder_rebuild_failed source=midnight")

    async def _due_time_reminder_reconcile_loop(self) -> None:
        interval_seconds = max(1, self._due_time_reminder_schedule.reconcile_minutes) * 60
        while not self.is_closed():
            await asyncio.sleep(interval_seconds)
            try:
                count = await self._rebuild_due_time_reminder_schedule(source="reconcile")
            except Exception:
                logging.exception("due_time_reminder_rebuild_failed source=reconcile")
                continue
            now_utc = datetime.now(timezone.utc)
            async with self._due_time_reminder_state_lock:
                pruned = self._prune_due_time_reminder_sent_ledger(now=now_utc)
            if pruned:
                await self._flush_due_time_reminder_sent_ledger()
            logging.info("due_time_reminder_reconcile_completed count=%s", count)

    async def _resolve_digest_channel(self) -> discord.abc.Messageable | None:
        if self._digest_channel_id:
            channel = self.get_channel(self._digest_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(self._digest_channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("daily_digest_channel_unavailable id=%s", self._digest_channel_id)
                return None
        if self._digest_user_id:
            user = self.get_user(self._digest_user_id)
            if not user:
                try:
                    user = await self.fetch_user(self._digest_user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logging.warning("daily_digest_user_unavailable id=%s", self._digest_user_id)
                    user = None
            if user:
                if user.dm_channel:
                    return user.dm_channel
                try:
                    return await user.create_dm()
                except (discord.HTTPException, discord.Forbidden):
                    logging.warning("daily_digest_dm_create_failed user=%s", self._digest_user_id)
                    return None
        if self._last_dm_channel_id:
            channel = self.get_channel(self._last_dm_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(self._last_dm_channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("daily_digest_last_dm_unavailable id=%s", self._last_dm_channel_id)
                return None
        return None

    def _scheduled_result_cursor_key(self, channel: discord.abc.Messageable) -> int:
        parent_id = getattr(channel, "parent_id", None)
        if isinstance(parent_id, int):
            return parent_id
        return int(getattr(channel, "id", 0))

    async def _send_daily_digest(self) -> None:
        channel = await self._resolve_digest_channel()
        if not channel:
            logging.warning("daily_digest_skipped reason=no_channel")
            return
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        digest = build_daily_digest(objects_root, self._config)
        content, cursor_object_ids = render_numbered_daily_digest(
            digest,
            numbered_command_tip=NUMBERED_COMMAND_TIP,
        )
        try:
            await channel.send(content=content)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("daily_digest_send_failed error=%s", exc)
            return
        self._runtime_state.store_result_cursor(
            self._scheduled_result_cursor_key(channel),
            cursor_object_ids,
            source_view="status",
        )

    async def _daily_digest_loop(self) -> None:
        if not self._digest_time:
            return
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = next_daily_run(now, self._digest_time)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._send_daily_digest()
            except Exception:
                logging.exception("daily_digest_failed")

    async def _send_weekly_review(self) -> None:
        channel = await self._resolve_digest_channel()
        if not channel:
            logging.warning("weekly_review_skipped reason=no_channel")
            return
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        review = build_weekly_review(objects_root, self._config)
        content, cursor_object_ids = render_numbered_weekly_review(
            review,
            numbered_command_tip=NUMBERED_COMMAND_TIP,
        )
        try:
            await channel.send(content=content)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("weekly_review_send_failed error=%s", exc)
            return
        self._runtime_state.store_result_cursor(
            self._scheduled_result_cursor_key(channel),
            cursor_object_ids,
            source_view="weekly",
        )

    async def _weekly_review_loop(self) -> None:
        if self._weekly_review_day is None or not self._weekly_review_time:
            return
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = next_weekly_run(now, self._weekly_review_day, self._weekly_review_time)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._send_weekly_review()
            except Exception:
                logging.exception("weekly_review_failed")
