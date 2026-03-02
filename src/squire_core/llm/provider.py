from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMResult:
    payload: dict
    raw_text: str


@runtime_checkable
class LLMProvider(Protocol):
    def interpret(self, text: str, schema: dict, model: str, system_prompt: str) -> LLMResult:
        ...


@runtime_checkable
class AsyncLLMProvider(Protocol):
    async def interpret_async(self, text: str, schema: dict, model: str, system_prompt: str) -> LLMResult:
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        ...


@runtime_checkable
class AsyncEmbeddingProvider(Protocol):
    async def embed_async(self, texts: list[str], model: str) -> list[list[float]]:
        ...
