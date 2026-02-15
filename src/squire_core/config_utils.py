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
_DEFAULT_MATCHING_CONFIG = {
    "lexical_weight": 1.0,
    "recency_weight": 0.15,
    "affinity_weight": 0.25,
    "semantic_weight": 0.15,
    "semantic_provider": "openai",
    "semantic_model": "text-embedding-3-small",
    "candidate_multiplier": 4,
    "max_candidate_pool": 20,
    "affinity_recent_ids_per_thread": 20,
    "affinity_ttl_days": 7,
    "affinity_max_boost": 0.15,
    "auto_min_score": 0.55,
    "auto_min_margin": 0.20,
    "candidate_limit": 5,
    "semantic_text_schema_version": 1,
}


@dataclass(frozen=True)
class DecisionConfig:
    auto_apply_threshold: float
    confirm_threshold: float
    candidate_limit: int
    candidate_score_threshold: float
    auto_min_score: float
    auto_min_margin: float


@dataclass(frozen=True)
class MatchingConfig:
    lexical_weight: float
    recency_weight: float
    affinity_weight: float
    semantic_weight: float
    semantic_provider: str
    semantic_model: str
    candidate_multiplier: int
    max_candidate_pool: int
    affinity_recent_ids_per_thread: int
    affinity_ttl_days: int
    affinity_max_boost: float
    auto_min_score: float
    auto_min_margin: float
    candidate_limit: int
    semantic_text_schema_version: int


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
    matching = config.get("matching")
    if not isinstance(matching, dict):
        matching = {}

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

    def _get_matching_float(key: str) -> float:
        value = matching.get(key, _DEFAULT_MATCHING_CONFIG[key])
        if isinstance(value, (int, float)):
            return float(value)
        return float(_DEFAULT_MATCHING_CONFIG[key])

    return DecisionConfig(
        auto_apply_threshold=_get_float("auto_apply_threshold"),
        confirm_threshold=_get_float("confirm_threshold"),
        candidate_limit=_get_int("candidate_limit"),
        candidate_score_threshold=_get_float("candidate_score_threshold"),
        auto_min_score=max(0.0, min(1.0, _get_matching_float("auto_min_score"))),
        auto_min_margin=max(0.0, min(1.0, _get_matching_float("auto_min_margin"))),
    )


def load_matching_config(config: dict[str, Any]) -> MatchingConfig:
    raw = config.get("matching")
    if not isinstance(raw, dict):
        raw = {}
    decision = config.get("decision")
    if not isinstance(decision, dict):
        decision = {}

    def _get_float(key: str) -> float:
        value = raw.get(key, _DEFAULT_MATCHING_CONFIG[key])
        if isinstance(value, (int, float)):
            return float(value)
        return float(_DEFAULT_MATCHING_CONFIG[key])

    def _get_int(key: str) -> int:
        value = raw.get(key, _DEFAULT_MATCHING_CONFIG[key])
        if isinstance(value, (int, float)):
            return int(value)
        return int(_DEFAULT_MATCHING_CONFIG[key])

    candidate_limit = raw.get("candidate_limit", decision.get("candidate_limit", _DEFAULT_MATCHING_CONFIG["candidate_limit"]))
    if not isinstance(candidate_limit, (int, float)):
        candidate_limit = _DEFAULT_MATCHING_CONFIG["candidate_limit"]

    semantic_provider = str(raw.get("semantic_provider", _DEFAULT_MATCHING_CONFIG["semantic_provider"])).strip().lower()
    if not semantic_provider:
        semantic_provider = str(_DEFAULT_MATCHING_CONFIG["semantic_provider"])

    semantic_model = str(raw.get("semantic_model", _DEFAULT_MATCHING_CONFIG["semantic_model"])).strip()
    if not semantic_model:
        semantic_model = str(_DEFAULT_MATCHING_CONFIG["semantic_model"])

    return MatchingConfig(
        lexical_weight=_get_float("lexical_weight"),
        recency_weight=_get_float("recency_weight"),
        affinity_weight=_get_float("affinity_weight"),
        semantic_weight=_get_float("semantic_weight"),
        semantic_provider=semantic_provider,
        semantic_model=semantic_model,
        candidate_multiplier=max(1, _get_int("candidate_multiplier")),
        max_candidate_pool=max(1, _get_int("max_candidate_pool")),
        affinity_recent_ids_per_thread=max(1, _get_int("affinity_recent_ids_per_thread")),
        affinity_ttl_days=max(1, _get_int("affinity_ttl_days")),
        affinity_max_boost=max(0.0, min(1.0, _get_float("affinity_max_boost"))),
        auto_min_score=max(0.0, min(1.0, _get_float("auto_min_score"))),
        auto_min_margin=max(0.0, min(1.0, _get_float("auto_min_margin"))),
        candidate_limit=max(1, int(candidate_limit)),
        semantic_text_schema_version=max(1, _get_int("semantic_text_schema_version")),
    )
