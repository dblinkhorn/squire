"""Shared due-time reminder scheduling helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any

from squire_core.surfacing import DueTimeReminderEvent
from squire_core.transport.state import DueTimeReminderScheduleConfig, DueTimeReminderSentLedgerEntry

DUE_TIME_REMINDER_NOTIFY_CONFIG_KEY = "_due_time_reminder_notify"
DUE_TIME_REMINDER_LEDGER_FILENAME = "due_time_reminder_sent_ledger_v1.json"
DUE_TIME_REMINDER_DEFAULT_OFFSETS_MINUTES = (90, 15)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            return int(trimmed)
    return None


def _parse_non_negative_int(value: Any, fallback: int) -> int:
    parsed = _coerce_int(value)
    if parsed is None or parsed < 0:
        return fallback
    return parsed


def _parse_minimum_int(value: Any, *, fallback: int, minimum: int) -> int:
    parsed = _coerce_int(value)
    if parsed is None or parsed < minimum:
        return fallback
    return parsed


def parse_due_time_reminder_offsets(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    offsets: set[int] = set()
    for item in value:
        parsed: int | None = None
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            parsed = item
        elif isinstance(item, float) and item.is_integer():
            parsed = int(item)
        elif isinstance(item, str):
            trimmed = item.strip()
            if trimmed.isdigit():
                parsed = int(trimmed)
        if parsed is None or parsed <= 0:
            continue
        offsets.add(parsed)
    return tuple(sorted(offsets, reverse=True))


def load_due_time_reminder_schedule_config(schedule: dict[str, Any]) -> DueTimeReminderScheduleConfig:
    if "due_time_reminder_offsets_minutes" in schedule:
        offsets = parse_due_time_reminder_offsets(schedule.get("due_time_reminder_offsets_minutes"))
    else:
        offsets = DUE_TIME_REMINDER_DEFAULT_OFFSETS_MINUTES
    return DueTimeReminderScheduleConfig(
        offsets_minutes=offsets,
        late_grace_minutes=_parse_non_negative_int(schedule.get("due_time_reminder_late_grace_minutes"), 10),
        reconcile_minutes=_parse_minimum_int(
            schedule.get("due_time_reminder_reconcile_minutes"),
            fallback=60,
            minimum=1,
        ),
        channel_id=_coerce_int(schedule.get("due_time_reminder_channel_id")),
        user_id=_coerce_int(schedule.get("due_time_reminder_user_id")),
    )


def due_time_reminder_key(event: DueTimeReminderEvent) -> str:
    return f"{event.object_id}|{event.due_at.isoformat()}|{event.offset_minutes}"


def due_time_reminder_ledger_path(config: dict[str, Any]) -> Path:
    events_derived = config.get("paths", {}).get("events_derived", "events/derived")
    return Path(str(events_derived)) / "runtime" / DUE_TIME_REMINDER_LEDGER_FILENAME


def _coerce_timezone_datetime(value: Any, tz: tzinfo) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            dt = datetime.fromisoformat(trimmed)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def serialize_due_time_reminder_ledger_entries(
    entries: dict[str, DueTimeReminderSentLedgerEntry],
    *,
    now: datetime,
) -> dict[str, Any]:
    payload_entries = []
    for key in sorted(entries):
        entry = entries[key]
        payload_entries.append(
            {
                "key": entry.key,
                "object_id": entry.object_id,
                "due_at": entry.due_at.isoformat(),
                "offset_minutes": entry.offset_minutes,
                "fire_at": entry.fire_at.isoformat(),
                "sent_at": entry.sent_at.isoformat(),
                "expires_at": entry.expires_at.isoformat(),
            }
        )
    return {
        "schema_version": 1,
        "updated_at": now.isoformat(),
        "entries": payload_entries,
    }


def load_due_time_reminder_ledger_entries(
    path: Path,
    *,
    now: datetime,
) -> dict[str, DueTimeReminderSentLedgerEntry]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("due_time_reminder_ledger_load_failed error=%s", exc)
        return {}
    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list):
        return {}
    entries: dict[str, DueTimeReminderSentLedgerEntry] = {}
    for raw_entry in entries_raw:
        if not isinstance(raw_entry, dict):
            continue
        key = raw_entry.get("key")
        object_id = raw_entry.get("object_id")
        due_at_raw = raw_entry.get("due_at")
        fire_at_raw = raw_entry.get("fire_at")
        sent_at_raw = raw_entry.get("sent_at")
        expires_at_raw = raw_entry.get("expires_at")
        offset_raw = raw_entry.get("offset_minutes")
        if not isinstance(key, str) or not isinstance(object_id, str):
            continue
        due_at = _coerce_timezone_datetime(due_at_raw, timezone.utc)
        fire_at = _coerce_timezone_datetime(fire_at_raw, timezone.utc)
        sent_at = _coerce_timezone_datetime(sent_at_raw, timezone.utc)
        expires_at = _coerce_timezone_datetime(expires_at_raw, timezone.utc)
        offset = _coerce_int(offset_raw)
        if due_at is None or fire_at is None or sent_at is None or expires_at is None or offset is None:
            continue
        if expires_at <= now:
            continue
        entries[key] = DueTimeReminderSentLedgerEntry(
            key=key,
            object_id=object_id,
            due_at=due_at,
            offset_minutes=offset,
            fire_at=fire_at,
            sent_at=sent_at,
            expires_at=expires_at,
        )
    return entries


def flush_due_time_reminder_ledger_entries(
    path: Path,
    *,
    entries: dict[str, DueTimeReminderSentLedgerEntry],
    now: datetime,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_due_time_reminder_ledger_entries(entries, now=now)
    tmp_path = path.parent / f"{path.name}.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    tmp_path.replace(path)


def notify_due_time_reminder_schedule_changed(config: dict[str, Any], *, clear_state: bool = False) -> None:
    callback = config.get(DUE_TIME_REMINDER_NOTIFY_CONFIG_KEY)
    if not callable(callback):
        return
    try:
        callback(clear_state=clear_state)
    except TypeError:
        callback()
    except Exception as exc:
        logging.warning("due_time_reminder_schedule_notify_failed error=%s", exc)
