from __future__ import annotations

import pytest

from squire_core.llm.registry import (
    build_provider_from_config,
    create_provider,
    probe_embedding_support,
    provider_name,
    supported_provider_names,
)


def test_supported_provider_names_contains_built_ins() -> None:
    assert supported_provider_names() == ["openai"]


def test_build_provider_from_config_requires_explicit_provider_and_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    llm, provider = build_provider_from_config({"llm": {"provider": "openai", "model": "gpt-5-mini"}})
    assert llm.provider == "openai"
    assert llm.model == "gpt-5-mini"
    assert provider_name(provider) == "openai"


def test_create_provider_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        create_provider("not-a-provider")


def test_probe_embedding_support_returns_false_without_embedding_api() -> None:
    class _NoEmbeddings:
        provider_name = "fake-no-embed"

    result = probe_embedding_support(_NoEmbeddings(), "text-embedding-any")
    assert result.available is False
    assert result.reason == "provider_has_no_embedding_api"


def test_probe_embedding_support_returns_true_when_embedding_call_succeeds() -> None:
    class _EmbeddingsOk:
        provider_name = "fake-embed"

        def embed(self, texts: list[str], model: str) -> list[list[float]]:
            assert texts == ["squire embedding capability probe"]
            assert model == "fake-model"
            return [[0.1, 0.2]]

    result = probe_embedding_support(_EmbeddingsOk(), "fake-model")
    assert result.available is True
    assert result.reason is None
