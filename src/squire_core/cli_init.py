from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

import yaml


def _default_archive_root() -> Path:
    return Path.home() / "squire-archive"


def _load_config(path: str | Path) -> dict[str, Any]:
    from squire_core.config_utils import load_config

    return load_config(path)


def _write_config(path: str | Path, config: dict[str, Any]) -> None:
    config_path = Path(path)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)


def _ensure_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if (root / ".git").exists():
        return
    subprocess.run(["git", "init"], cwd=root, check=True)


def _apply_archive_paths(config: dict[str, Any], archive_root: Path) -> dict[str, Any]:
    config["archive_root"] = str(archive_root)
    paths = config.setdefault("paths", {})
    paths["events_raw"] = str(archive_root / "events" / "raw")
    paths["events_derived"] = str(archive_root / "events" / "derived")
    paths["objects_root"] = str(archive_root / "objects")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Squire archive storage")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--archive-root",
        default="",
        help="Override archive root (default: ~/squire-archive)",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Skip git initialization (useful for local dev/testing)",
    )
    args = parser.parse_args()

    config = _load_config(args.config)
    archive_value = args.archive_root or config.get("archive_root", "")
    archive_root = Path(archive_value).expanduser() if archive_value else _default_archive_root()
    if not archive_root.is_absolute():
        raise SystemExit("archive_root must be an absolute path. Use ~/... or an абсолюте path.")

    git_enabled = config.get("archive_git_enabled", True)
    if args.no_git:
        git_enabled = False

    if git_enabled:
        _ensure_git_repo(archive_root)
    config = _apply_archive_paths(config, archive_root)
    _write_config(args.config, config)

    print(f"Archive initialized at {archive_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
