"""Runtime bootstrap helpers for shared transport startup behavior."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from squire_core.indexer import rebuild_index
from squire_core.test_seed import SeedStats, ensure_test_safe_archive_root, seed_test_canonical_objects

_WEEKDAY_MAP = {
    "MON": 0,
    "MONDAY": 0,
    "TUE": 1,
    "TUESDAY": 1,
    "WED": 2,
    "WEDNESDAY": 2,
    "THU": 3,
    "THURSDAY": 3,
    "FRI": 4,
    "FRIDAY": 4,
    "SAT": 5,
    "SATURDAY": 5,
    "SUN": 6,
    "SUNDAY": 6,
}


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self._max_level


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_MaxLevelFilter(logging.ERROR))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)


def parse_daily_digest_time(value: Any) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        value = str(value)
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def next_daily_run(now: datetime, target: time) -> datetime:
    candidate = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def parse_weekly_review_day(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        return None
    label = str(value).strip().upper()
    return _WEEKDAY_MAP.get(label)


def next_weekly_run(now: datetime, target_day: int, target_time: time) -> datetime:
    days_ahead = (target_day - now.weekday()) % 7
    candidate = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    candidate = candidate + timedelta(days=days_ahead)
    if candidate <= now:
        candidate = candidate + timedelta(days=7)
    return candidate


def next_midnight_run(now: datetime) -> datetime:
    candidate = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def clear_archive_contents(archive_root: str | Path) -> int:
    root = Path(archive_root).expanduser()
    if not root.exists():
        raise ValueError(f"archive_root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"archive_root is not a directory: {root}")

    removed = 0
    for child in root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return removed


def run_test_mode_reset_seed(
    config: dict[str, Any],
    *,
    env_value: str | None = None,
    now: datetime | None = None,
) -> SeedStats | None:
    raw_env = env_value if env_value is not None else os.getenv("SQUIRE_ENV")
    if str(raw_env or "").strip().lower() != "test":
        return None

    archive_root = config.get("archive_root")
    if not isinstance(archive_root, str) or not archive_root.strip():
        raise ValueError("archive_root is not configured.")

    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("paths are not configured.")
    objects_root = paths.get("objects_root")
    index_db = paths.get("index_db")
    if not isinstance(objects_root, str) or not objects_root.strip():
        raise ValueError("paths.objects_root is not configured.")
    if not isinstance(index_db, str) or not index_db.strip():
        raise ValueError("paths.index_db is not configured.")

    root = ensure_test_safe_archive_root(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    logging.info("test_mode_startup_enabled archive_root=%s", root)

    removed = clear_archive_contents(root)
    logging.info("test_mode_reset_completed removed_entries=%s", removed)

    stats = seed_test_canonical_objects(
        objects_root=objects_root,
        schema_path=Path("config/schemas/canonical_object_v1.json"),
        now=now,
    )
    logging.info(
        "test_mode_seed_completed admin=%s projects=%s people=%s ideas=%s",
        stats.admin_count,
        stats.projects_count,
        stats.people_count,
        stats.ideas_count,
    )

    rebuild_index(objects_root, index_db)
    logging.info("test_mode_rebuild_index_completed")
    return stats


def apply_test_archive_root_override(
    config: dict[str, Any],
    *,
    env_value: str | None = None,
) -> dict[str, Any]:
    raw_env = env_value if env_value is not None else os.getenv("SQUIRE_ENV")
    if str(raw_env or "").strip().lower() != "test":
        return config

    override = config.get("test_archive_root")
    if not isinstance(override, str) or not override.strip():
        return config

    updated = dict(config)
    updated["archive_root"] = override.strip()

    paths = config.get("paths")
    if isinstance(paths, dict):
        relative_paths: dict[str, Any] = {}
        for key, value in paths.items():
            candidate = Path(str(value)).expanduser()
            if not candidate.is_absolute():
                relative_paths[str(key)] = value
        if relative_paths:
            updated["paths"] = relative_paths
        else:
            updated.pop("paths", None)

    logging.info(
        "test_mode_archive_root_override enabled=true archive_root=%s test_archive_root=%s",
        config.get("archive_root"),
        override.strip(),
    )
    return updated
