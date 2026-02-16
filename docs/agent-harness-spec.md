# Agent Harness + Observability Spec

## Objective

Define an agent-first development harness for Squire that is:

- deterministic for code validation
- observable through machine-queryable logs/metrics/traces
- local-first for implementation loops
- collector-agnostic for production deployments

This spec is implementation-oriented so a fresh agent can execute it without prior chat context.

## Problem

Current Squire development has strong unit coverage and durable runtime artifacts, but it lacks a single harness workflow that lets an agent do this end-to-end in one repeatable loop:

1. bootstrap known state
2. run scenario(s)
3. validate behavior/contracts
4. inspect telemetry and traces programmatically

Current observability has JSON logs, stage metrics hooks, and `/health`, but still lacks a single agent-first query/assertion harness loop.

## Goals

- Provide one deterministic local harness command family for agents.
- Make telemetry queryable by agents without requiring a UI.
- Support dual deployment models:
  - local ephemeral o11y stack for dev/agent sessions
  - production export to user-managed collectors/cloud backends
- Reduce instruction entropy by using `AGENTS.md` as a table of contents and moving details into single-purpose docs.
- Add lightweight recurring drift checks for docs/contracts/dead-code candidates.

## Non-Goals

- Rewriting Squire runtime architecture.
- Requiring Grafana UI for local agent runs.
- Forcing a single production backend vendor.
- Auto-deleting code or auto-merging doc cleanup without review.

## Design Principles

- Determinism over convenience for validation paths.
- Ephemeral-by-default local telemetry; keep only short-lived session data.
- Open standards first (`OTLP`, structured JSON logs, HTTP query APIs).
- Single-source docs; avoid duplicated procedural instructions.
- Evidence-first agent assertions (query output + artifacts), not narrative-only claims.

## Proposed System

### 1) Knowledge Architecture (Entropy Control)

#### `AGENTS.md` as ToC

Keep root `AGENTS.md` short and navigational. It should point to canonical docs instead of carrying full policy text.

Planned structure:

- workflow and tracked files (`.agent/plan.md`, `.agent/context.md`, `.agent/scratchpad.md`)
- implementation contracts (`docs/configuration.md`, `docs/commands.md`, `docs/architecture.md`)
- active specs (`docs/*-spec.md`)
- observability and harness runbooks (new docs below)

#### New docs

- `docs/agent-harness-spec.md` (this doc): implementation contract
- `docs/agent-harness-runbook.md`: operator commands and troubleshooting
- `docs/observability.md`: telemetry schema and query examples

### 2) Deterministic Local Harness

Provide explicit make targets for agent workflows.

#### Make targets

- `make harness-bootstrap`
- `make harness-up`
- `make harness-run`
- `make harness-validate`
- `make harness-inspect`
- `make harness-down`
- `make harness` (orchestrates full lifecycle)

#### Harness run model

Each run has a unique `run_id` (ULID-like), propagated to logs/traces/artifacts.

Suggested run directory:

- `.agent/runs/<run_id>/`

Artifacts written:

- `summary.json`
- `assertions.json`
- `squire.log.jsonl`
- selected query results (`loki.json`, `prom.json`, `tempo.json`)
- optional diff bundles for failed checks

#### Two harness modes

- `deterministic` (default):
  - fixed `timezone` and fixed reference time (`SQUIRE_HARNESS_NOW`)
  - fixture-based provider responses (no live OpenAI dependency)
  - replay scenarios from checked-in fixtures
- `integration-smoke`:
  - runs live bot + test Discord server traffic
  - validates transport/integration health and end-to-end wiring

#### Session Gate Policy (Local Attestation Required)

For every implementation session that changes executable behavior, local session verification is mandatory.

Scope:

- required for changes in runtime/config/test code (for example `src/`, `config/`, `tests/`, `Makefile`, compose files)
- docs-only changes may skip live smoke but should record `skipped` with reason

Required command:

- `make verify-session`

Required checks inside `verify-session`:

- dependency sync/validation when dependency manifests changed in session scope
- deterministic harness checks (unit tests + deterministic replay)
- live Discord smoke run against dedicated test server/channel

Required local attestation artifact:

- `.agent/runs/<run_id>/session_gate.json`
- must include:
  - `run_id`
  - `git_sha`
  - `timestamp`
  - check results (`dependency_sync`, `deterministic`, `integration_smoke`)
  - overall `status`

Completion rule:

