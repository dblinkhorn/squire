from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from squire_core.transport import matching_pipeline


def test_build_decision_input_serializes_candidates() -> None:
    payload = matching_pipeline.build_decision_input(
        raw_event_id="R_1",
        object_type="admin",
        message="call dentist",
        candidates=[
            SimpleNamespace(
                object_id="A_1",
                title="Call dentist",
                snippet="Call dentist next Tuesday",
                score=0.93,
            )
        ],
    )

    parsed = json.loads(payload)
    assert parsed["raw_event_id"] == "R_1"
    assert parsed["object_type"] == "admin"
    assert parsed["candidates"][0]["id"] == "A_1"
    assert parsed["candidates"][0]["score"] == 0.93


def test_candidate_queries_from_llm_filters_non_strings(monkeypatch) -> None:
    async def _fake_interpret_text_async(**kwargs):
        del kwargs
        return SimpleNamespace(derived={"queries": ["  alpha  ", 123, "", "beta"]})

    monkeypatch.setattr(matching_pipeline, "interpret_text_async", _fake_interpret_text_async)

    queries = asyncio.run(
        matching_pipeline.candidate_queries_from_llm(
            provider=SimpleNamespace(),
            model="gpt-5-mini",
            prompt="prompt",
            message="msg",
        )
    )

    assert queries == ["alpha", "beta"]
