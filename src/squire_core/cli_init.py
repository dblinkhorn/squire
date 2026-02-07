from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Sequence

import yaml

from squire_core.config_utils import load_config, normalize_archive_config


def _default_archive_root() -> str:
    return str((Path.home() / "squire-archive").expanduser())


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize Squire archive storage and config paths.")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml).",
    )
    parser.add_argument(
        "--archive-root",
        default=None,
        help=f'Archive root directory (default: "{_default_archive_root()}").',
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Skip git repository initialization under archive_root.",
    )
    return parser.parse_args(argv)


def _ensure_archive_paths(config: dict[str, object]) -> None:
    paths = config.get("paths")
    if not isinstance(paths, dict):
        return
    for key, value in paths.items():
        if not isinstance(value, str):
            continue
        path = Path(value)
        if key == "index_db":
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)


def _ensure_git_repo(archive_root: Path) -> None:
    git_dir = archive_root / ".git"
    if git_dir.exists():
        return
    try:
        subprocess.run(["git", "init", str(archive_root)], check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed; re-run with --no-git or install git.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"git init failed for {archive_root}: {stderr or exc}") from exc


def _write_config(path: Path, config: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)


def initialize_config(
    *,
    config_path: str | Path = "config.yaml",
    archive_root: str | Path | None = None,
    git_enabled: bool = True,
) -> dict[str, object]:
    path = Path(config_path)
    config = load_config(path)

    if archive_root is None:
        configured = config.get("archive_root")
        archive_root = configured if isinstance(configured, str) and configured.strip() else _default_archive_root()

    root = Path(str(archive_root)).expanduser()
    config["archive_root"] = str(root)
    config["archive_git_enabled"] = bool(git_enabled)

    normalized = normalize_archive_config(config)
    _ensure_archive_paths(normalized)
    archive_root_path = Path(str(normalized["archive_root"]))
    archive_root_path.mkdir(parents=True, exist_ok=True)
    if git_enabled:
        _ensure_git_repo(archive_root_path)
    _write_config(path, normalized)
    return normalized


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    archive_root = args.archive_root if args.archive_root else None
    try:
        normalized = initialize_config(
            config_path=args.config,
            archive_root=archive_root,
            git_enabled=not args.no_git,
        )
    except Exception as exc:
        raise SystemExit(str(exc)) from exc

    print(f'Initialized archive at: {normalized["archive_root"]}')
    print(f'Updated config: {Path(args.config).resolve()}')
    paths = normalized.get("paths")
    if isinstance(paths, dict):
        for key in ("events_raw", "events_derived", "pending_actions", "objects_root", "index_db"):
            value = paths.get(key)
            if isinstance(value, str):
                print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
