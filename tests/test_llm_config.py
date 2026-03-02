from __future__ import annotations

import pytest

from squire_core.config_utils import load_llm_config, load_matching_config


def test_load_llm_config_requires_llm_block() -> None:
    with pytest.raises(ValueError, match="llm block is required"):
        load_llm_config({})


def test_load_llm_config_requires_provider() -> None:
    with pytest.raises(ValueError, match="llm.provider is required"):
        load_llm_config({"llm": {"model": "gpt-5-mini"}})


def test_load_llm_config_requires_model() -> None:
    with pytest.raises(ValueError, match="llm.model is required"):
        load_llm_config({"llm": {"provider": "openai"}})


def test_load_llm_config_normalizes_provider() -> None:
    llm = load_llm_config(
        {
            "llm": {
                "provider": " OPENAI ",
                "model": "gpt-5-mini",
            }
        }
    )
    assert llm.provider == "openai"
    assert llm.model == "gpt-5-mini"


def test_load_matching_config_requires_semantic_model_when_semantic_weight_enabled() -> None:
    with pytest.raises(ValueError, match="matching.semantic_model is required"):
        load_matching_config({"llm": {"provider": "openai", "model": "gpt-5-mini"}})


def test_matching_semantic_provider_defaults_to_active_llm_provider() -> None:
    matching = load_matching_config(
        {
            "llm": {"provider": "openai", "model": "gpt-5-mini"},
            "matching": {"semantic_model": "text-embedding-3-small"},
        }
    )
    assert matching.semantic_provider == "openai"


def test_matching_semantic_provider_override_is_normalized() -> None:
    matching = load_matching_config(
        {
            "llm": {"provider": "openai", "model": "gpt-5-mini"},
            "matching": {
                "semantic_provider": " OPENAI ",
                "semantic_model": "text-embedding-3-small",
            },
        }
    )
    assert matching.semantic_provider == "openai"


def test_matching_semantic_provider_must_be_non_empty_when_provided() -> None:
    with pytest.raises(ValueError, match="matching.semantic_provider must be a non-empty string"):
        load_matching_config(
            {
                "llm": {"provider": "openai", "model": "gpt-5-mini"},
                "matching": {
                    "semantic_provider": "   ",
                    "semantic_model": "text-embedding-3-small",
                },
            }
        )


def test_matching_semantic_model_optional_when_semantic_weight_disabled() -> None:
    matching = load_matching_config(
        {
            "llm": {"provider": "openai", "model": "gpt-5-mini"},
            "matching": {"semantic_weight": 0},
        }
    )
    assert matching.semantic_weight == 0
    assert matching.semantic_model == ""
