# Squire Agent Workflow (ToC)

This file is the workflow entrypoint and table of contents for agent sessions.

## Core Workflow Rules

- Keep this file at repo root as the canonical workflow index.
- For non-trivial work, keep `.agent/plan.md` short and current.
- Record durable context in `.agent/context.md`.
- Record high-level future ideas in `.agent/future-plans.md`.
- Use `.agent/scratchpad.md` only for temporary local notes.
- Keep docs aligned with behavior when functionality changes (`docs/` and `README.md` if user-facing).
- For Python work, follow standard Python conventions unless project docs explicitly say otherwise.

## Architecture Principles

- Clear boundaries: modules map to one capability and expose small stable interfaces.
- Single responsibility: each module should have one reason to change.
- High cohesion, low coupling: keep related logic together; avoid cross-module internal reach.
- Unidirectional dependencies: avoid cycles; domain logic should not depend on transport/storage/framework details.
- Separate core logic from I/O: keep domain rules independent from transport, storage, and framework concerns.
- Prefer pure functions where practical: core logic should transform inputs to outputs without touching network, disk, database, clock, env vars, or global state.
- Isolate I/O at boundaries: keep side effects in adapter/edge modules and pass data into core logic as explicit inputs.
- Design to interfaces: prefer contracts/abstractions over concrete dependencies.
- Composition over inheritance: assemble small components rather than deep type hierarchies.
- Explicit state and flow: make mutations visible; avoid hidden global side effects.
- Validate at boundaries: enforce schema/config/input constraints early and fail fast.
- Stable contracts: prefer additive, backward-compatible API/schema evolution.
- Optimize for testability: core decisions should be testable without full runtime boot.
- Observability by default: structured logs, trace context, and stage metrics for new behaviors.
- Dependency injection where useful: inject external services (LLM, storage, clock, transport) instead of hardwiring.
- Localize complexity: keep intricate rules behind narrow APIs.

## Dependency Sync Policy

- If dependency manifests change (`pyproject.toml`, lockfiles), sync the environment before tests:
  - preferred: `uv sync`
  - fallback: `pip install -e ".[dev]"`
- Fail fast when required dependencies are missing for enabled features.
- Do not mark implementation complete if dependency sync/validation has not been run after dependency changes.

## Session Startup Checklist

1. Read `README.md`.
2. Read relevant docs in `docs/` before implementing.
3. Check `.agent/context.md` for durable decisions and recent changes.
4. If work is non-trivial, update `.agent/plan.md` before editing.

## Implementation Gates

- Current baseline gate: run relevant tests (`make test` at minimum) and report gaps if any checks are skipped.
- Planned gate (defined in `docs/agent-harness-spec.md`): `make verify-session` with local attestation artifact.

## Canonical References

- Product/runtime references:
  - `docs/architecture.md` - end-to-end system flow, trust model, and mutation pipeline.
  - `docs/modules.md` - module responsibilities and boundaries.
  - `docs/commands.md` - user command contracts and expected behaviors.
  - `docs/configuration.md` - env/config keys and runtime configuration behavior.
  - `docs/deployment.md` - runtime startup/deployment and operational setup.
  - `docs/data-model.md` - canonical/index data structures and storage contracts.
  - `docs/surfacing.md` - implemented digest/review/list surfacing behavior.
  - `docs/querying.md` - implemented query/search behavior and limits.
- Active implementation specs:
  - `docs/*-spec.md` - detailed planned work with scope, phased implementation, and acceptance criteria. Read the specs relevant to the files/behavior you are changing.
- Harness and telemetry docs:
  - `docs/agent-harness-runbook.md` - execution runbook for current vs target harness workflows.
  - `docs/observability.md` - current signals and target telemetry model.
