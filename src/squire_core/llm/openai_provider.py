from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from squire_core.llm.provider import LLMResult


class OpenAIProvider:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAIProvider")

    def interpret(self, text: str, schema: dict, model: str, system_prompt: str) -> LLMResult:
        body = {
            "model": model,
            "instructions": system_prompt,
            "input": text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "derived_event",
                    "schema": schema,
                    "strict": True,
                }
            },
        }

        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8")
            raise RuntimeError(f"OpenAI API error {exc.code}: {error_body}") from exc

        raw_text = _extract_output_text(payload)
        parsed = json.loads(raw_text)
        return LLMResult(payload=parsed, raw_text=raw_text)

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        if not texts:
            return []
        body = {
            "model": model,
            "input": texts,
        }

        request = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8")
            raise RuntimeError(f"OpenAI embedding API error {exc.code}: {error_body}") from exc
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


def _extract_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    raise RuntimeError("No output_text found in OpenAI response")
