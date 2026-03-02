# Squire AI Context

## Purpose

- This file is a durable handoff for future fresh-context agents.
- Keep only need-to-know decisions, constraints, and loose ends that materially affect future work.

## Product Scope (Current)

- Primary runtime interface is Discord.
- Runtime uses one active LLM provider selected by `config.yaml` (`llm.provider`, `llm.model`); current implemented provider support is OpenAI.
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
- Semantic retrieval can use `matching.semantic_provider` + `matching.semantic_model`; when `semantic_provider` is omitted it defaults to `llm.provider`.
- Runtime now threads a dedicated embedding provider through startup semantic sync and command/mutation index refresh flows (separate from primary interpret provider).
- If semantic provider init/probe fails at startup, semantic matching is auto-disabled with warning and runtime falls back to lexical-only matching.
- Deterministic auto-apply gates include minimum score and margin checks.
- When retrieval is degraded/unavailable, runtime follows safe fallbacks (no unsafe auto-apply).

### LLM Config Validation

- `llm.provider` and `llm.model` are required config keys; startup fails fast when either is missing/empty.
- Legacy `llm.interpreter_model` fallback is removed.
- `matching.semantic_model` is required when `matching.semantic_weight > 0`; startup fails fast if missing/empty.
- `matching.semantic_provider` is optional; when provided it must be a non-empty string and is normalized to lowercase.

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
- Stages 1-5 helper/state/command/routing/adapter extraction are complete; shared runtime logic now lives under `src/squire_core/transport/*` and Discord-specific behavior is isolated under `src/squire_core/transport/discord/*`.
- Stage 6 is complete (2026-02-22):
  - removed compatibility shim `src/squire_core/discord_bot.py`
  - canonical runtime entrypoint is `python -m squire_core.runtime` (`src/squire_core/runtime.py`)
  - runtime surfaces (`Makefile`, `Dockerfile`) and shim-dependent tests were migrated off `squire_core.discord_bot`
- Sequencing decision: Slack adapter behavior remains out of scope for this staged refactor and is tracked as separate follow-on work (not a remaining step in this refactor sequence).
- Stage 7 is explicitly two-phase in `docs/multi-transport-refactor-spec.md`: Stage 7A (safe hygiene/inventory) -> Stage 8 (boundary hardening) -> Stage 7B (post-hardening orphan removals).
- Stage 7A is complete (2026-02-22):
  - removed low-risk unused imports in `src/squire_core/transport/routing.py` and `src/squire_core/transport/discord/scheduler.py`, plus unused exception aliases in `src/squire_core/runtime.py`
  - refreshed deferred-orphan inventory in `docs/multi-transport-refactor-spec.md` with rationale/confidence
- Stage 8 is complete (2026-02-22):
  - `src/squire_core/runtime.py` is now transport-agnostic; current launch delegation resolves to `src/squire_core/transport/discord/runtime.py` (via Phase-4 inversion), with `flow.py` retained only as a transitional compatibility module.
  - Discord message lifecycle and orchestration remain in transition from `src/squire_core/transport/discord/flow.py` toward narrower Discord runtime/adapter modules.
  - shared command/routing contracts now use `TransportMessageContext` in `src/squire_core/transport/{commands.py,routing.py}`
  - tests importing runtime internals were migrated to `squire_core.transport.discord.flow` to keep runtime thin while preserving behavior assertions
  - added adapter contract-bridge coverage in `tests/test_discord_contract_bridge.py` for command and NL routing context translation
- Stage 7B is complete (2026-02-23) for non-Slack scope:
  - removed confirmed orphan symbols from `src/squire_core/transport/contracts.py`: `TransportIO`, `SendTextFn`, `AddReactionFn`, `SendPendingControlsFn`, `CommandResult`, `RouteResult`
  - removed confirmed orphan symbols from `src/squire_core/transport/state.py`: `NLRouteIntentV1`, `get_result_cursor`, `get_archive_clear_confirmation`
  - verification evidence: repo symbol scan + focused suite passed (`56 passed`) across transport/Discord-bridge/NL routing command tests
- Follow-on modularization spec added (2026-02-23):
  - `docs/transport-modularity-hardening-spec.md` defines post-Stage-8 extraction to reach truly pluggable transport boundaries.
  - explicit end-state decision: remove `src/squire_core/transport/discord/flow.py` after phased extraction; do not preserve it as a permanent architecture file.
  - preferred execution is one PR with phase-gated validation checkpoints.
