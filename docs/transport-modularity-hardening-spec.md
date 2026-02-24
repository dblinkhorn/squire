# Transport Modularity Hardening Spec (Post-Stage-8)

## Purpose

Define an implementation-ready follow-on refactor to finish transport modularization so the codebase is genuinely pluggable for future integrations.

This spec supersedes the transitional role of `/Users/dblinkhorn/squire/src/squire_core/transport/discord/flow.py` and treats it as temporary migration scaffolding, not an end-state module.

## Problem Statement

After Stage 8, major progress exists (shared command/routing modules, runtime cutover, adapter split), but `/Users/dblinkhorn/squire/src/squire_core/transport/discord/flow.py` still mixes:

1. Discord-specific adapter behavior (SDK objects, IO, startup wiring)
2. Shared app behavior (inbound classify/decision/extract/apply pipeline)
3. Shared mutation and trace behavior that should be reusable across transports
4. Legacy duplicated helpers already canonicalized in shared modules

This prevents truly additive transport implementation and keeps high coupling in one oversized module.

## Design Goals

1. Remove `flow.py` as a structural concept in the end state.
2. Ensure shared runtime behavior is transport-agnostic and reusable.
3. Keep all transport SDK imports and UI semantics in adapter directories only.
4. Make transport runtime entrypoints thin wiring modules.
5. Preserve all current Discord behavior and user-facing semantics.
6. Allow a fresh-context implementer to execute this in one PR with clear phase gates.

## Non-Goals

1. Implement additional transport runtime behavior in this workstream (for example Slack).
2. Redesign user command semantics or NL policy semantics.
3. Change canonical storage/index schemas.
4. Introduce compatibility aliases as permanent architecture.

## Decision: One PR vs Multiple PRs

One PR is acceptable for this follow-on if phase gates are enforced inside the PR and every phase is validated before proceeding.

Why this can work:

1. The work is tightly coupled and centered on one migration seam (`flow.py` removal).
2. Piecemeal partial merges would likely increase temporary duplication and instability.

Risks to control in one PR:

1. test fragility from monkeypatches against `flow.py` internals
2. accidental behavior drift while moving mixed responsibilities

Mitigation: follow the phase-gated plan in this spec and do not skip validation gates.

## Target End State (Directories and Files)

Shared transport-agnostic modules:

- `/Users/dblinkhorn/squire/src/squire_core/transport/inbound.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/matching_pipeline.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/mutations.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/targeting.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/validation.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/tracing.py`

Discord adapter modules:

- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/runtime.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/context.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/io.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/views.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/adapter.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/scheduler.py`

Removed at end of migration:

- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/flow.py`

## Required Runtime Entry Shape

1. `/Users/dblinkhorn/squire/src/squire_core/runtime.py` remains top-level composition root.
2. Discord transport entrypoint moves to `/Users/dblinkhorn/squire/src/squire_core/transport/discord/runtime.py`.
3. `runtime.py` delegates to transport runtime selection/wiring; no SDK-native message handling logic at top-level runtime.

## Current `flow.py` Inventory and Disposition

Source file:

- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/flow.py`

### A) Discord-Specific (Keep in adapter modules)

1. SDK message-to-context translation currently at `_build_transport_context`.
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/discord/context.py`
2. Discord IO wrappers `_safe_add_reaction`, `_swap_reaction`, `_send_response`.
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/discord/io.py`
3. Discord runtime adapter classes (`_DiscordRoutingRuntime`, `_DiscordCommandRuntime`).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/discord/runtime.py`
4. Discord startup wiring and token boot (`main`).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/discord/runtime.py`

### B) Shared Reusable (Extract to shared transport modules)

1. Non-command inbound pipeline (`_handle_message` core classify/decision/extract/apply flow).
   - Move reusable orchestration to: `/Users/dblinkhorn/squire/src/squire_core/transport/inbound.py`
   - Keep only Discord event entry wrapper in `discord/runtime.py`.
2. Matching/decision prep and candidate query generation (`_candidate_queries_from_llm`, `_build_decision_input`).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/matching_pipeline.py`
3. Mutation apply orchestration (`_apply_command_operation` core logic and non-command apply branch behavior).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/mutations.py`
4. Targeting/cursor resolution policy (`_cursor_key`, `_parent_cursor_key`, `_resolve_result_cursor*`, `_resolve_command_target`, mapping helpers).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/targeting.py`
5. Trace writers (`_write_matching_trace`, `_write_nl_mutation_normalized_trace`) and trace-payload helpers.
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/tracing.py`
6. `!fix`/field parsing validation helpers (`_validate_fix_updates`, ISO/date/datetime checks).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/validation.py`
7. Index refresh + semantic sync helpers (`_refresh_index`, `_refresh_index_async`).
   - Move to: `/Users/dblinkhorn/squire/src/squire_core/transport/mutations.py` (or a small `index_sync.py` helper module if needed).