- agent must not declare implementation complete unless the latest attestation is `pass` for current `HEAD`
- bounded retries are allowed for transient smoke failures; after retries, status is `blocked` with evidence

Dependency policy within `verify-session`:

- if dependency manifests changed (`pyproject.toml`, lockfiles), run environment sync before checks
- fail fast if required dependencies for enabled features are missing

Future extension:

- CI may validate presence/freshness of local attestation and optionally run its own smoke gate

### 3) Observability Dual System

#### Local dev/agent profile (ephemeral)

Run a local stack with Docker Compose:

- Alloy (collector)
- Loki (logs)
- Prometheus (metrics)
- Tempo (traces)
- optional Grafana (UI only; not required for agent queries)

Agent access uses APIs directly:

- Loki: `/loki/api/v1/query` or `/loki/api/v1/query_range`
- Prometheus: `/api/v1/query`
- Tempo: trace query API

Retention defaults should be short (for example 4-24 hours).

#### Production profile (user-managed)

Squire exports telemetry using open interfaces only.

- traces/metrics via OTLP endpoint env vars
- structured JSON logs to stdout/stderr (for Docker collector tailing or ingestion)

If OTLP env vars are unset, Squire still runs with normal logging and no hard OTLP-backend dependency.

### 4) Telemetry Contract

#### Structured logs

Move runtime logs to JSON lines with stable fields:

- `ts`
- `level`
- `event`
- `message`
- `run_id`
- `raw_event_id` (when applicable)
- `discord_message_id` (when applicable)
- `pipeline_stage`
- `object_type`
- `decision_action`
- `retrieval_mode`
- `duration_ms`
- `error_type` / `error_message` (on failure)

Keep current stdout/stderr severity split.

#### Traces

Instrument major pipeline spans:

- `discord.message.receive`
- `event.raw.write`
- `classify`
- `candidate.retrieve`
- `decision.route`
- `interpret.extract`
- `operation.apply`
- `response.send`
- `matching.trace.write`

Required span attributes:

- `run_id`
- `raw_event_id`
- `object_type`
- `retrieval_mode`
- `decision_action`

#### Metrics

Add low-cardinality counters/histograms:

- `squire_messages_total{entrypoint}`
- `squire_pipeline_failures_total{stage,error_type}`
- `squire_stage_duration_seconds{stage}`
- `squire_decision_outcomes_total{outcome}`
- `squire_pending_actions_total{status}`

Notes:

- do not add `run_id` as a metric label (high cardinality risk)
- correlate metrics to a run via bounded time windows and stage/outcome labels

### 5) Agent Query + Assertions

Add a small query/assertion toolchain for automated checks.

Proposed scripts:

- `tools/harness/run_harness.py`
- `tools/harness/query_o11y.py`
- `tools/harness/assert_o11y.py`

Behavior:

- query logs/traces by `run_id`
- query metrics by run time window + stable labels
- evaluate pass/fail rules
- emit machine-readable report + short human summary

Example assertion set:

- zero `ERROR` logs for the run
- no failed spans for required stages
- `p95` `classify` and `interpret.extract` below configured threshold
- expected decision outcomes appear for fixture cases

### 6) Drift and Hygiene Automations

Define recurring checks that produce reviewable reports:

- `docs-drift` (daily): docs claims vs runtime/config/tests
- `stale-specs` (weekly): unimplemented or deprecated spec sections
- `dead-code-candidates` (weekly): unreferenced modules/keys/flags
- `harness-health` (daily): deterministic harness regression summary

Guardrails:

- report-only by default
- no destructive edits
- evidence required (`file`, `line`, command/query output)

## Configuration Additions

### Environment variables