- Modularity hardening Phase 1 helper extraction is complete (2026-02-23):
  - added shared modules `src/squire_core/transport/{validation.py,targeting.py,tracing.py}` as canonical sources for `!fix` validation, cursor/target resolution, and trace writing/payload helpers.
  - rewired `src/squire_core/transport/discord/flow.py` and `src/squire_core/transport/routing.py` to consume those modules; removed duplicated local helper implementations while preserving compatibility aliases still referenced by tests.
  - focused hardening suite passed: `56 passed` (`tests/test_transport_commands.py`, `tests/test_transport_routing.py`, `tests/test_discord_contract_bridge.py`, `tests/test_discord_commands.py`, `tests/test_nl_command_routing_config.py`, `tests/test_nl_mutation_normalization.py`, `tests/test_nl_multi_operation_clarification.py`).
- Modularity hardening Phase 2 extraction is complete (2026-02-23):
  - added shared modules `src/squire_core/transport/{matching_pipeline.py,mutations.py}` for matching/decision helper orchestration and shared mutation/index-sync helpers.
  - rewired `src/squire_core/transport/discord/flow.py` to delegate Phase-2 logic through these modules while keeping compatibility wrappers for test seams (`_apply_command_operation`, `_refresh_index`, `_refresh_index_async`).
  - added seam tests `tests/test_transport_{matching_pipeline,mutations}.py`; focused hardening suite now passes `67 passed`.
- Modularity hardening Phase 3 extraction is complete (2026-02-23):
  - added shared inbound orchestration module `src/squire_core/transport/inbound.py` for non-command capture/interpret/matching/apply flow.
  - rewired `src/squire_core/transport/discord/flow.py::_handle_message` to delegate non-command processing through `_DiscordInboundRuntime` + `transport.inbound.handle_non_command_message`.
  - added shared inbound unit tests `tests/test_transport_inbound.py`; focused hardening suite now passes `69 passed`.
- Phase 4/5 migration outcomes (2026-02-23):
  - `src/squire_core/transport/discord/{context.py,io.py,runtime.py}` introduced and runtime startup ownership moved under Discord runtime.
  - legacy `src/squire_core/transport/discord/flow.py` removed.
  - follow-on cleanup removed transitional `src/squire_core/transport/discord/orchestration.py`; Discord ingress now lives in `src/squire_core/transport/discord/message_entry.py` and runtime delegates directly there.
- Transport-boundary rule is currently doc/review guidance (not CI-enforced import-lint).
- Validation baseline:
  - `.venv/bin/python -m pytest -q` currently passes except known sandbox socket-bind failures in `tests/test_health_server.py`.
  - Stage 8 hardening verification (2026-02-22): `py_compile` across `src/` + `tests/` passed; focused Stage-8 suite passed (`80 passed`).

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
- Strict modularity seam migration complete (2026-02-23):
  - adapter runtime seams moved off transitional orchestration dependencies onto canonical transport/discord modules.
  - Added `src/squire_core/transport/discord/command_contract.py` for command help/copy/constant contract + helper functions consumed by adapters.
  - Removed dead compatibility wrappers (`_refresh_index`, `_refresh_index_async`, `_apply_command_operation`).
  - Discord-focused tests were migrated off orchestration monkeypatch seams to canonical seams (`transport.*`, adapter modules, `discord.io`) while preserving behavior parity.
  - Focused validation baseline after seam migration: `72 passed` (`tests/test_discord_commands.py`, `tests/test_discord_contract_bridge.py`, `tests/test_discord_schedule.py`, `tests/test_nl_multi_operation_clarification.py`, `tests/test_surfacing_cursor.py`, `tests/test_test_mode_startup.py`, `tests/test_nl_mutation_normalization.py`, `tests/test_transport_routing.py`).
