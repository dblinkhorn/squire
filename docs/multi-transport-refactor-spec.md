# Multi-Transport Refactor Spec (Discord -> Shared Core)

## Purpose

Define an implementation-ready refactor plan to separate transport-specific logic from shared Squire runtime behavior, so future interfaces can reuse command, routing, mutation, and state logic without duplicating `discord_bot.py` behavior.

This spec is intentionally detailed for a fresh-context implementer.

## Motivation

Current runtime behavior is heavily concentrated in `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py`. The file now mixes:

1. transport concerns (Discord event lifecycle, views, reactions, message IO)
2. shared behavioral concerns (command semantics, cursor resolution, NL routing/normalization, reminder scheduling primitives)
3. bootstrap/runtime concerns (health server, logging, config parsing, test-mode startup)

This slows feature development and raises risk for each new integration because core behavior must be copied or re-implemented.

Primary objective: modularize once so future transports can be added on top of reusable shared layers.

## Goals

1. Preserve current Discord behavior while extracting transport-agnostic logic.
2. Establish clear module boundaries for shared runtime behavior vs adapter behavior.
3. Introduce stable transport-facing interfaces (message context, response contract, action handlers).
4. Prepare shared command and NL mutation flows for future transport adapters with minimal duplication.
5. Keep confirmation-first mutation safety and explicit-only destructive controls unchanged.
6. Keep the app working at the end of every stage (no intentional broken intermediate stage).

## Non-Goals (this refactor)

1. Redesigning command UX or NL policy semantics.
2. Changing canonical storage, index schema, or reminder business rules.
3. Implementing Slack adapter/runtime behavior in this staged refactor.
4. Broad architecture rewrite outside transport boundaries.
5. Requiring a console-script packaging change in this refactor (existing `make`-driven startup remains acceptable).

## Explicit Decisions (Approved)

1. `discord_bot.py` is kept as a temporary compatibility shim during staged extraction, then removed near the end.
2. Final runtime composition root is `/Users/dblinkhorn/squire/src/squire_core/runtime.py`.
3. Canonical invocation target is `python -m squire_core.runtime` (with `make` targets updated accordingly).
4. Transport import boundaries are documented as design guidance in docs/AGENTS, not enforced by strict CI import-lint rules in this refactor.
5. Test strategy is not only moving tests; it must include new seam/contract tests in addition to behavior-parity tests.

## Existing Invariants to Preserve

1. Raw events immutable, derived artifacts versioned, canonical objects mutable source of truth.
2. Explicit `!` commands remain highest precedence.
3. `!clear-archive` safety (`DELETE` follow-up) remains explicit-only.
4. Mutation apply remains confirmation-first.
5. Numbered cursor behavior remains deterministic and channel/user scoped (including thread-parent fallback behavior currently implemented).
6. Due-time reminders remain deterministic, ledger-deduped, and resilient to restarts.

## Current Concern Clusters in `discord_bot.py`

Representative anchors in `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py`:

1. Shared state dataclasses and caches: lines ~303-384, ~1729-2090.
2. Bootstrap/health/config parsing utilities: lines ~394-745, ~1644-1726.
3. NL routing + mutation normalization + clarification logic: lines ~783-3517.
4. Explicit command orchestration and operation apply flow: lines ~4285-4808.
5. Discord-specific UI/View classes: lines ~2189-2554.
6. Discord lifecycle loops and scheduler dispatch: lines ~4810-5243.

These clusters define extraction targets.

## Target Organization (End State)

### High-Level Layering

1. `core domain` (existing: canonical store, indexer, surfacing, operation apply, pending actions, decision flow).
2. `transport shared runtime` (new: command orchestration, NL routing, state/cursor management, reminder schedule helpers, bootstrap helpers).
3. `transport adapters` (Discord, Slack): event ingestion, platform identity/channel resolution, message send/edit/reaction/action translation, platform UI components.

### Proposed Package Map

```text
src/squire_core/
  __main__.py        (optional; delegates to runtime.main)
  runtime.py
  transport/
    __init__.py
    state.py
    bootstrap.py
    health.py
    commands.py
    routing.py
    reminders.py
    contracts.py
    discord/
      __init__.py
      adapter.py
      views.py
      scheduler.py
    slack/
      __init__.py
      adapter.py
      scheduler.py   (optional in v1)
```

