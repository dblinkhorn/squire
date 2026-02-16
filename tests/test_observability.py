from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from squire_core import observability


def _counter_value(snapshot: dict[str, list[dict[str, Any]]], name: str, **labels: str) -> float:
    for row in snapshot["counters"]:
        if row["name"] != name:
            continue
        if row["labels"] == labels:
            return float(row["value"])
    return 0.0


def _histogram_count(snapshot: dict[str, list[dict[str, Any]]], name: str, **labels: str) -> int:
    for row in snapshot["histograms"]:
        if row["name"] != name:
            continue
        if row["labels"] == labels:
            return int(row["count"])
    return 0


def test_json_log_formatter_includes_run_id_and_extra_fields() -> None:
    observability.set_run_id("run_test_123")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    record.event = "unit_test"
    record.pipeline_stage = "classify"
    record.duration_ms = 42

    formatter = observability.JsonLogFormatter()
    payload = json.loads(formatter.format(record))

    assert payload["event"] == "unit_test"
    assert payload["message"] == "hello world"
    assert payload["run_id"] == "run_test_123"
    assert payload["pipeline_stage"] == "classify"
    assert payload["duration_ms"] == 42


def test_observe_stage_records_success_histogram() -> None:
    observability.reset_metrics_for_tests()
    observability.set_run_id("run_success")

    with observability.observe_stage("classify", raw_event_id="R_1"):
        pass

    snapshot = observability.snapshot_metrics()
    assert _histogram_count(snapshot, "squire_stage_duration_seconds", stage="classify") == 1


def test_observe_stage_records_failure_counter() -> None:
    observability.reset_metrics_for_tests()
    observability.set_run_id("run_failure")

    with pytest.raises(ValueError):
        with observability.observe_stage("decision.route", raw_event_id="R_2"):
            raise ValueError("boom")

    snapshot = observability.snapshot_metrics()
    assert (
        _counter_value(
            snapshot,
            "squire_pipeline_failures_total",
            stage="decision.route",
            error_type="ValueError",
        )
        == 1.0
    )
    assert _histogram_count(snapshot, "squire_stage_duration_seconds", stage="decision.route") == 1


def test_load_observability_config_reads_nested_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    monkeypatch.delenv("SQUIRE_LOG_LEVEL", raising=False)
    monkeypatch.delenv("SQUIRE_ENV", raising=False)

    config = {
        "observability": {
            "enabled": True,
            "service_name": "squire-test",
            "environment": "test",
            "log_level": "DEBUG",
            "otlp_endpoint": "http://localhost:4318",
            "otlp_headers": "a=b,c=d",
        }
    }

    loaded = observability.load_observability_config(config)

    assert loaded.enabled is True
    assert loaded.service_name == "squire-test"
    assert loaded.environment == "test"
    assert loaded.log_level == "DEBUG"
    assert loaded.otlp_endpoint == "http://localhost:4318"
    assert loaded.otlp_headers == "a=b,c=d"


def test_load_observability_config_prefers_squire_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SQUIRE_ENV", "dev")
    config = {"observability": {"environment": "prod"}}

    loaded = observability.load_observability_config(config)

    assert loaded.environment == "dev"
    assert loaded.log_level == "INFO"


def test_load_observability_config_prefers_log_level_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SQUIRE_LOG_LEVEL", "ERROR")
    config = {"observability": {"log_level": "DEBUG"}}

    loaded = observability.load_observability_config(config)

    assert loaded.log_level == "ERROR"


def test_initialize_observability_fails_fast_when_enabled_without_endpoint() -> None:
    config = observability.ObservabilityConfig(
        enabled=True,
        service_name="squire-test",
        environment="test",
        log_level="INFO",
        otlp_endpoint=None,
        otlp_headers=None,
    )
    with pytest.raises(RuntimeError) as excinfo:
        observability.initialize_observability(config)
    message = str(excinfo.value)
    assert "OTLP endpoint" in message


def test_configure_logging_uses_explicit_debug_level() -> None:
    observability.configure_logging("DEBUG")
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


def test_stage_complete_log_is_emitted_at_debug_level(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[int, str, object]] = []

    def _capture(level: int, event: str, message: str, **fields: object) -> None:
        del message
        captured.append((level, event, fields.get("duration_ms")))

    monkeypatch.setattr(observability, "log_event", _capture)

    with observability.observe_stage("classify"):
        pass

    assert any(level == logging.DEBUG and event == "stage_complete" for level, event, _ in captured)
    durations = [value for level, event, value in captured if level == logging.DEBUG and event == "stage_complete"]
    assert durations
    assert all(isinstance(value, int) and value >= 1 for value in durations)
