# Squire Agent Workflow

This file is the workflow entrypoint and index for agent sessions.

## Core Workflow Rules

- Keep this file at repo root as the canonical workflow index.
- For non-trivial work, keep `.agent/plan.md` short and current.
- Record durable context in `.agent/context.md`.
- Record high-level future ideas in `.agent/future-plans.md`.
- Use `.agent/scratchpad.md` only for temporary local notes.
- If a task is ambiguous, ask clarifying questions early.
- Keep docs aligned with behavior when functionality changes (`docs/` and `README.md` if user-facing).
- For Python work, follow standard Python conventions unless project docs explicitly say otherwise.

## Session Startup Checklist

1. Read `README.md`.
2. Read relevant docs in `docs/` before implementing.
3. Check `.agent/context.md` for durable decisions and recent changes.
4. If work is non-trivial, update `.agent/plan.md` before editing.

## Task-to-Doc Routing

Use this section to decide which docs are relevant for the current task.

- Runtime behavior or flow changes:
  - `docs/architecture.md`, `docs/modules.md`, `docs/data-model.md`
- Discord command behavior or UX text:
  - `docs/commands.md`, `docs/surfacing.md`, `docs/querying.md`
- Config/env/default changes:
  - `docs/configuration.md`
- Deployment/runtime startup/operations:
  - `docs/deployment.md`
- Surfacing/digest or review behavior:
  - `docs/surfacing.md`
- Extensibility or integration direction:
  - `docs/extensibility.md`

## Canonical Docs Index

- `README.md` - Product overview, setup, commands, and local run/test basics.
- `docs/architecture.md` - End-to-end system flow and trust/update model.
- `docs/modules.md` - Responsibilities and boundaries for core modules.
- `docs/data-model.md` - Raw/derived/canonical/pending data contracts and fields.
- `docs/commands.md` - Implemented Discord commands and command semantics.
- `docs/querying.md` - Current query/search flow and limits (`!find`/`!show`).
- `docs/surfacing.md` - Implemented digest/review/list surfacing behavior.
- `docs/configuration.md` - Environment variables and `config.yaml` options.
- `docs/deployment.md` - Deployment and startup behavior.
- `docs/extensibility.md` - Current extensibility limits and future direction.

## Implementation Gate

- Run relevant tests before handoff (`make test` at minimum).
- If dependency manifests changed (`pyproject.toml`, lockfiles), sync environment first:
  - preferred: `uv sync`
  - fallback: `pip install -e ".[dev]"`
- If checks are skipped, call out exactly what was skipped and why.
