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

## Transport Refactor Intent

- During the multi-transport refactor, keep shared runtime/application modules transport-agnostic.
- Prefer limiting SDK imports (`discord.py`, Slack SDK, etc.) to transport adapter modules.
- Slack adapter/runtime behavior is out of scope for the staged refactor; revisit only after Stage 6 is complete (entrypoint cutover + `discord_bot.py` shim removal) per `docs/multi-transport-refactor-spec.md`.
- This is currently a design intention and review guideline, not a strict CI import-boundary gate.
- Treat `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py` as a temporary compatibility shim during staged extraction; target end-state removes it after adapter/runtime migration is complete.
- Runtime entrypoint direction for multi-transport support is `/Users/dblinkhorn/squire/src/squire_core/runtime.py` (typically invoked as `python -m squire_core.runtime`).

## Scope Discipline (Docs and Tests)

- Distinguish functional behavior changes from low-impact, minor changes.
- Treat these as low-impact unless requested otherwise: copy edits, minor UI/UX polish, incidental/stylistic code tweaks, and non-behavioral refactors.
- For low-impact changes, avoid adding special documentation callouts or dedicated regression tests unless explicitly requested.
- For low-impact changes, avoid recording "new behavior" notes in durable documentation; include only brief implementation notes if needed.
- Add/adjust tests for low-impact text or presentation changes only when contract-critical:
  - safety or policy language users must see exactly
  - machine-parsed/output-contract text
  - explicit user request to lock wording
- Keep docs focused on semantics and behavior, not exact phrasing or presentational/incidental details, unless they are part of the contract.

## Context Hygiene (`.agent/context.md`)

- Purpose: leave only durable, need-to-know handoff context for future fresh-context agents.
- Include only items that materially affect future work:
  - unresolved decisions, known loose ends, or intentional deferrals
  - non-obvious constraints, caveats, or gotchas
  - behavior-impacting changes that are not yet obvious from canonical docs/code
  - testing gaps that matter (for example skipped critical checks)
- Do not log routine/incidental details:
  - minor copy/UI/stylistic tweaks
  - exhaustive file-change summaries
  - normal “tests passed” notes unless they signal a risk or exception
- Prefer updating or replacing existing context entries over appending duplicates.
- Prune stale/resolved entries during each non-trivial session; delete notes that are no longer actionable or relevant.
- If information belongs in canonical docs (`docs/`, `README.md`, config docs), move it there and remove it from `.agent/context.md`.
- Keep entries compact and decision-focused (what matters, why it matters, what remains).

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
