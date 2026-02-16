# Agent Harness Runbook

## Purpose

Provide operational guidance for agent implementation sessions using the harness and observability model defined in `docs/agent-harness-spec.md`.

This runbook is the execution companion to the spec.

## Status

Current state in this repository:

- `docs/agent-harness-spec.md` is implemented as the planning contract.
- Initial Phase 2 harness targets are implemented:
  - `make harness-bootstrap`, `make harness-up`, `make harness-run`, `make harness-inspect`,
    `make harness-validate`, `make harness-down`, `make harness`, `make verify-session`,
    plus `make o11y-up` / `make o11y-down`.
- Runtime currently has:
  - unit tests (`make test`)
  - health endpoint (`GET /health`)
  - structured JSON logging with `run_id` correlation
  - stage timing/failure metric hooks for the Discord pipeline
  - structured derived matching trace artifacts in archive data

## Session Workflow (Harness)

Use this flow for implementation sessions:

1. `make harness-bootstrap`
2. `make harness-up`
3. `make harness-run`
4. `make harness-inspect`
5. `make harness-validate`
6. `make harness-down`

Default gate for executable-behavior changes:

1. `make verify-session`
2. inspect `.agent/runs/<run_id>/session_gate.json`
3. record any `blocked` evidence before handoff

`make test` is still useful as a quick pre-check, but it is not the session gate.

## Known-Good Smoke Flow

Use this copy/paste sequence to verify local o11y ingestion and assertions:

1. `RUN_ID="run_o11y_smoke_$(date +%s)"`
2. `make harness-bootstrap RUN_ID="$RUN_ID" mode=deterministic env=dev`
3. `make o11y-up RUN_ID="$RUN_ID"`
4. `.venv/bin/python tools/harness/run_harness.py emit-telemetry --run-id "$RUN_ID" --environment dev`
5. `make harness-inspect RUN_ID="$RUN_ID"`
6. `make harness-validate RUN_ID="$RUN_ID"`
7. `cat ".agent/runs/$RUN_ID/assertions.json"`

Expected result:

- all checks `pass`
- overall `"status": "pass"` in `assertions.json`

## Session Workflow (Fallback Baseline)

Use this when local o11y stack is unavailable:

1. Validate startup prerequisites (`config.yaml`, `.env`, Discord/OpenAI credentials as needed).
2. If dependency manifests changed (`pyproject.toml`, lockfiles), sync environment first:
   - preferred: `uv sync`
   - fallback: `pip install -e ".[dev]"`
3. Run test baseline:
   - `make test`
4. For integration sanity checks, optionally run bot locally:
   - `make run-bot env=dev` (or `env=test` / `env=prod`)
   - either set `observability.log_level: "DEBUG"` in `config.yaml`, or run `make run-bot ... log_level=DEBUG` for one-off debug visibility
5. If running live smoke manually, use a dedicated Discord test server/channel only.
6. Record outcome, skipped steps, and blockers in session summary response.

Expected result from harness workflow:

1. run artifacts under `.agent/runs/<run_id>/`
2. local gate attestation at `.agent/runs/<run_id>/session_gate.json`

## Phase 2 Implementation Defaults

Use these defaults when implementing Phase 2 unless the user explicitly requests alternatives.

- Local stack command:
  - `docker compose -f docker-compose.o11y.local.yml up -d`
- Services:
  - `alloy`, `loki`, `tempo`, `prometheus`
  - Grafana UI is intentionally deferred for now.
- Ports:
  - base defaults are Alloy OTLP gRPC `4317`, OTLP HTTP `4318`, Loki `3100`, Tempo `3200`, Prometheus `9090`
  - harness bootstrap now allocates a run-scoped free port bundle and writes it to `.agent/runs/<run_id>/run.env`
  - harness commands reuse that same run-scoped bundle by default
- Compose project isolation:
  - harness bootstrap writes `SQUIRE_O11Y_PROJECT` into `run.env`
  - `harness-up/down` and lifecycle commands reuse that compose project to avoid cross-run collisions
- Harness run env defaults:
  - `SQUIRE_ENV=dev`
  - unique `SQUIRE_RUN_ID` per run
  - deterministic mode includes fixed `SQUIRE_HARNESS_NOW`
- Required artifacts:
  - `.agent/runs/<run_id>/summary.json`
  - `.agent/runs/<run_id>/assertions.json`
  - `.agent/runs/<run_id>/session_gate.json`
  - `.agent/runs/<run_id>/loki.json`
  - `.agent/runs/<run_id>/prom.json`
  - `.agent/runs/<run_id>/tempo.json`
- Required status outputs:
  - `pass`, `failed`, or `blocked` (no additional status values)
- Retry policy:
  - integration smoke max `2` retries, then mark `blocked` with evidence

## Local Attestation Contract (Target)

`session_gate.json` should include:

- `run_id`
- `git_sha`
- `timestamp`
- `checks.dependency_sync.status`
- `checks.deterministic.status`
- `checks.integration_smoke.status`
- `status` (`pass`, `blocked`, or `failed`)
- `notes` (optional)

## Smoke Test Guardrails

- Use dedicated test Discord resources (never production channels).
- Bound retries for transient failures.
- On repeated failure, mark gate status as `blocked` and provide evidence.
- Do not treat smoke-only success as a substitute for deterministic checks.

## Handoff Expectations

When handing off implementation work, include:

- what checks ran
- what checks were skipped (with reason)
- where artifacts/log evidence are located
- whether the gate passed, failed, or is blocked