- Modularity hardening follow-on implemented (2026-02-23):
  - Runtime composition root decoupled from Discord adapter import: `src/squire_core/runtime.py` now delegates via `src/squire_core/transport/runtime_registry.py` (`SQUIRE_TRANSPORT`, default `discord`).
  - Removed Discord naming compatibility alias `SquireBot` from adapter exports; canonical class is `DiscordSquireBot`.
  - Added shared archive-clear confirmation helpers in `src/squire_core/transport/archive_clear.py`; Discord command runtime now uses this shared seam.
  - Added `RuntimeStateStore` in `src/squire_core/transport/state.py` and switched active Discord runtime path to injected runtime-state instances:
    - `src/squire_core/transport/discord/runtime.py` now creates a process-scoped runtime state store and injects it into ingress handling.
    - `src/squire_core/transport/discord/message_entry.py` now threads that state store through command/routing/inbound adapter operations.
    - cursor/affinity/archive-clear/clarification state writes now execute through injected store seams rather than module-level dict references.
  - Removed hidden reminder notifier config-key coupling:
    - `src/squire_core/transport/reminders.py` now exposes explicit notifier invocation helper (`invoke_due_time_reminder_notifier`) instead of config key storage/lookup.
    - `src/squire_core/transport/discord/runtime.py` now injects scheduler notifier callback explicitly into ingress handling.
    - `src/squire_core/transport/discord/message_entry.py` now propagates notifier callback through command/routing/inbound runtimes.
    - transport protocols in `src/squire_core/transport/{commands.py,inbound.py,mutations.py,routing.py}` now call notifier methods without config-key coupling.
  - Added durable reference+plan doc: `docs/modularity-gap-audit-and-remediation-plan.md` with phased remediation.
  - Removed `src/squire_core/transport/discord/orchestration.py` and migrated remaining Discord tests to canonical seams (`message_entry`, `transport.commands`, `transport.routing`, `transport.state`, `transport.targeting`, `transport.discord.command_contract`, `transport.discord.views`).
- Validation baseline for this follow-on:
  - `89 passed` across focused modularity/Discord + runtime registry + shared transport suites.
  - known sandbox constraint unchanged: health endpoint socket-bind tests fail with `PermissionError` in restricted environments.
  - additional shared-module focused checks remain green (`10 passed` across inbound/mutations/matching/tracing/validation tests).
- Reminder notifier contract hardening complete (2026-02-23):
  - Config-key notifier contract removed from active runtime path.
  - Due-time reminder schedule updates now use explicit callback injection from Discord scheduler/runtime composition into runtime adapters.
- Modularity hardening Phase E complete (2026-02-23):
  - split Discord runtime adapters into focused modules:
    - `src/squire_core/transport/discord/runtime_adapter_command.py`
    - `src/squire_core/transport/discord/runtime_adapter_routing.py`
    - `src/squire_core/transport/discord/runtime_adapter_inbound.py`
  - removed adapter hub file `src/squire_core/transport/discord/runtime_adapters.py`; runtime/message entry/tests now import direct adapter modules.
  - added adapter-shared utilities in `src/squire_core/transport/discord/runtime_adapter_utils.py`.
- Runtime-state fallback removal complete (2026-02-23):
  - removed `DEFAULT_RUNTIME_STATE` and module fallback activation from `src/squire_core/transport/state.py`; state helper APIs now require explicit `RuntimeStateStore`.
  - `tests/conftest.py` now exposes an explicit per-test `runtime_state` fixture instead of monkeypatching singleton globals.
  - `tests/test_discord_commands.py` no longer references global singleton state.
  - validation baseline unchanged: focused modular suite `66 passed`; full suite remains `142 passed, 2 failed` (known sandbox socket-bind failures in `tests/test_health_server.py`).
- Strict DI hardening complete (2026-02-23):
  - removed Discord runtime-path fallback to `DEFAULT_RUNTIME_STATE`; runtime state is now explicitly injected through `message_entry` and adapter constructors.
  - removed runtime adapter re-export surface from `src/squire_core/transport/discord/runtime.py` (`__all__` now entrypoints only).
  - removed adapter-to-adapter construction inside adapter classes:
    - routing adapter now accepts injected command-runtime factory
    - inbound adapter now accepts injected routing-runtime factory
    - factories are composed in `src/squire_core/transport/discord/message_entry.py`
  - validation baseline unchanged: focused suite `83 passed`; full suite `142 passed, 2 failed` (sandbox bind restriction in health tests).
- Slack scaffolding cleanup complete (2026-02-24):
  - removed accidental Slack placeholder package `src/squire_core/transport/slack/`.
  - removed `Source.slack` from `src/squire_core/raw_event.py`.
  - runtime registry test now asserts unknown transport rejection without Slack-specific coupling.
  - current runtime remains Discord-only; any Slack integration should start as a fresh implementation after refactor completion.
- Compatibility-surface/import cleanup complete (2026-02-24):
  - removed unused imports in `src/squire_core/transport/routing.py` and `tests/test_discord_commands.py`.
  - trimmed package-level re-export scaffolding in `src/squire_core/transport/discord/__init__.py`; callers now import concrete Discord modules directly.
