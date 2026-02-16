from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, cast
from uuid import uuid4

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode


_RUN_ID = contextvars.ContextVar[str | None]("squire_run_id", default=None)
_RUNTIME_ENV = contextvars.ContextVar[str]("squire_runtime_env", default="dev")
_INSTRUMENTATION_NAME = "squire_core"
_TRACER = trace.get_tracer(_INSTRUMENTATION_NAME)
_METER = metrics.get_meter(_INSTRUMENTATION_NAME)

_INITIALIZED = False

_COUNTER_INSTRUMENTS: dict[str, Any] = {}
_HISTOGRAM_INSTRUMENTS: dict[str, Any] = {}
_INSTRUMENT_LOCK = threading.Lock()

_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}
_METRIC_LOCK = threading.Lock()


@dataclass(frozen=True)
class ObservabilityConfig:
    enabled: bool
    service_name: str
    environment: str
    log_level: str
    otlp_endpoint: str | None
    otlp_headers: str | None


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def _coerce_str(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _coerce_log_level(value: Any, fallback: str = "INFO") -> str:
    name = _coerce_str(value, fallback).upper()
    if name == "WARN":
        name = "WARNING"
    parsed = logging.getLevelName(name)
    if isinstance(parsed, int):
        return name
    return fallback.upper()


def load_observability_config(config: dict[str, Any]) -> ObservabilityConfig:
    raw = config.get("observability") if isinstance(config, dict) else None
    section = raw if isinstance(raw, dict) else {}

    enabled = _coerce_bool(os.getenv("OTEL_ENABLED"), _coerce_bool(section.get("enabled"), False))
    service_name = _coerce_str(section.get("service_name"), "squire-core")
    environment = _coerce_str(os.getenv("SQUIRE_ENV"), _coerce_str(section.get("environment"), "dev"))
    log_level = _coerce_log_level(
        os.getenv("SQUIRE_LOG_LEVEL"),
        _coerce_log_level(section.get("log_level"), "INFO"),
    )
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or _coerce_str(section.get("otlp_endpoint"), "")
    otlp_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS") or _coerce_str(section.get("otlp_headers"), "")
    return ObservabilityConfig(
        enabled=enabled,
        service_name=service_name,
        environment=environment,
        log_level=log_level,
        otlp_endpoint=otlp_endpoint or None,
        otlp_headers=otlp_headers or None,
    )


def generate_run_id() -> str:
    return f"run_{uuid4().hex[:12]}"


def set_run_id(run_id: str) -> str:
    value = run_id.strip() or generate_run_id()
    _RUN_ID.set(value)
    return value


def get_run_id() -> str | None:
    return _RUN_ID.get()


def set_runtime_environment(environment: str | None) -> str:
    value = (environment or "").strip().lower() or "dev"
    _RUNTIME_ENV.set(value)
    return value


def get_runtime_environment() -> str:
    return _RUNTIME_ENV.get()


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self._max_level


class JsonLogFormatter(logging.Formatter):
    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None) or get_run_id()
        if run_id:
            payload["run_id"] = run_id

        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = value

        if record.exc_info is not None:
            exc_info = cast(tuple[type[BaseException], BaseException, Any], record.exc_info)
            payload["error_type"] = payload.get("error_type") or str(exc_info[0].__name__)
            payload["exception"] = self.formatException(exc_info)

        return json.dumps(payload, default=str, ensure_ascii=True)


def configure_logging(log_level_name: str = "INFO") -> None:
    normalized_level_name = _coerce_log_level(log_level_name, "INFO")
    parsed_level = logging.getLevelName(normalized_level_name)
    log_level = parsed_level if isinstance(parsed_level, int) else logging.INFO
    root = logging.getLogger()
    root.setLevel(log_level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter: logging.Formatter = JsonLogFormatter()

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(log_level)
    stdout_handler.addFilter(_MaxLevelFilter(logging.ERROR))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)


def log_event(level: int, event: str, message: str, **fields: Any) -> None:
    extra: dict[str, Any] = {"event": event}
    run_id = get_run_id()
    if run_id:
        extra["run_id"] = run_id
    for key, value in fields.items():
        if value is not None:
            extra[key] = value
    logging.getLogger().log(level, message, extra=extra)


