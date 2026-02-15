from __future__ import annotations

import asyncio
import json

import pytest

from squire_core.llm.openai_provider import OpenAIProvider

def test_interpret_uses_configured_timeout(monkeypatch) -> None:
    captured: dict[str, float] = {}

    async def _fake_post_json(self, url, body, *, timeout_seconds, error_prefix):
        captured["timeout"] = timeout_seconds
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "{\"title\": \"Call dentist\"}"}],
                }
            ]
        }

    monkeypatch.setattr(OpenAIProvider, "_post_json", _fake_post_json)
    provider = OpenAIProvider(api_key="test", interpret_timeout_seconds=12.5)

    result = asyncio.run(
        provider.interpret_async(
            text="call dentist",
            schema={"type": "object"},
            model="gpt-5-mini",
            system_prompt="Extract",
        )
    )

    assert captured["timeout"] == 12.5
    assert result.payload == {"title": "Call dentist"}


def test_embed_uses_configured_timeout(monkeypatch) -> None:
    captured: dict[str, float] = {}

    async def _fake_post_json(self, url, body, *, timeout_seconds, error_prefix):
        captured["timeout"] = timeout_seconds
        return {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2]},
                {"index": 1, "embedding": [0.3, 0.4]},
            ]
        }

    monkeypatch.setattr(OpenAIProvider, "_post_json", _fake_post_json)
    provider = OpenAIProvider(api_key="test", embed_timeout_seconds=30.0)

    vectors = asyncio.run(provider.embed_async(["first", "second"], model="text-embedding-3-small"))

    assert captured["timeout"] == 30.0
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_timeouts_default_to_45_seconds(monkeypatch) -> None:
    captured: list[float] = []

    async def _fake_post_json(self, url, body, *, timeout_seconds, error_prefix):
        captured.append(timeout_seconds)
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "{\"title\": \"Call dentist\"}"}],
                }
            ]
        }

    monkeypatch.setattr(OpenAIProvider, "_post_json", _fake_post_json)
    provider = OpenAIProvider(api_key="test")

    asyncio.run(
        provider.interpret_async(
            text="call dentist",
            schema={"type": "object"},
            model="gpt-5-mini",
            system_prompt="Extract",
        )
    )

    assert captured == [45.0]


def test_timeouts_clamp_to_minimum_10_seconds(monkeypatch) -> None:
    captured: list[float] = []

    async def _fake_post_json(self, url, body, *, timeout_seconds, error_prefix):
        captured.append(timeout_seconds)
        if url.endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "{\"title\": \"Call dentist\"}"}],
                }
            ]
        }

    monkeypatch.setattr(OpenAIProvider, "_post_json", _fake_post_json)
    provider = OpenAIProvider(
        api_key="test",
        interpret_timeout_seconds=1.0,
        embed_timeout_seconds=2.0,
    )

    asyncio.run(
        provider.interpret_async(
            text="call dentist",
            schema={"type": "object"},
            model="gpt-5-mini",
            system_prompt="Extract",
        )
    )
    asyncio.run(provider.embed_async(["first"], model="text-embedding-3-small"))

    assert captured == [10.0, 10.0]


def test_sync_methods_raise_inside_active_event_loop(monkeypatch) -> None:
    async def _fake_post_json(self, url, body, *, timeout_seconds, error_prefix):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps({"title": "Call dentist"})}],
                }
            ]
        }

    monkeypatch.setattr(OpenAIProvider, "_post_json", _fake_post_json)
    provider = OpenAIProvider(api_key="test")

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="cannot run inside an active event loop"):
            provider.interpret(
                text="call dentist",
                schema={"type": "object"},
                model="gpt-5-mini",
                system_prompt="Extract",
            )

    asyncio.run(_run())
