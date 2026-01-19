from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ARCHIVE_PATH_KEYS = ("events_raw", "events_derived", "objects_root")
_DEFAULT_ARCHIVE_SUBPATHS = {
    "events_raw": Path("events") / "raw",
    "events_derived": Path("events") / "derived",
    "objects_root": Path("objects"),
}


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize_archive_config(config: dict[str, Any]) -> dict[str, Any]:
    archive_root_value = config.get("archive_root")
    if not archive_root_value:
        raise ValueError("archive_root is required. Run squire init or set archive_root in config.yaml.")

    archive_root = Path(str(archive_root_value)).expanduser()
    if not archive_root.is_absolute():
        raise ValueError("archive_root must be an absolute path.")

    paths = config.setdefault("paths", {})
    for key in _ARCHIVE_PATH_KEYS:
        value = paths.get(key)
        if not value:
            value = _DEFAULT_ARCHIVE_SUBPATHS[key]
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = archive_root / path
        path_resolved = path.resolve()
        root_resolved = archive_root.resolve()
        if not path_resolved.is_relative_to(root_resolved):
            raise ValueError(f"{key} must be inside archive_root.")
        paths[key] = str(path)

    config["archive_root"] = str(archive_root)
    return config