def _normalize_labels(labels: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        normalized[str(key)] = str(value)
    return normalized


def _metric_key(metric_name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (metric_name, tuple(sorted(labels.items())))


def increment_counter(metric_name: str, amount: float = 1.0, **labels: Any) -> None:
    normalized = _normalize_labels(labels)
    key = _metric_key(metric_name, normalized)
    with _METRIC_LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0.0) + float(amount)

    with _INSTRUMENT_LOCK:
        instrument = _COUNTER_INSTRUMENTS.get(metric_name)
        if instrument is None:
            instrument = _METER.create_counter(metric_name)
            _COUNTER_INSTRUMENTS[metric_name] = instrument
    instrument.add(float(amount), attributes=normalized)


def observe_histogram(metric_name: str, value: float, **labels: Any) -> None:
    normalized = _normalize_labels(labels)
    key = _metric_key(metric_name, normalized)
    with _METRIC_LOCK:
        values = _HISTOGRAMS.get(key)
        if values is None:
            values = []
            _HISTOGRAMS[key] = values
        values.append(float(value))

    with _INSTRUMENT_LOCK:
        instrument = _HISTOGRAM_INSTRUMENTS.get(metric_name)
        if instrument is None:
            instrument = _METER.create_histogram(metric_name)
            _HISTOGRAM_INSTRUMENTS[metric_name] = instrument
    instrument.record(float(value), attributes=normalized)


def snapshot_metrics() -> dict[str, list[dict[str, Any]]]:
    with _METRIC_LOCK:
        counters = [
            {"name": name, "labels": dict(labels), "value": value}
            for (name, labels), value in sorted(_COUNTERS.items())
        ]
        histograms = [
            {"name": name, "labels": dict(labels), "count": len(values), "sum": sum(values)}
            for (name, labels), values in sorted(_HISTOGRAMS.items())
        ]
    return {"counters": counters, "histograms": histograms}


def reset_metrics_for_tests() -> None:
    with _METRIC_LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()


def _elapsed_ms(seconds: float) -> int:
    return max(1, int(round(seconds * 1000)))


@contextmanager
def observe_stage(stage: str, **fields: Any) -> Iterator[None]:
    started = time.perf_counter()
    with _TRACER.start_as_current_span(stage) as span:
        run_id = get_run_id()
        if run_id:
            span.set_attribute("run_id", run_id)
        span.set_attribute("pipeline_stage", stage)
        for key, value in fields.items():
            if value is not None:
                span.set_attribute(key, str(value))
        try:
            yield
        except Exception as exc:
            elapsed_seconds = time.perf_counter() - started
            elapsed_ms = _elapsed_ms(elapsed_seconds)
            observe_histogram("squire_stage_duration_seconds", elapsed_seconds, stage=stage)
            increment_counter("squire_pipeline_failures_total", stage=stage, error_type=exc.__class__.__name__)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            log_event(
                logging.ERROR,
                "stage_failed",
                "stage_failed",
                pipeline_stage=stage,
                duration_ms=elapsed_ms,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
                **fields,
            )
            raise
        else:
            elapsed_seconds = time.perf_counter() - started
            elapsed_ms = _elapsed_ms(elapsed_seconds)
            observe_histogram("squire_stage_duration_seconds", elapsed_seconds, stage=stage)
            log_event(
                logging.DEBUG,
                "stage_complete",
                "stage_complete",
                pipeline_stage=stage,
                duration_ms=elapsed_ms,
                **fields,
            )


def _parse_otlp_headers(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    headers: dict[str, str] = {}
    for item in value.split(","):
        token = item.strip()
        if not token or "=" not in token:
            continue
        key, header_value = token.split("=", 1)
        key = key.strip()
        header_value = header_value.strip()
        if key and header_value:
            headers[key] = header_value
    return headers or None


def _signal_endpoint(base: str, signal: str) -> str:
    trimmed = base.strip()
    if "/v1/" in trimmed:
        return trimmed
    return f"{trimmed.rstrip('/')}/v1/{signal}"


def initialize_observability(config: ObservabilityConfig) -> None:
    global _INITIALIZED, _TRACER, _METER
    if _INITIALIZED:
        return
    if not config.enabled:
        return
    if not config.otlp_endpoint:
        raise RuntimeError("Observability is enabled but OTLP endpoint is not configured.")

    headers = _parse_otlp_headers(config.otlp_headers)
    try:
        resource_attributes: dict[str, str] = {
            "service.name": config.service_name,
            "deployment.environment": config.environment,
        }
        run_id = get_run_id()
        if run_id:
            resource_attributes["run_id"] = run_id
        resource = Resource.create(
            resource_attributes
        )

        tracer_provider = TracerProvider(resource=resource)
        tracer_exporter = OTLPSpanExporter(
            endpoint=_signal_endpoint(config.otlp_endpoint, "traces"),
            headers=headers,
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(tracer_exporter))
        trace.set_tracer_provider(tracer_provider)
        _TRACER = trace.get_tracer(_INSTRUMENTATION_NAME)

        metric_exporter = OTLPMetricExporter(
            endpoint=_signal_endpoint(config.otlp_endpoint, "metrics"),
            headers=headers,
        )
        metric_reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        _METER = metrics.get_meter(_INSTRUMENTATION_NAME)
    except Exception as exc:  # pragma: no cover - exporter init failure path
        raise RuntimeError(f"Observability initialization failed: {exc}") from exc

    _INITIALIZED = True
    log_event(
        logging.INFO,
        "otel_initialized",
        "otel_initialized",
        service_name=config.service_name,
        environment=config.environment,
    )
