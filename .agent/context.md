# Squire AI Context

## Purpose

- This file is a durable handoff for future fresh-context agents.
- Keep only need-to-know decisions, constraints, and loose ends that materially affect future work.

## Product Scope (Current)

- Primary runtime interface is Discord.
- Primary LLM provider is OpenAI.
- Natural-language command routing is in active use for read and mutation intents.
- High-level future ideas belong in `.agent/future-plans.md`.

## Core Invariants

- Raw events are immutable; derived artifacts are versioned; canonical objects are mutable source-of-truth.
- Explicit `!` commands have highest precedence.
- `!clear-archive` + `DELETE`, `!confirm`, and `!cancel` remain explicit-only controls.
- Mutation writes stay confirmation-first unless explicitly designed otherwise.
- Canonical object audit linkage (`source_event_ids`, `last_decision_id`) must be preserved.

## Durable Runtime Decisions

### Numbered Mutation UX

- `!done`, `!append`, and `!fix` support `<id|number>` targets.
- Numbered targets resolve from the latest numbered cursor in user/channel context.
- Cursor sources include `!recent`, `!find`, `!status`, and `!weekly`.
- Thread-parent fallback is implemented so numbered follow-ups from thread replies resolve correctly.

### Matching and Retrieval

- Hybrid lexical + semantic retrieval is active with conservative defaults.
- Deterministic auto-apply gates include minimum score and margin checks.
- When retrieval is degraded/unavailable, runtime follows safe fallbacks (no unsafe auto-apply).

### Test-Mode Startup

- `SQUIRE_ENV=test` runs destructive reset + deterministic seed + index rebuild.
- Guardrails require a test-safe archive path.
- `test_archive_root` can override `archive_root` only in test mode.
- Test seed now includes two `admin` notes with `due_at` (`TEST_ADMIN_DUE_AT_OPEN`, `TEST_ADMIN_DUE_AT_BLOCKED`) so reminder scheduling can be smoke-tested immediately.

### Due-Time Reminders

- Optional due-time reminders are implemented for `admin` items with `due_at` only.
- Eligibility is strict: `status in {open, blocked}` and `archived != true`; `due_date`-only items are excluded.
- Scheduling model is event-driven + reconcile:
  - startup queue build
  - local-midnight queue rebuild
  - event-driven queue rebuild after canonical write paths (`!done`/`!append`/`!fix`, confirm/apply flows, mutation buttons)
  - periodic full reconcile (`schedule.due_time_reminder_reconcile_minutes`, default `60`)
- Runtime sleeps until next fire time (no per-minute scan loop) and uses dedupe persistence across restarts via:
  - `paths.events_derived/runtime/due_time_reminder_sent_ledger_v1.json`
- If `schedule.due_time_reminder_offsets_minutes` is omitted, runtime defaults to offsets `(90, 15)`; set explicit `[]` to disable reminders.

## Natural-Language Routing (Current Contract)

- Runtime uses route schema `nl_route_intent_v1` and mutation schemas `nl_mutation_plan_v1` / `nl_mutation_normalized_v1`.
- Default natural-language routing prompt is `config/prompts/nl_command_routing_v1.txt`.

### Mutation Plan Behavior

- Mutation plans support multi-operation and multi-target requests.
- Runtime normalizes per target and assigns operation status:
  - `resolved`
  - `unresolved`
  - `cancelled_unresolved`
- Conflicting writes on the same target+field are marked `operation_conflict`.
- Pending actions are created from resolved operations only.
- Confirm/apply path supports mixed object-type batches (`pending.object_type = "mixed"`).

### Clarification Policy

- One-turn clarification only.
- Clarification scope is immutable and limited to unresolved operations.
- Out-of-scope clarification replies are blocked with required policy copy.
- If unresolved operations remain after the one clarification turn, they are cancelled.

### NL Routing Config Surface

- Active keys:
  - `enabled`
  - `clarify_on_ambiguous`
  - `allow_nl_mutations`
  - `plan_trace_enabled`
  - `read_auto_min_confidence`
  - `mutation_confirm_min_confidence`
  - `max_recent_limit`
- Removed keys (do not reintroduce without explicit decision):
  - `mutation_plan_enabled`
  - `plan_auto_aliasing`

## Multi-Transport Refactor (Current Decisions)

- Stage 0 scaffolding is complete:
  - `src/squire_core/transport/{__init__.py,contracts.py,state.py,bootstrap.py,health.py,commands.py,routing.py,reminders.py}`
  - `src/squire_core/transport/discord/{__init__.py,adapter.py,views.py,scheduler.py}`
  - `src/squire_core/transport/slack/{__init__.py,adapter.py,scheduler.py}`
- Stages 1-5 helper/state/command/routing/adapter extraction are complete; shared runtime logic now lives under `src/squire_core/transport/*` and Discord-specific behavior is isolated under `src/squire_core/transport/discord/*`.
- Stage 6 is complete (2026-02-22):
  - removed compatibility shim `src/squire_core/discord_bot.py`
  - canonical runtime entrypoint is `python -m squire_core.runtime` (`src/squire_core/runtime.py`)
  - runtime surfaces (`Makefile`, `Dockerfile`) and shim-dependent tests were migrated off `squire_core.discord_bot`
- Sequencing decision (2026-02-22): Slack adapter behavior remains out of scope for this staged refactor and should be revisited as follow-on work after Stage 6.
- Stage 7 is explicitly two-phase in `docs/multi-transport-refactor-spec.md`: Stage 7A (safe hygiene/inventory) -> Stage 8 (boundary hardening) -> Stage 7B (post-hardening orphan removals).
- Stage 7A is complete (2026-02-22):
  - removed low-risk unused imports in `src/squire_core/transport/routing.py` and `src/squire_core/transport/discord/scheduler.py`, plus unused exception aliases in `src/squire_core/runtime.py`
  - refreshed deferred-orphan inventory in `docs/multi-transport-refactor-spec.md` with rationale/confidence (contracts/state aliases + Slack scaffolds deferred to Stage 8/7B)
- Stage 8 boundary hardening is now tracked in `docs/multi-transport-refactor-spec.md` to complete original modularity intent (remove Discord coupling from root runtime/shared flow contracts).
- Transport-boundary rule is currently doc/review guidance (not CI-enforced import-lint).
- Validation baseline:
  - `.venv/bin/python -m pytest -q` currently passes except known sandbox socket-bind failures in `tests/test_health_server.py`.
  - Stage 6 cutover verification (2026-02-22): `py_compile` across `src/` + `tests/` passed; focused cutover suite passed (`73 passed`).

## Known Constraints and Loose Ends

- Clarification context is in-memory runtime state and does not persist across process restarts.
- Plan-size guardrails (max operations per plan / max targets per operation) are intentionally deferred for now.
  - Track this in `.agent/future-plans.md` under routing hardening.
- Due-time reminder scheduler assumes single-process runtime ownership of queue/ledger writes.
- Investigate daily status surfacing gap: admin items without `due_date` are reported as not surfacing reliably in daily status output.

## Canonical References

- Workflow rules: `AGENTS.md`
- Commands: `docs/commands.md`
- Configuration: `docs/configuration.md`
- Architecture/data model: `docs/architecture.md`, `docs/data-model.md`, `docs/modules.md`
- Natural-language routing spec and implementation reference:
  - `docs/nl-command-routing-spec.md`
  - `docs/nl-command-routing-implementation-plan.md`
