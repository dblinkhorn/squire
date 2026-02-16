# Observability

## Purpose

Document Squire observability signals and how they are used for runtime operations and agent validation workflows.

## Current Runtime Signals (Implemented)

### Health Endpoint

- Route: `GET /health`
- Expected response: HTTP `200` with `{"status":"ok"}`
- Controlled by:
  - `HEALTH_HOST` (default `0.0.0.0`)
  - `HEALTH_PORT` (default `8080`, `0` disables server)

### Logs

- `INFO` and `WARNING` go to `stdout`
- `ERROR` and above go to `stderr`
- Logging format is structured JSON lines only.
- `run_id` correlation is attached when available (`SQUIRE_RUN_ID` or generated at startup)
- `stage_complete` events are debug-level lifecycle logs; enable by setting `observability.log_level: "DEBUG"` in `config.yaml`.

### Stage Timing and Counters

Phase 1 introduces stage instrumentation hooks for key pipeline steps (message receive, raw write, classify, retrieval, decision, extraction, apply, response send).

Current emitted metric hooks include:

- `squire_messages_total{entrypoint}`
- `squire_pipeline_failures_total{stage,error_type}`
- `squire_stage_duration_seconds{stage}`
- `squire_decision_outcomes_total{outcome}`
- `squire_pending_actions_total{status}`

### Derived Trace Artifacts

Squire writes durable decision/matching artifacts under archive-derived event paths, including matching trace payloads for update/append routing and gating outcomes.

## Target Observability Model (Planned)

The target model is defined in `docs/agent-harness-spec.md` and includes:

- structured JSON logs with correlation fields
- spans for key pipeline stages
- low-cardinality metrics for stage timing and outcomes
- local agent-queryable APIs through a local stack (Alloy + Loki + Tempo + Prometheus)
- collector-agnostic production export via OTLP + logs

## Correlation Model (Planned)

- `run_id` is used for logs, traces, and session artifacts.
- `run_id` should not be used as a metric label (cardinality risk).
- Metric analysis should use bounded time windows and stable labels (for example stage/outcome).

## Local Agent Query Strategy (Planned)

Agents should query telemetry by API, not UI dependency:

- logs: Loki query endpoints
- metrics: Prometheus query endpoints
- traces: Tempo trace query endpoints

Grafana is optional for human dashboards and ad hoc investigation.

## Production Strategy (Planned)

Deployments should remain collector-agnostic:

- if OTLP is configured, export telemetry to operator-managed collector/backend
- if OTLP is not configured, app still runs with baseline logging and health checks

## OTLP Export Controls (Implemented)

OTLP initialization is supported in runtime and controlled by:

- `observability.enabled` or `OTEL_ENABLED=true`
- `observability.otlp_endpoint` or `OTEL_EXPORTER_OTLP_ENDPOINT`
- `observability.otlp_headers` or `OTEL_EXPORTER_OTLP_HEADERS`

If observability is enabled but required OTLP configuration is missing, startup fails fast with an explicit runtime error.
OpenTelemetry Python packages are required runtime dependencies; OTLP backend/export configuration remains optional.

## Related Docs

- `docs/agent-harness-spec.md`
- `docs/agent-harness-runbook.md`
- `docs/deployment.md`
- `docs/configuration.md`