### C) Duplicated Legacy Helpers (Delete from `flow.py`; keep canonical shared versions)

These have canonical equivalents already in shared modules and should not remain duplicated:

1. NL routing constants/normalizers now canonical in `/Users/dblinkhorn/squire/src/squire_core/transport/routing.py`
2. Field normalization helpers already canonical in `/Users/dblinkhorn/squire/src/squire_core/transport/routing.py`
3. Candidate/title formatting helpers already duplicated in `/Users/dblinkhorn/squire/src/squire_core/transport/discord/views.py`

Rule: when moving code, import shared canonical implementation instead of cloning.

## Module Responsibilities (End State)

### `/Users/dblinkhorn/squire/src/squire_core/transport/inbound.py`

Shared inbound orchestration for message capture fallback path:

1. raw event creation/write orchestration
2. classify prompt/extract prompt decision logic
3. decision + matching orchestration hooks
4. pending-vs-apply branch orchestration
5. adapter callback hooks for IO and view factory

Must not import transport SDKs.

### `/Users/dblinkhorn/squire/src/squire_core/transport/matching_pipeline.py`

1. candidate-query prompt execution
2. decision input payload construction
3. matching trace payload construction/update
4. retrieval/decision helper orchestration that is transport-neutral

Must not import transport SDKs.

### `/Users/dblinkhorn/squire/src/squire_core/transport/mutations.py`

1. command mutation apply orchestration
2. non-command apply orchestration helpers
3. index refresh and semantic sync helper calls
4. reminder schedule notification hook invocation
5. affinity touch updates

Must not import transport SDKs.

### `/Users/dblinkhorn/squire/src/squire_core/transport/targeting.py`

1. cursor key derivation policy from `TransportMessageContext`
2. thread-parent fallback target resolution
3. command target token to canonical ID resolution
4. reason-code mapping for telemetry/clarification

Must align key type with `InteractionKey` from `/Users/dblinkhorn/squire/src/squire_core/transport/state.py`.

### `/Users/dblinkhorn/squire/src/squire_core/transport/validation.py`

1. strict `!fix` validation rules
2. date/datetime parsing and normalization utilities used by command and NL paths
3. field ambiguity resolution rules (e.g., `due_date` vs `due_at`)

Single canonical source for this logic.

### `/Users/dblinkhorn/squire/src/squire_core/transport/tracing.py`

1. matching trace write path
2. normalized NL mutation trace write path
3. trace operation payload format helpers

Single canonical source for trace artifact writing.

### `/Users/dblinkhorn/squire/src/squire_core/transport/discord/runtime.py`

Thin composition module only:

1. startup config/bootstrap wiring
2. bot/message handler registration
3. translation from Discord event hooks to shared inbound/command/routing entrypoints
4. runtime-level dependency injection of Discord IO + view factories into shared modules

No heavy business logic body.

### `/Users/dblinkhorn/squire/src/squire_core/transport/discord/context.py`

1. convert `discord.Message` to `TransportMessageContext`
2. adapter-specific identity/thread mapping

### `/Users/dblinkhorn/squire/src/squire_core/transport/discord/io.py`

1. send response, reaction add/swap, thread behavior
2. Discord-specific IO error handling

## Contract and Coupling Requirements

1. Shared modules must accept transport-neutral context and callbacks/protocols only.
2. No `discord.py` imports outside `/Users/dblinkhorn/squire/src/squire_core/transport/discord/`.
3. Shared targeting must not depend on Discord integer-only assumptions when context values are strings.
4. Shared modules may return plain data structures for adapter rendering, but must not create SDK views/components directly.
5. Compatibility aliases are prohibited unless explicitly marked temporary with removal criteria and target completion phase.

## One-PR Phase Plan (Required)

### Phase 0: Scaffolding and Safety Nets

1. add new module files listed above
2. add minimal protocol/callback seams needed by shared modules
3. keep behavior unchanged

Gate:

1. compile + existing focused tests pass

### Phase 1: Move Pure Shared Helpers

