from __future__ import annotations

import json
from types import SimpleNamespace

from squire_core.transport import matching_pipeline


def test_build_capture_input_serializes_candidates() -> None:
    payload = matching_pipeline.build_capture_input(
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


def test_build_decision_payload_from_capture_prefers_decision_confidence() -> None:
    payload = matching_pipeline.build_decision_payload_from_capture(
        raw_event_id="R_1",
        object_type="admin",
        derived={
            "confidence": 0.62,
            "decision_confidence": 0.88,
            "proposed_operations": [{"op": "update", "target_id": "A_1"}],
            "model": "gpt-5-mini",
            "prompt_version": "extract_v1",
            "timestamp": "2026-03-22T10:00:00+00:00",
        },
        candidates=[
            SimpleNamespace(
                object_id="A_1",
                title="Call dentist",
                snippet="Call dentist next Tuesday",
                score=0.93,
            )
        ],
    )

    assert payload["confidence"] == 0.88
    assert payload["proposed_operations"] == [{"op": "update", "target_id": "A_1"}]
