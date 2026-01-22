from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ARCHIVE_PATH_KEYS = ("events_raw", "events_derived", "objects_root", "pending_actions", "index_db")
_DEFAULT_ARCHIVE_SUBPATHS = {
    "events_raw": Path("events") / "raw",
    "events_derived": Path("events") / "derived",
    "objects_root": Path("objects"),
    "pending_actions": Path("events") / "pending",
    "index_db": Path("index") / "sb.sqlite",
}
_DEFAULT_DECISION_CONFIG = {
    "auto_apply_threshold": 0.85,
    "confirm_threshold": 0.65,
    "candidate_limit": 3,
    "candidate_score_threshold": 0.2,
}


@dataclass(frozen=True)
class DecisionConfig:
    auto_apply_threshold: float
    confirm_threshold: float
    candidate_limit: int
    candidate_score_threshold: float


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


def load_decision_config(config: dict[str, Any]) -> DecisionConfig:
    raw = config.get("decision")
    if not isinstance(raw, dict):
        raw = {}

    def _get_float(key: str) -> float:
        value = raw.get(key, _DEFAULT_DECISION_CONFIG[key])
        if isinstance(value, (int, float)):
            return float(value)
        return float(_DEFAULT_DECISION_CONFIG[key])

    def _get_int(key: str) -> int:
        value = raw.get(key, _DEFAULT_DECISION_CONFIG[key])
        if isinstance(value, (int, float)):
            return int(value)
        return int(_DEFAULT_DECISION_CONFIG[key])

    return DecisionConfig(
        auto_apply_threshold=_get_float("auto_apply_threshold"),
        confirm_threshold=_get_float("confirm_threshold"),
        candidate_limit=_get_int("candidate_limit"),
        candidate_score_threshold=_get_float("candidate_score_threshold"),
    )