- `SQUIRE_RUN_ID` (optional; generated when absent)
- `SQUIRE_ENV` (`dev`, `test`, `prod`)
- `OTEL_ENABLED` (`true`/`false`, default `false`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` (optional)
- `OTEL_EXPORTER_OTLP_HEADERS` (optional)
- `SQUIRE_HARNESS_MODE` (`deterministic` or `integration-smoke`)
- `SQUIRE_HARNESS_NOW` (ISO timestamp for deterministic mode)

### Config file additions (`config.yaml`)

```yaml
observability:
  enabled: false
  service_name: "squire-core"
  environment: "dev"
  log_level: "INFO"
  traces:
    enabled: true
  metrics:
    enabled: true
  local:
    run_id_header: "x-squire-run-id"
harness:
  default_mode: "deterministic"
  fixed_now: "2026-02-15T12:00:00+00:00"
  scenario_root: "tests/harness/scenarios"
  artifact_root: ".agent/runs"
```

## Implementation Plan

### Phase 0: Spec + doc skeleton

- Add this spec and linked runbook docs.
- Refactor `AGENTS.md` into ToC style with links.
- Update `.agent/context.md` with durable rationale.

### Phase 1: Logging + telemetry scaffolding

Files:

- `src/squire_core/discord_bot.py`
- `src/squire_core/observability.py` (new)
- `pyproject.toml` (otel deps)
- `tests/test_observability.py` (new)

Deliverables:

- JSON logger wrapper with correlation context
- basic span + metric hooks around core stages
- regression tests for log structure and run_id propagation

### Phase 2: Local o11y stack + harness scripts

Files:

- `docker-compose.o11y.local.yml` (new)
- `config/observability/alloy-local.river` (new)
- `tools/harness/run_harness.py` (new)
- `tools/harness/query_o11y.py` (new)
- `tools/harness/assert_o11y.py` (new)
- `Makefile`

Deliverables:

- `make harness-*` and `make o11y-*` targets
- `make verify-session` target producing `session_gate.json`
- ephemeral run directories and reports
- query-by-run_id assertions
- automated dependency-sync + dependency-validation step integrated in `verify-session`

#### Phase 2 Execution Contract (Normative)

This section is binding for implementation details in Phase 2 so a fresh agent does not need to invent conventions.

##### Required local services (compose profile)

- `alloy`: OTLP ingress and fanout pipeline.
- `loki`: log storage + query API.
- `tempo`: trace storage + search/query API.
- `prometheus`: metrics storage + query API.
- `grafana` (optional): UI only, not required for automated checks.

Default host ports:

- `4317` OTLP gRPC ingress (Alloy)
- `4318` OTLP HTTP ingress (Alloy)
- `3100` Loki HTTP API
- `3200` Tempo HTTP API
- `9090` Prometheus HTTP API
- `3000` Grafana UI (optional)

Required files:

- `docker-compose.o11y.local.yml`
- `config/observability/alloy-local.river`
- `config/observability/prometheus.local.yml`
- `config/observability/tempo.local.yml`

Retention defaults:

- local o11y data retention must default to `<= 24h`
- all local o11y state must be disposable via `make o11y-down` (ephemeral dev profile)

##### Alloy pipeline contract

Alloy must accept OTLP from Squire and route signals as follows:

- logs -> Loki push API
- traces -> Tempo OTLP ingest
- metrics -> Prometheus remote write receiver

Required signal attributes/labels:

- preserve JSON log body fields, including `run_id`
- include stable stream metadata (`stdout`/`stderr`) when available
- do not promote `run_id` to a Prometheus metric label

##### Make target contract

Required targets and behavior:

- `make harness-bootstrap`
  - creates `.agent/runs/<run_id>/`
  - writes `.agent/runs/<run_id>/run.env` with `SQUIRE_RUN_ID`, `SQUIRE_ENV`, `SQUIRE_HARNESS_MODE`, `SQUIRE_HARNESS_NOW`
  - default `SQUIRE_ENV=dev` unless explicitly overridden
- `make harness-up`
  - starts `docker compose -f docker-compose.o11y.local.yml up -d`
  - blocks until Loki/Tempo/Prometheus health checks pass or timeout
- `make harness-run`
  - executes deterministic checks and optional integration-smoke path based on mode
  - starts bot with the run env file (`SQUIRE_RUN_ID` required)
- `make harness-validate`
  - evaluates assertions and writes `assertions.json`
- `make harness-inspect`
  - fetches/stores raw query results (`loki.json`, `prom.json`, `tempo.json`)
- `make harness-down`
  - stops local o11y stack and removes ephemeral volumes for the local profile
- `make verify-session`
  - runs dependency validation/sync gate when manifests changed
  - runs harness lifecycle needed for current change scope
  - writes `session_gate.json`

##### Query API contract

`tools/harness/query_o11y.py` must provide one command per signal family and return JSON.

Required query endpoints:

- Loki: `http://127.0.0.1:3100/loki/api/v1/query_range`
- Prometheus: `http://127.0.0.1:9090/api/v1/query`
- Tempo: `http://127.0.0.1:3200/api/search` (or equivalent Tempo search endpoint configured in compose)

Required baseline queries:

- logs by `run_id` over run window
- error logs count over run window
- trace search by `run_id`
- failure metric increase over run window:
  - `sum(increase(squire_pipeline_failures_total[<window>]))`
- stage p95 latency:
  - `histogram_quantile(0.95, sum by (le, stage) (rate(squire_stage_duration_seconds_bucket[5m])))`

##### Artifact schema contract

Required artifact files in `.agent/runs/<run_id>/`:

- `summary.json`
- `assertions.json`
- `session_gate.json`
- `loki.json`
- `prom.json`
- `tempo.json`

Required `summary.json` keys:

- `run_id`
- `git_sha`
- `mode`
- `started_at`
- `finished_at`
- `duration_ms`
- `status`
- `artifacts` (map of artifact filenames to relative paths)

Required `assertions.json` keys:

- `run_id`
- `window` (`start`, `end`)
- `checks` (array)
- `status` (`pass`, `failed`, `blocked`)

Each check entry must include:

- `id`
- `description`
- `status`
- `evidence_refs` (artifact-relative paths or query identifiers)

Required `session_gate.json` keys:

- `run_id`
- `git_sha`
- `timestamp`
- `checks.dependency_sync.status`
- `checks.deterministic.status`
- `checks.integration_smoke.status`
- `status` (`pass`, `failed`, `blocked`)

##### Assertion + status semantics

Minimum required assertions:

- zero error logs for deterministic runs
- zero increase in `squire_pipeline_failures_total` during deterministic run window
- required stages appear in traces for exercised flows
- integration smoke run proves Discord receive + response stages executed at least once

Status mapping:

- `pass`: all required checks pass
- `failed`: deterministic assertions fail or required artifacts are missing
- `blocked`: external dependency/system prevented evaluation (Discord outage, local stack unavailable, credential missing)

##### Retry and timeout policy

Required defaults:

- o11y stack startup timeout: `120s`
- deterministic run timeout: `10m`
- integration smoke timeout: `8m`
- integration smoke retries: max `2` retries for transient transport failures

After retry budget exhaustion:

- mark `integration_smoke` as `blocked`
- include last error evidence in `summary.json` and `session_gate.json`

### Phase 3: Deterministic scenario fixtures

Files:

- `tests/harness/scenarios/*.yaml` (new)
- `tests/harness/fixtures/*.json` (new)
- `src/squire_core/llm/harness_provider.py` (new)
- `tests/test_harness_runner.py` (new)

Deliverables:

- deterministic replay cases for create/update/append and surfacing
- expected decision/matching outcomes checked in

### Phase 4: Production collector-agnostic docs

Files:

- `docs/deployment.md`
- `docs/configuration.md`
- `README.md`
- `docs/observability.md` (new)

Deliverables:

- clear operator guidance for local-only, cloud, and self-hosted collector setups
- explicit note that Grafana UI is optional for agents

## Acceptance Criteria

- Agent can run one command (`make harness`) and receive machine-readable pass/fail output.
- Deterministic harness runs are reproducible across two invocations on same commit.
- Local telemetry queries work without Grafana UI.
- `run_id` correlates logs, traces, and harness artifacts.
- `make verify-session` writes `.agent/runs/<run_id>/session_gate.json` with pass/fail and commit SHA.
- `session_gate.json` includes explicit `dependency_sync` check result.
- For executable-behavior changes, a passing local session gate is required before task completion.
- Squire still runs when observability is disabled.
- Production deploys can forward telemetry to arbitrary OTLP-compatible collectors.

## Risks and Mitigations

- Risk: high-cardinality labels degrade metrics/log performance.
  - Mitigation: keep labels low-cardinality; place details in payload fields.
- Risk: PII leakage in exported telemetry.
  - Mitigation: default redaction policy and explicit guidance for production exporters.
- Risk: harness flakiness from network/Discord/OpenAI.
  - Mitigation: deterministic + smoke both required in local gate, with bounded retries and explicit blocked status after retry budget is exhausted.
- Risk: doc drift over time.
  - Mitigation: recurring `docs-drift` automation and ToC-style doc ownership.

## Initial Decisions

- Local dev telemetry is ephemeral-by-default.
- Grafana UI is optional for local harness; agents use HTTP query APIs directly.
- Logs remain first-class and structured; traces/metrics are additive.
- Production observability is collector-agnostic and opt-in by configuration.
- Local implementation-session enforcement is attestation-first (`session_gate.json`); CI enforcement is planned follow-on.
