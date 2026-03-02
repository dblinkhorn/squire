from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, cast

from squire_core.config_utils import LLMConfig, load_llm_config
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.provider import (
    AsyncEmbeddingProvider,
    AsyncLLMProvider,
    EmbeddingProvider,
    LLMProvider,
)

LLMProviderLike = LLMProvider | AsyncLLMProvider

_ProviderFactory = Callable[[], LLMProviderLike]
_PROVIDER_FACTORIES: dict[str, _ProviderFactory] = {
    "openai": lambda: OpenAIProvider(),
}


@dataclass(frozen=True)
class EmbeddingProbeResult:
    available: bool
    reason: str | None = None


class _SyncEmbeddingAdapter:
    def __init__(self, provider: AsyncEmbeddingProvider) -> None:
        self._provider = provider

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return asyncio.run(self._provider.embed_async(texts, model))


class _AsyncEmbeddingAdapter:
    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider

    async def embed_async(self, texts: list[str], model: str) -> list[list[float]]:
        return await asyncio.to_thread(self._provider.embed, texts, model)


def supported_provider_names() -> list[str]:
    return sorted(_PROVIDER_FACTORIES.keys())


def create_provider(provider_name: str) -> LLMProviderLike:
    normalized = provider_name.strip().lower()
    factory = _PROVIDER_FACTORIES.get(normalized)
    if factory is None:
        supported = ", ".join(supported_provider_names())
        raise ValueError(f"Unsupported LLM provider: {provider_name!r}. Supported providers: {supported}")
    return factory()


def build_provider_from_config(config: dict[str, Any]) -> tuple[LLMConfig, LLMProviderLike]:
    llm = load_llm_config(config)
    return llm, create_provider(llm.provider)


def provider_name(value: Any) -> str:
    name = getattr(value, "provider_name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    return value.__class__.__name__.strip().lower()


def get_async_embedding_provider(provider: Any) -> AsyncEmbeddingProvider | None:
    embed_async = getattr(provider, "embed_async", None)
    if callable(embed_async):
        return cast(AsyncEmbeddingProvider, provider)
    embed = getattr(provider, "embed", None)
    if callable(embed):
        return _AsyncEmbeddingAdapter(cast(EmbeddingProvider, provider))
    return None


def get_sync_embedding_provider(provider: Any) -> EmbeddingProvider | None:
    embed = getattr(provider, "embed", None)
    if callable(embed):
        return cast(EmbeddingProvider, provider)
    embed_async = getattr(provider, "embed_async", None)
    if callable(embed_async):
        return _SyncEmbeddingAdapter(cast(AsyncEmbeddingProvider, provider))
    return None


def supports_embeddings(provider: Any) -> bool:
    return get_sync_embedding_provider(provider) is not None


def probe_embedding_support(provider: Any, model: str) -> EmbeddingProbeResult:
    embedding_provider = get_sync_embedding_provider(provider)
    if embedding_provider is None:
        return EmbeddingProbeResult(available=False, reason="provider_has_no_embedding_api")
    model_value = str(model).strip()
    if not model_value:
        return EmbeddingProbeResult(available=False, reason="embedding_model_missing")
    try:
        vectors = embedding_provider.embed(["squire embedding capability probe"], model_value)
    except Exception as exc:  # pragma: no cover - defensive path
        return EmbeddingProbeResult(available=False, reason=f"{exc.__class__.__name__}: {exc}")
    if not vectors or not isinstance(vectors[0], list) or not vectors[0]:
        return EmbeddingProbeResult(available=False, reason="empty_embedding_result")
    return EmbeddingProbeResult(available=True)
