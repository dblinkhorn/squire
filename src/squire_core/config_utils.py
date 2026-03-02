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
_DEFAULT_NL_COMMAND_ROUTING_CONFIG = {
    "enabled": True,
    "clarify_on_ambiguous": True,
    "allow_nl_mutations": True,
    "plan_trace_enabled": True,
    "read_auto_min_confidence": 0.85,
    "mutation_confirm_min_confidence": 0.75,
    "max_recent_limit": 25,
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


@dataclass(frozen=True)
class NLCommandRoutingConfig:
    enabled: bool
    clarify_on_ambiguous: bool
    allow_nl_mutations: bool
    plan_trace_enabled: bool
    read_auto_min_confidence: float
    mutation_confirm_min_confidence: float
    max_recent_limit: int


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str


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


def load_llm_config(config: dict[str, Any]) -> LLMConfig:
    raw = config.get("llm")
    if not isinstance(raw, dict):
        raise ValueError("llm block is required in config.yaml.")
    provider_value = raw.get("provider")
    if not isinstance(provider_value, str) or not provider_value.strip():
        raise ValueError("llm.provider is required in config.yaml.")
    provider = str(provider_value).strip().lower()
    if not provider:
        raise ValueError("llm.provider is required in config.yaml.")
    model_value = raw.get("model")
    if not isinstance(model_value, str) or not model_value.strip():
        raise ValueError("llm.model is required in config.yaml.")
    model = str(model_value).strip()
    if not model:
        raise ValueError("llm.model is required in config.yaml.")
    return LLMConfig(
        provider=provider,
        model=model,
    )


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

    llm_provider = load_llm_config(config).provider
    semantic_provider_raw = raw.get("semantic_provider")
    semantic_provider = llm_provider
    if semantic_provider_raw is not None:
        if not isinstance(semantic_provider_raw, str) or not semantic_provider_raw.strip():
            raise ValueError("matching.semantic_provider must be a non-empty string when provided.")
        semantic_provider = semantic_provider_raw.strip().lower()

    semantic_weight = _get_float("semantic_weight")
    semantic_model_raw = raw.get("semantic_model")
    semantic_model = ""
    if semantic_weight > 0:
        if not isinstance(semantic_model_raw, str) or not semantic_model_raw.strip():
            raise ValueError("matching.semantic_model is required when matching.semantic_weight > 0.")
        semantic_model = semantic_model_raw.strip()
    elif isinstance(semantic_model_raw, str):
        semantic_model = semantic_model_raw.strip()

    return MatchingConfig(
        lexical_weight=_get_float("lexical_weight"),
        recency_weight=_get_float("recency_weight"),
        affinity_weight=_get_float("affinity_weight"),
        semantic_weight=semantic_weight,
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


def load_nl_command_routing_config(config: dict[str, Any]) -> NLCommandRoutingConfig:
    raw = config.get("nl_command_routing")
    if not isinstance(raw, dict):
        raw = {}

    def _get_bool(key: str) -> bool:
        value = raw.get(key, _DEFAULT_NL_COMMAND_ROUTING_CONFIG[key])
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(_DEFAULT_NL_COMMAND_ROUTING_CONFIG[key])

    def _get_float(key: str) -> float:
        value = raw.get(key, _DEFAULT_NL_COMMAND_ROUTING_CONFIG[key])
        if isinstance(value, (int, float)):
            return float(value)
        return float(_DEFAULT_NL_COMMAND_ROUTING_CONFIG[key])

    def _get_int(key: str) -> int:
        value = raw.get(key, _DEFAULT_NL_COMMAND_ROUTING_CONFIG[key])
        if isinstance(value, (int, float)):
            return int(value)
        return int(_DEFAULT_NL_COMMAND_ROUTING_CONFIG[key])

    return NLCommandRoutingConfig(
        enabled=_get_bool("enabled"),
        clarify_on_ambiguous=_get_bool("clarify_on_ambiguous"),
        allow_nl_mutations=_get_bool("allow_nl_mutations"),
        plan_trace_enabled=_get_bool("plan_trace_enabled"),
        read_auto_min_confidence=max(0.0, min(1.0, _get_float("read_auto_min_confidence"))),
        mutation_confirm_min_confidence=max(0.0, min(1.0, _get_float("mutation_confirm_min_confidence"))),
        max_recent_limit=max(1, min(50, _get_int("max_recent_limit"))),
    )
