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


def _extract_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    raise RuntimeError("No output_text found in OpenAI response")
