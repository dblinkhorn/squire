from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from squire_core import runtime as discord_bot
from squire_core.config_utils import normalize_archive_config


def _config_for(root: Path) -> dict[str, object]:
    return {
        "archive_root": str(root),
        "paths": {
            "objects_root": str(root / "objects"),
            "index_db": str(root / "index" / "sb.sqlite"),
        },
    }


def test_run_test_mode_reset_seed_skips_non_test_env(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    config = _config_for(root)

    result = discord_bot._run_test_mode_reset_seed(config, env_value="dev")

    assert result is None
    assert not root.exists()


def test_apply_test_archive_root_override_skips_non_test_env(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    config = {
        "archive_root": str(root),
        "test_archive_root": str(tmp_path / "squire-test-archive"),
        "paths": {"objects_root": "objects"},
    }

    updated = discord_bot._apply_test_archive_root_override(config, env_value="dev")

    assert updated == config


def test_apply_test_archive_root_override_applies_in_test_env(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    config = {
        "archive_root": str(root),
        "test_archive_root": str(tmp_path / "squire-test-archive"),
        "paths": {
            "objects_root": "objects",
            "index_db": str(root / "index" / "sb.sqlite"),
        },
    }

    updated = discord_bot._apply_test_archive_root_override(config, env_value="test")

    assert updated["archive_root"] == str(tmp_path / "squire-test-archive")
    paths = updated.get("paths")
    assert isinstance(paths, dict)
    assert paths.get("objects_root") == "objects"
    assert "index_db" not in paths


def test_run_test_mode_reset_seed_rejects_unsafe_archive_root() -> None:
    root = Path.home() / "squire-ci-unsafe-archive"
    config = _config_for(root)

    with pytest.raises(ValueError, match="test-safe"):
        discord_bot._run_test_mode_reset_seed(config, env_value="test")


def test_run_test_mode_reset_seed_accepts_test_archive_root_override(tmp_path: Path) -> None:
    base_root = tmp_path / "archive"
    test_root = tmp_path / "squire-test-archive"
    config = {
        "archive_root": str(base_root),
        "test_archive_root": str(test_root),
        "paths": {
            "events_raw": "events/raw",
            "events_derived": "events/derived",
            "pending_actions": "events/pending",
            "objects_root": "objects",
            "index_db": "index/sb.sqlite",
        },
    }

    overridden = discord_bot._apply_test_archive_root_override(config, env_value="test")
    normalized = normalize_archive_config(overridden)
    stats = discord_bot._run_test_mode_reset_seed(
        normalized,
        env_value="test",
        now=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert stats is not None
    assert (test_root / "objects").exists()
    assert not base_root.exists()


def test_run_test_mode_reset_seed_runs_reset_seed_and_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "squire-test-startup"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "old-file.txt").write_text("remove-me\n", encoding="utf-8")
    config = _config_for(root)

    stats = discord_bot._run_test_mode_reset_seed(
        config,
        env_value="test",
        now=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert stats is not None
    assert stats.admin_count == 7
    assert stats.projects_count == 3
    assert stats.people_count == 2
    assert stats.ideas_count == 2
    assert (root / ".git").exists()
    assert not (root / "old-file.txt").exists()

    index_path = root / "index" / "sb.sqlite"
    assert index_path.exists()
    conn = sqlite3.connect(index_path)
    try:
        object_count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()
    finally:
        conn.close()
    assert object_count is not None
    assert int(object_count[0]) == 14
