from __future__ import annotations

from pathlib import Path

import pytest

from squire_core.cli_init import initialize_config
from squire_core.config_utils import load_config


def test_initialize_config_writes_paths_and_directories(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    archive_root = tmp_path / "archive"

    normalized = initialize_config(
        config_path=config_path,
        archive_root=archive_root,
        git_enabled=False,
    )

    loaded = load_config(config_path)
    assert loaded["archive_root"] == str(archive_root)
    assert loaded["archive_git_enabled"] is False
    assert normalized["archive_root"] == str(archive_root)

    paths = loaded["paths"]
    assert isinstance(paths, dict)
    for key in ("events_raw", "events_derived", "pending_actions", "objects_root", "index_db"):
        assert key in paths
        value = Path(str(paths[key]))
        assert value.is_absolute()
        if key == "index_db":
            assert value.parent.exists()
        else:
            assert value.exists()

    assert not (archive_root / ".git").exists()


def test_initialize_config_rejects_relative_archive_root(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    with pytest.raises(ValueError, match="archive_root must be an absolute path"):
        initialize_config(
            config_path=config_path,
            archive_root="relative/path",
            git_enabled=False,
        )