Notes:

1. `bootstrap.py` and `health.py` may be combined initially if simpler.
2. `contracts.py` should hold transport-agnostic dataclasses/protocols used by both `commands.py` and `routing.py`.
3. Keep existing non-transport modules unchanged unless a narrow adapter seam requires signature expansion.
4. Console-script entrypoints in `pyproject.toml` are optional and out of scope for this refactor.

## Shared Contracts (Required)

Introduce transport-neutral contracts before heavy extraction.

### Message Context Contract

Example shape:

```python
@dataclass
class TransportMessageContext:
    source: str  # "discord" | "slack"
    user_id: str
    channel_id: str
    thread_id: str | None
    message_id: str
    content: str
    is_dm: bool
    created_at: datetime

    async def send_text(self, text: str) -> None: ...
    async def send_warning(self, text: str) -> None: ...
    async def add_reaction(self, emoji: str) -> None: ...
    async def send_pending_controls(self, pending_id: str, payload: dict[str, Any]) -> None: ...
```

Dependency boundary guidance:

1. shared modules must accept transport-neutral contracts (like this context)
2. adapter modules own SDK-native object translation
3. this boundary is review-guided in this project phase (not CI-enforced)

### Command/Routing Result Contracts

Use typed results to avoid stringly coupled branching across adapters:

1. `CommandResult` (handled/not-handled, telemetry payload, cursor updates, side effects).
2. `RouteResult` (read command mapped, mutation plan normalized, blocked reason, clarification request).
3. `TargetResolutionResult` (resolved id/object_type + reason/source_view for telemetry).

### Shared State Contracts

Move and formalize existing dataclasses from `discord_bot.py` lines ~303-384:

1. `ResultCursor`
2. `CommandTargetResolution`
3. `NLClarificationContext`
4. `AffinityTouch`
5. `DueTimeReminderScheduleConfig`
6. `DueTimeReminderSentLedgerEntry`

## Detailed Move Catalog

This catalog identifies where logic should move. It is grouped by concern rather than by every single function.

### A) Shared State and Cursor Management

Source cluster:

- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:303`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:1729`

Move to:

- `/Users/dblinkhorn/squire/src/squire_core/transport/state.py`

Includes:

1. cursor prune/store/resolve helpers
2. affinity touch recording and scoring helpers
3. NL clarification context prune/load/store/clear
4. archive clear confirmation TTL state helpers
5. numbered digest/review rendering helpers that are transport-neutral text formatting

Keep in adapter:

1. extraction of platform-specific cursor keys from native message object (Discord/Slack object to standard keys)

### B) Bootstrap, Health, and Runtime Parsing Helpers

Source cluster:

- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:394`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:602`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:1644`

Move to:

- `/Users/dblinkhorn/squire/src/squire_core/transport/bootstrap.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/health.py`

Includes:

1. logging configuration helpers
2. health HTTP server and parse helpers
3. daily/weekly/due-time parse helpers
4. reminder ledger read/serialize/flush helpers
5. test archive override and test-mode seed orchestration entry helpers

Keep in adapter/main:

1. runtime lifecycle wiring for when bootstrap is invoked

### C) NL Routing and Mutation Normalization

Source cluster:

- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:783`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:3131`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:2705`

Move to:

- `/Users/dblinkhorn/squire/src/squire_core/transport/routing.py`

Includes:

1. route payload normalization and risk/confidence interpretation
2. read-command mapping from NL route output
3. mutation plan normalization and field/value resolution
4. clarification scope enforcement and merge logic
5. pending action preparation and normalized trace writing orchestration hooks
6. NL telemetry helper events

Keep in adapter:

1. platform-specific response formatting wrappers if they depend on native UI affordances
2. platform-specific "send controls" implementation for pending confirmation

### D) Explicit Command Orchestration

Source cluster:

- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:4285`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:4685`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:4784`

Move to:

- `/Users/dblinkhorn/squire/src/squire_core/transport/commands.py`

Includes:

1. command parsing and argument validation
2. numbered target resolution orchestration
3. `!status`, `!weekly`, `!recent`, `!find`, `!show` shared behavior
4. `!done`, `!append`, `!fix` apply path orchestration
5. `!confirm`, `!cancel`, `!clear-archive` shared safety semantics
6. cursor update behavior and related telemetry

Keep in adapter:

1. actual message send/edit/reaction operations
2. platform-specific action controls

### E) Reminder Scheduling Core

Source cluster:

- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:562`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:4890`

Move (shared core) to:

- `/Users/dblinkhorn/squire/src/squire_core/transport/reminders.py`

Includes:

1. schedule config parse and validation
2. queue/ledger utility behavior and wake semantics
3. destination selection policy scaffolding via adapter callback

Keep in adapter scheduler:

1. platform send destination resolution specifics
2. adapter loop lifecycle registration

### F) Discord-Specific Views and Lifecycle

Source cluster:

- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:2189`
- `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py:4810`

Move to:

- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/views.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/adapter.py`
- `/Users/dblinkhorn/squire/src/squire_core/transport/discord/scheduler.py`

Includes:

1. all `discord.ui.View` classes and callbacks
2. `discord.Client` subclass and Discord event hooks
3. Discord channel/thread/user fetch behavior
4. reaction swap/disable-view mechanics

This remains transport-specific by design.

## Staged Implementation Plan

Use staged execution to keep behavior stable and reviewable.

## Stage 0: Refactor Preparation (No Behavior Change)

Deliverables:

1. add this spec and implementation checklist references
2. add module scaffolding files under `src/squire_core/transport/`
3. add contract dataclasses/protocols in `contracts.py` and shared state objects in `state.py`

Acceptance criteria:

1. no runtime behavior changes
2. imports compile
3. full tests still pass (subject to known environment constraints)

## Stage 1: Move Pure/Low-Coupling Helpers First

Scope:

1. extract bootstrap/health/time parsing helpers
2. extract reminder ledger parse/flush helpers
3. route `main()` and startup to use extracted helpers

Why first:

- low coupling to Discord message objects
- high confidence, low regression risk

Acceptance criteria:

1. health endpoint behavior unchanged
2. startup logs and schedule parse behavior unchanged
3. existing tests for health/config still pass

## Stage 2: Shared State and Cursor Logic Extraction

Scope:

1. move cursor, affinity, clarification, archive-clear state helpers to `transport/state.py`
2. introduce explicit APIs for get/store/prune operations
3. preserve thread-parent fallback behavior exactly

Acceptance criteria:

1. numbered command tests unchanged
2. clarification TTL behavior unchanged
3. telemetry fields (`source_view`, reason codes) unchanged

## Stage 3: Command Engine Extraction

Scope:

1. move command parsing/orchestration into `transport/commands.py`
2. create adapter-facing callback hooks for send/reaction/UI
3. keep command semantics and text contract stable unless explicitly approved

Acceptance criteria:

1. all command tests pass without behavioral drift
2. cursor updates and numbered operation flows remain identical
3. `!clear-archive` safety flow unchanged

## Stage 4: NL Routing Engine Extraction

Scope:

1. move NL route and mutation normalization into `transport/routing.py`
2. use shared contracts for route results and adapter callbacks
3. keep normalized trace artifacts and reason codes stable

Acceptance criteria:

1. NL routing and clarification tests unchanged
2. pending creation/confirm semantics unchanged
3. out-of-scope clarification policy copy unchanged

## Stage 5: Discord Adapter Consolidation

Scope:

1. reduce Discord adapter to thin event-translation and IO layer
2. move Discord-only views/scheduler into `transport/discord/*`
3. keep `discord_bot.py` as compatibility wrapper/small shim while call sites are migrated
4. use explicit Discord-specific adapter naming (`DiscordSquireBot`); keep `SquireBot` as a temporary compatibility alias during migration

Acceptance criteria:

1. Discord runtime behavior unchanged
2. code ownership boundaries are clear and documented
3. `discord_bot.py` significantly reduced in responsibility

## Stage 6: Entrypoint Cutover and Shim Removal

Scope:

1. switch runtime invocation to `python -m squire_core.runtime` across local/dev/deploy surfaces
2. migrate remaining references/imports away from `squire_core.discord_bot`
3. remove `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py` once compatibility is no longer needed

Expected update surfaces:

1. `/Users/dblinkhorn/squire/Makefile`
2. `/Users/dblinkhorn/squire/Dockerfile`
3. tests currently importing `from squire_core import discord_bot`

Acceptance criteria:

1. no remaining operational runtime/test/doc references to `squire_core.discord_bot` entrypoint (historical references in this planning spec are acceptable)
2. startup behavior remains unchanged from user perspective
3. full test suite passes with new module paths

Completion notes (2026-02-22):

1. runtime entrypoint cut over to `python -m squire_core.runtime` in local/dev/deploy surfaces
2. compatibility shim file `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py` removed
3. tests that imported `from squire_core import discord_bot` migrated to runtime module import paths

Execution ordering after Stage 6 (required):

1. Stage 7A (safe hygiene + verification inventory) first
2. Stage 8 (transport boundary hardening) second
3. Stage 7B (orphan removals that depend on boundary-hardening outcomes) last

## Stage 7: Cleanup and Orphan Handling (Two-Phase)

Safety proviso (required):

1. do not remove code solely because it appears unused from static scans
2. before deleting any symbol/module/path, verify it is truly leftover by checking runtime call paths, tests, and config-driven/indirect usage
3. if certainty is low, prefer deprecation + follow-up verification over immediate removal
4. every removal PR must include explicit evidence of safety (references searched, tests run, and observed behavior parity)

Recorded findings inventory (2026-02-22 audit):

1. likely unused imports in transport/shared modules:
   - `/Users/dblinkhorn/squire/src/squire_core/transport/routing.py` (`Awaitable`, and likely `timezone`)
   - `/Users/dblinkhorn/squire/src/squire_core/transport/discord/scheduler.py` (`Path`)
2. likely unused exception aliases in runtime:
   - `/Users/dblinkhorn/squire/src/squire_core/runtime.py` (`except Exception as exc` sites where `exc` is not referenced)
3. likely orphaned transport helpers/protocol aliases (no call sites found in repo scan):
   - `/Users/dblinkhorn/squire/src/squire_core/transport/contracts.py`: `TransportIO`, `SendTextFn`, `AddReactionFn`, `SendPendingControlsFn`
   - `/Users/dblinkhorn/squire/src/squire_core/transport/state.py`: `NLRouteIntentV1`, `get_result_cursor`, `get_archive_clear_confirmation`
4. Slack scaffold modules currently unreferenced by runtime:
   - `/Users/dblinkhorn/squire/src/squire_core/transport/slack/adapter.py`
   - `/Users/dblinkhorn/squire/src/squire_core/transport/slack/scheduler.py`
   - `/Users/dblinkhorn/squire/src/squire_core/transport/slack/__init__.py`

### Stage 7A: Safe Hygiene and Verification Inventory (Pre-Hardening)

Scope:

1. remove only low-risk hygiene issues that are independently safe (for example clearly-unused imports/locals with no behavior impact)
2. create/refresh the verified inventory of candidate orphan symbols/modules, including evidence and confidence level
3. add lightweight lint/static-check coverage in dev workflow to prevent new unused-symbol drift
4. defer any ambiguous module/symbol removals to Stage 7B unless safety is proven

Acceptance criteria:

1. static analysis reports no new unused-import/unused-variable warnings in `src/squire_core/transport/*` and `src/squire_core/runtime.py`
2. all Stage 7A removals include explicit safety evidence and behavior-parity verification
3. inventory of deferred candidate removals is documented with rationale and confidence

Completion notes (2026-02-22):

1. applied low-risk hygiene removals with no behavior changes:
   - removed unused imports in `/Users/dblinkhorn/squire/src/squire_core/transport/routing.py` (`Awaitable`, `timezone`)
   - removed unused import in `/Users/dblinkhorn/squire/src/squire_core/transport/discord/scheduler.py` (`Path`)
   - removed unused exception aliases in `/Users/dblinkhorn/squire/src/squire_core/runtime.py` (two `except Exception as exc` sites where `exc` was not referenced)
2. refreshed deferred-inventory decisions with confidence:
   - `/Users/dblinkhorn/squire/src/squire_core/transport/contracts.py` (`TransportIO`, `SendTextFn`, `AddReactionFn`, `SendPendingControlsFn`) remain deferred with **medium** confidence they are orphaned; held for Stage 8/7B to avoid pre-hardening contract churn
   - `/Users/dblinkhorn/squire/src/squire_core/transport/state.py` (`NLRouteIntentV1`, `get_result_cursor`, `get_archive_clear_confirmation`) remain deferred with **medium-high** confidence they are orphaned; held until Stage 8 determines final shared contract shape
   - `/Users/dblinkhorn/squire/src/squire_core/transport/slack/{adapter.py,scheduler.py,__init__.py}` remain deferred with **high** confidence runtime-unreferenced; final retain/remove decision stays in Stage 7B

### Stage 7B: Orphan Removal and Contract Pruning (Post-Hardening)

Scope:

1. after Stage 8, remove or wire orphaned helpers/symbols based on the hardened boundary design
2. decide and document final status of Slack scaffold placeholders (retain as explicit stubs vs retire)
3. remove any temporary compatibility/deprecation scaffolding proven unnecessary after Stage 8

Acceptance criteria:

1. every exported symbol in `transport/contracts.py` and `transport/state.py` is either used or intentionally documented as reserved scaffolding
2. docs capture final decision for Slack scaffold retention vs removal
3. tests remain green at current baseline (allowing known sandbox-limited health-server bind constraints)
4. each removed symbol/module has recorded verification evidence that removal is safe and non-disruptive

## Stage 8: Transport Boundary Hardening (Modularity Intent Completion)

Intent:

1. complete the original modularity goal by enforcing strict separation between shared runtime behavior and transport SDK concerns
2. address architecture drift left after Stage 6 cutover, where runtime composition remains Discord-coupled

Recorded boundary violations (2026-02-22 audit):

1. `/Users/dblinkhorn/squire/src/squire_core/runtime.py` still imports and types against `discord` directly, including `discord.Message` in shared flow helpers and runtime wrappers
2. `/Users/dblinkhorn/squire/src/squire_core/runtime.py` still constructs and dispatches Discord UI/view primitives (`PendingActionView`, `MutationPendingView`, `AutoApplyFeedbackView`) instead of delegating those concerns to adapter modules
3. shared command/routing orchestration is invoked through Discord-specific runtime wrappers (`_DiscordCommandRuntime`, `_DiscordRoutingRuntime`) whose method signatures remain transport-specific
4. transport-neutral contracts in `/Users/dblinkhorn/squire/src/squire_core/transport/contracts.py` (`TransportMessageContext`, `TransportIO`) are not yet the active production entry contracts for command/routing flow

Scope:

1. make `/Users/dblinkhorn/squire/src/squire_core/runtime.py` a thin composition/launch module only (config/bootstrap/wiring)
2. move Discord message lifecycle handling and Discord-specific flow orchestration under `/Users/dblinkhorn/squire/src/squire_core/transport/discord/`
3. refactor shared flow entrypoints to consume transport-neutral contracts (`TransportMessageContext`, `TransportIO`, typed result contracts) instead of SDK-native message/view types
4. remove Discord UI and reaction/send primitives from shared runtime codepaths; keep them adapter-owned
5. add lightweight import-boundary checks and contract tests so boundary regressions are caught automatically

Implementation guidance:

1. shared modules (`transport/commands.py`, `transport/routing.py`, `transport/state.py`) should not import or type against `discord.py`/Slack SDK objects
2. adapter modules should own all SDK object translation and UI concerns
3. any temporary compatibility wrappers must be documented with explicit removal criteria and targeted follow-up stage
4. apply the Stage 7 removal-safety proviso to any boundary-hardening deletions (verify first, then remove)

Acceptance criteria:

1. `/Users/dblinkhorn/squire/src/squire_core/runtime.py` has no `discord.py` imports and no function signatures typed with Discord SDK objects
2. shared transport modules (`/Users/dblinkhorn/squire/src/squire_core/transport/{commands.py,routing.py,state.py,contracts.py}`) expose transport-neutral contracts only
3. Discord-specific UI/view creation and message/reaction side effects exist only under `/Users/dblinkhorn/squire/src/squire_core/transport/discord/`
4. contract tests validate that Discord adapter translates SDK-native objects into shared contracts and preserves current behavior
5. docs (`architecture.md`, `modules.md`, this spec) reflect the enforced boundary model, not just directional intent

## Test and Validation Strategy

Run by stage; do not wait until end.

Minimum recurring suite:

1. `/Users/dblinkhorn/squire/tests/test_discord_commands.py`
2. `/Users/dblinkhorn/squire/tests/test_discord_schedule.py`
3. `/Users/dblinkhorn/squire/tests/test_surfacing.py`
4. `/Users/dblinkhorn/squire/tests/test_nl_command_routing_config.py`
5. `/Users/dblinkhorn/squire/tests/test_nl_mutation_normalization.py`
6. `/Users/dblinkhorn/squire/tests/test_nl_multi_operation_clarification.py`

Stage-specific additions:

1. add unit tests for new `transport/state.py` APIs
2. add unit tests for contract objects and adapter callback behavior
3. add smoke tests for reminder helper extraction boundaries

### Test Migration Guidance (Fresh-Context Implementer)

Tests should evolve in phases, not as a bulk move:

1. keep existing black-box behavior tests in place during early extraction (`test_discord_commands.py`, `test_discord_schedule.py`, etc.)
2. add focused unit tests for new shared modules as they appear:
  - `tests/test_transport_state.py`
  - `tests/test_transport_commands.py`
  - `tests/test_transport_routing.py`
  - `tests/test_transport_reminders.py`
3. add adapter contract tests to verify Discord adapter passes normalized context/callbacks correctly
4. stage 6 cutover completed on 2026-02-22; tests importing the old shim were migrated to runtime module paths
5. preserve user-visible behavior assertions while moving internal-unit coverage closer to extracted modules

Regression policy:

1. preserve existing user-visible response copy unless explicitly changed
2. preserve existing telemetry event names and reason codes
3. preserve existing pending action file formats

## Risk Register and Mitigations

1. Hidden behavior coupling in monolith function flow
- Mitigation: extract in thin wrappers first, keep function bodies unchanged, then simplify.

2. Transport callback mismatch (Discord assumptions leak into shared layer)
- Mitigation: enforce explicit callback protocol in `contracts.py` and add adapter contract tests.

3. Clarification-context regression
- Mitigation: preserve one-turn/immutable-scope logic verbatim during first extraction pass.

4. Reminder delivery drift
- Mitigation: isolate logic tests for queue/ledger behavior before and after extraction.

5. Over-scoping before cutover completion
- Mitigation: stage gates with explicit acceptance criteria; complete through Stage 6 and stop there if instability appears.

## Documentation Updates Required During Implementation

When stages land, keep docs aligned:

1. `/Users/dblinkhorn/squire/docs/modules.md` (add shared transport layer + adapter boundaries)
2. `/Users/dblinkhorn/squire/docs/architecture.md` (update runtime flow to include transport shared core)
3. `/Users/dblinkhorn/squire/README.md` (if new runtime entrypoints or adapter config become user-facing)

## Handoff Checklist for Fresh-Context Implementer

1. Read:
- `/Users/dblinkhorn/squire/AGENTS.md`
- `/Users/dblinkhorn/squire/README.md`
- `/Users/dblinkhorn/squire/.agent/context.md`
- `/Users/dblinkhorn/squire/docs/multi-transport-refactor-spec.md`

2. Execute by stages, one PR-sized slice at a time.

Post-Stage-6 sequencing requirement:

- execute Stage 7A, then Stage 8, then Stage 7B

3. For each stage:
- keep behavior unchanged
- run focused tests first, then broader suite
- update `.agent/context.md` with only durable decisions and unresolved risks

4. Slack adapter implementation remains out of scope for this staged refactor. Revisit as a follow-on after Stage 7B completes.

## Success Definition

This project is successful when:

1. Discord behavior is unchanged from user perspective.
2. Shared runtime logic is isolated from Discord platform primitives.
3. New transport additions should become additive adapter work, not monolith edits.
