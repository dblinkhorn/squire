from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Sequence

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

_DEFAULT_SERVICE_NAME = "squire-core"
_TRACER_NAME = "squire"
_TRACER_VERSION = "0.1.0"

_provider: TracerProvider | None = None
_tracer = trace.get_tracer(_TRACER_NAME, _TRACER_VERSION)
_initialized = False
_enabled = False


def initialize_tracing(
    *,
    service_name: str = _DEFAULT_SERVICE_NAME,
    exporter: SpanExporter | None = None,
    span_processors: Sequence[SpanProcessor] | None = None,
) -> bool:
    global _provider, _tracer, _initialized, _enabled
    if _initialized:
        return _enabled

    _initialized = True
    if _sdk_disabled():
        return False
    if exporter is None and span_processors is None and not _has_otlp_endpoint():
        return False

    try:
        resource = Resource.create(_build_resource_attributes(service_name))
        provider = TracerProvider(resource=resource)
        processors = list(span_processors or ())
        if not processors:
            if exporter is None:
                exporter = OTLPSpanExporter()
                processors.append(BatchSpanProcessor(exporter))
            else:
                processors.append(SimpleSpanProcessor(exporter))
        for processor in processors:
            provider.add_span_processor(processor)
        _provider = provider
        _tracer = provider.get_tracer(_TRACER_NAME, _TRACER_VERSION)
        _enabled = True
        logging.info("tracing_enabled service_name=%s", _resolved_service_name(service_name))
        return True
    except Exception as exc:
        logging.warning("tracing_init_failed error=%s", exc)
        _provider = None
        _tracer = trace.get_tracer(_TRACER_NAME, _TRACER_VERSION)
        _enabled = False
        return False


def shutdown_tracing() -> None:
    global _provider, _tracer, _initialized, _enabled
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception as exc:
            logging.warning("tracing_shutdown_failed error=%s", exc)
    _provider = None
    _tracer = trace.get_tracer(_TRACER_NAME, _TRACER_VERSION)
    _initialized = False
    _enabled = False


def reset_tracing_for_tests() -> None:
    shutdown_tracing()


def tracing_enabled() -> bool:
    return _enabled


def start_span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
):
    return _tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=_normalize_attributes(attributes),
        record_exception=True,
        set_status_on_exception=True,
    )


def current_span() -> Span:
    return trace.get_current_span()


def set_span_attribute(key: str, value: Any, *, span: Span | None = None) -> None:
    target = span or current_span()
    if not target.is_recording():
        return
    normalized = _normalize_attribute_value(value)
    if normalized is None:
        return
    target.set_attribute(key, normalized)


def set_span_attributes(attributes: Mapping[str, Any], *, span: Span | None = None) -> None:
    target = span or current_span()
    if not target.is_recording():
        return
    for key, value in attributes.items():
        normalized = _normalize_attribute_value(value)
        if normalized is None:
            continue
        target.set_attribute(key, normalized)


def record_exception(
    exc: BaseException,
    *,
    span: Span | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> None:
    target = span or current_span()
    if not target.is_recording():
        return
    target.record_exception(exc, attributes=_normalize_attributes(attributes))
    target.set_status(Status(StatusCode.ERROR, str(exc)))


def set_status_error(description: str, *, span: Span | None = None) -> None:
    target = span or current_span()
    if not target.is_recording():
        return
    target.set_status(Status(StatusCode.ERROR, description))


def _sdk_disabled() -> bool:
    value = os.getenv("OTEL_SDK_DISABLED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _has_otlp_endpoint() -> bool:
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    )


def _resolved_service_name(default: str) -> str:
    value = os.getenv("OTEL_SERVICE_NAME", "").strip()
    return value or default


def _build_resource_attributes(default_service_name: str) -> dict[str, str]:
    attributes = _parse_resource_attributes(os.getenv("OTEL_RESOURCE_ATTRIBUTES", ""))
    attributes["service.name"] = _resolved_service_name(default_service_name)
    return attributes


def _parse_resource_attributes(value: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for item in value.split(","):
        if "=" not in item:
            continue
        key, raw = item.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key or not raw:
            continue
        attributes[key] = raw
    return attributes


def _normalize_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not attributes:
        return None
    normalized: dict[str, Any] = {}
    for key, value in attributes.items():
        item = _normalize_attribute_value(value)
        if item is None:
            continue
        normalized[key] = item
    return normalized or None


def _normalize_attribute_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, str, int, float)):
        return value
    if isinstance(value, Sequence) and not isinstance(value, str):
        normalized_items: list[Any] = []
        for item in value:
            normalized_item = _normalize_attribute_value(item)
            if normalized_item is None or isinstance(normalized_item, list):
                continue
            normalized_items.append(normalized_item)
        return normalized_items or None
    return str(value)
