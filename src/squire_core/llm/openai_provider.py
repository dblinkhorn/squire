from __future__ import annotations

import asyncio
import copy
import json
import os
from typing import Any, Awaitable, TypeVar

import aiohttp

from squire_core.llm.provider import LLMResult

_DEFAULT_INTERPRET_TIMEOUT_SECONDS = 45.0
_DEFAULT_EMBED_TIMEOUT_SECONDS = 45.0
_MIN_TIMEOUT_SECONDS = 10.0
_RESPONSES_URL = "https://api.openai.com/v1/responses"
_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_T = TypeVar("_T")
_OPENAI_SCHEMA_STRIP_KEYS = {
    "$schema",
    "$id",
    "definitions",
    "$defs",
    "format",
}


class OpenAIProvider:
    provider_name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        interpret_timeout_seconds: float = _DEFAULT_INTERPRET_TIMEOUT_SECONDS,
        embed_timeout_seconds: float = _DEFAULT_EMBED_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAIProvider")
        self._interpret_timeout_seconds = max(_MIN_TIMEOUT_SECONDS, float(interpret_timeout_seconds))
        self._embed_timeout_seconds = max(_MIN_TIMEOUT_SECONDS, float(embed_timeout_seconds))

    async def interpret_async(self, text: str, schema: dict, model: str, system_prompt: str) -> LLMResult:
        response_schema = _prepare_response_format_schema(schema)
        body = {
            "model": model,
            "instructions": system_prompt,
            "input": text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "derived_event",
                    "schema": response_schema,
                    "strict": True,
                }
            },
        }
        payload = await self._post_json(
            _RESPONSES_URL,
            body,
            timeout_seconds=self._interpret_timeout_seconds,
            error_prefix="OpenAI API",
        )
        raw_text = _extract_output_text(payload)
        parsed = json.loads(raw_text)
        return LLMResult(payload=parsed, raw_text=raw_text)

    def interpret(self, text: str, schema: dict, model: str, system_prompt: str) -> LLMResult:
        return _run_coro_sync(self.interpret_async(text=text, schema=schema, model=model, system_prompt=system_prompt))

    async def embed_async(self, texts: list[str], model: str) -> list[list[float]]:
        if not texts:
            return []
        body = {
            "model": model,
            "input": texts,
        }
        payload = await self._post_json(
            _EMBEDDINGS_URL,
            body,
            timeout_seconds=self._embed_timeout_seconds,
            error_prefix="OpenAI embedding API",
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("OpenAI embedding API returned invalid payload")
        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda value: value.get("index", 0) if isinstance(value, dict) else 0):
            if not isinstance(item, dict):
                continue
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise RuntimeError("OpenAI embedding API returned invalid embedding")
            vector = [float(value) for value in embedding if isinstance(value, (int, float))]
            if not vector:
                raise RuntimeError("OpenAI embedding API returned empty embedding")
            vectors.append(vector)
        if len(vectors) != len(texts):
            raise RuntimeError("OpenAI embedding API returned unexpected number of embeddings")
        return vectors

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return _run_coro_sync(self.embed_async(texts=texts, model=model))

    async def _post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        timeout_seconds: float,
        error_prefix: str,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.post(url, json=body) as response:
                    raw_text = await response.text()
                    if response.status >= 400:
                        raise RuntimeError(f"{error_prefix} error {response.status}: {raw_text}")
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{error_prefix} request timed out after {timeout_seconds:.1f}s") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"{error_prefix} request failed: {exc}") from exc
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{error_prefix} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{error_prefix} returned invalid payload")
        return payload


def _extract_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    raise RuntimeError("No output_text found in OpenAI response")


def _prepare_response_format_schema(schema: dict[str, Any]) -> dict[str, Any]:
    root = copy.deepcopy(schema)
    return _sanitize_schema_node(root, root)


def _sanitize_schema_node(node: Any, root: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_sanitize_schema_node(item, root) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_local_schema_ref(root, ref)
        merged = copy.deepcopy(resolved)
        for key, value in node.items():
            if key == "$ref":
                continue
            merged[key] = value
        return _sanitize_schema_node(merged, root)

    sanitized: dict[str, Any] = {}
    for key, value in node.items():
        if key in _OPENAI_SCHEMA_STRIP_KEYS:
            continue
        sanitized[key] = _sanitize_schema_node(value, root)
    _normalize_required_keys(sanitized)
    return sanitized


def _resolve_local_schema_ref(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"Unsupported schema reference: {ref}")
    current: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Unresolvable schema reference: {ref}")
        current = current[part]
    return current


def _normalize_required_keys(node: dict[str, Any]) -> None:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return

    node_type = node.get("type")
    is_object_schema = node_type == "object"
    if isinstance(node_type, list):
        is_object_schema = "object" in node_type
    if not is_object_schema:
        return

    required = node.get("required")
    required_items: list[str] = []
    if isinstance(required, list):
        required_items.extend(item for item in required if isinstance(item, str))

    seen = set(required_items)
    for key in properties:
        if key in seen:
            continue
        required_items.append(key)
        seen.add(key)
    node["required"] = required_items


def _run_coro_sync(coro: Awaitable[_T]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if hasattr(coro, "close"):
        coro.close()
    raise RuntimeError("OpenAIProvider sync methods cannot run inside an active event loop; use async methods instead.")