1. extract validation/tracing/targeting pure helpers from `flow.py`
2. update call sites to new shared modules
3. remove duplicate implementations from `flow.py`

Gate:

1. focused routing/commands tests pass
2. no duplicate helper definitions remain in `flow.py`

### Phase 2: Extract Mutation and Matching Pipelines

1. move `_apply_command_operation` shared logic into `transport/mutations.py`
2. move matching/decision helper logic into `transport/matching_pipeline.py`
3. wire both command runtime and inbound runtime paths through new modules

Gate:

1. command behavior parity tests pass
2. pending/apply/matching trace tests pass

### Phase 3: Extract Shared Inbound Orchestration

1. carve non-command inbound path from `_handle_message` into `transport/inbound.py`
2. keep Discord runtime wrapper as thin adapter around shared inbound entrypoint

Gate:

1. capture/classify/decision/apply behavior parity tests pass
2. no business-logic-heavy block remains in adapter runtime file

### Phase 4: Discord Runtime Decomposition

1. move SDK translation into `discord/context.py`
2. move IO wrappers into `discord/io.py`
3. create `discord/runtime.py` for lifecycle wiring
4. migrate imports and runtime delegation

Gate:

1. runtime startup and command/NL flows still pass focused suite
2. adapter boundaries are explicit

### Phase 5: Remove Transitional File

1. delete `/Users/dblinkhorn/squire/src/squire_core/transport/discord/flow.py`
2. update all imports/tests/docs to new module paths
3. remove any temporary compatibility wrappers introduced during migration

Gate:

1. repo scan shows zero references to `transport.discord.flow`
2. focused suite green; broader suite at current baseline

## Test and Validation Requirements

Minimum focused suite each phase:

1. `/Users/dblinkhorn/squire/tests/test_transport_commands.py`
2. `/Users/dblinkhorn/squire/tests/test_transport_routing.py`
3. `/Users/dblinkhorn/squire/tests/test_discord_contract_bridge.py`
4. `/Users/dblinkhorn/squire/tests/test_discord_commands.py`
5. `/Users/dblinkhorn/squire/tests/test_nl_command_routing_config.py`
6. `/Users/dblinkhorn/squire/tests/test_nl_mutation_normalization.py`
7. `/Users/dblinkhorn/squire/tests/test_nl_multi_operation_clarification.py`

Add required tests in this PR:

1. new inbound shared module unit tests
2. new mutations shared module unit tests
3. new targeting module tests (including thread-parent fallback)
4. new tracing/validation module tests
5. adapter runtime tests that no longer monkeypatch deleted `flow.py` internals

## Acceptance Criteria (Hard)

1. `/Users/dblinkhorn/squire/src/squire_core/transport/discord/flow.py` does not exist.
2. All shared modules are transport-agnostic and import no transport SDKs.
3. Discord SDK usage is confined to `/Users/dblinkhorn/squire/src/squire_core/transport/discord/`.
4. Runtime behavior for existing Discord workflows is unchanged from user perspective.
5. No duplicate canonical logic remains between shared modules and adapter modules.
6. Tests currently patching `flow.py` internals are migrated to patch stable shared/adapter seams.
7. `docs/modules.md` and `docs/architecture.md` are updated to reflect the final boundaries.

## Risk Register

1. Hidden coupling via tests patching private functions.
- Mitigation: migrate tests early in phases; patch stable module APIs.

2. Behavioral drift during inbound extraction.
- Mitigation: move with wrappers first, then reduce wrappers; validate each phase.

3. Temporary compatibility code becoming permanent.
- Mitigation: forbid compatibility aliases without explicit removal gate.

4. One-PR merge difficulty.
- Mitigation: maintain phase checklist in PR description and verify each gate before continuing.

## Handoff Checklist (Fresh Context)

1. Read:
- `/Users/dblinkhorn/squire/AGENTS.md`
- `/Users/dblinkhorn/squire/README.md`
- `/Users/dblinkhorn/squire/.agent/context.md`
- `/Users/dblinkhorn/squire/docs/multi-transport-refactor-spec.md`
- `/Users/dblinkhorn/squire/docs/transport-modularity-hardening-spec.md`

2. Implement phases in order; do not skip gates.
3. Keep behavior parity as priority over cleanup speed.
4. If a removal is uncertain, defer with explicit follow-up evidence tasks.

## Success Definition

This follow-on is complete when adding a new transport is primarily adapter work against shared modules, rather than copying or re-implementing app flow logic from a transport-specific runtime file.
