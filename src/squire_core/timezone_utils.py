from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def resolve_timezone(tz_name: str | None) -> ZoneInfo:
    if not tz_name or tz_name == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc

    return ZoneInfo(tz_name)


def format_reference_time(tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


def format_reference_date(tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d")


def format_reference_weekday(tz: ZoneInfo) -> str:
    return datetime.now(tz).strftime("%A")
