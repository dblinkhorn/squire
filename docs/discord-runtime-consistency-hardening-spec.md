# Discord Runtime Consistency Hardening Spec

## Purpose

Define an implementation-ready follow-on refactor that removes the remaining migration-shaped abstractions in the Discord transport/runtime stack.

This spec is explicitly about end-state consistency. The target code should read as if Squire had been structured this way from day one, not as if a monolith had been split and then preserved through compatibility-oriented layers.

## Problem Statement

The pending-interaction extraction completed an important transport boundary cleanup, but the Discord runtime stack still has several layers that look historically accumulated rather than intentionally designed:

1. `src/squire_core/transport/discord/runtime_adapter_command.py` still contains module-level forwarding seams kept for test patching.
2. `src/squire_core/transport/discord/runtime_adapter_utils.py` duplicates shared-module ownership through a Discord-local utility layer.
3. `src/squire_core/transport/discord/message_entry.py` constructs nested runtime adapters through factory lambdas instead of explicit composition.
4. Discord IO behavior is split across three layers:
   - `src/squire_core/transport/discord/adapter.py`
   - `src/squire_core/transport/discord/io.py`
   - helper wrappers in `src/squire_core/transport/discord/message_entry.py`
5. `src/squire_core/transport/runtime_registry.py` and `SQUIRE_TRANSPORT` runtime selection are speculative generalization while only one transport exists.
6. `src/squire_core/transport/discord/runtime.py` exports a thin `handle_message(...)` trampoline that only forwards into `message_entry.py`.

None of these are catastrophic, but together they still make the codebase read like “post-migration transport hardening” rather than “clean first-principles Discord transport architecture.”

## Design Goals

1. Remove adapter-local forwarding layers whose main value is test patchability.
2. Replace nested runtime factory composition with one explicit Discord message-runtime object.
3. Make one module the obvious canonical home for Discord IO behavior.
4. De-generalize runtime selection until more than one real transport exists.
5. Preserve all current Discord behavior and user-facing semantics.
6. Keep shared transport modules transport-agnostic.
7. Ensure tests validate behavior through canonical seams, not migration scaffolding.

## Non-Goals

1. Implement Slack or any additional transport.
2. Change command semantics, NL routing semantics, or pending-action semantics.
3. Redesign Discord UI wording or thread/reaction behavior.
4. Change archive, canonical object, pending action, or index schemas.
5. Introduce a new “temporary” compatibility layer to ease this refactor.

## Day-One Consistency Rule

The implementation must follow this rule:

If a module, type, function, or test would only make sense because the codebase used to have a different shape, it should not exist in the final state.

Practical implications:

1. No module-level forwarding seams kept “for tests.”
2. No adapter utility modules that merely relay into shared modules.
3. No runtime factory lambdas whose main job is to preserve an older split.
4. No tests asserting delegation to wrappers or preserving deleted intermediate seams.
5. No speculative transport generalization when only one real transport exists.

## Current Findings and Required Fixes

### C1. Discord command adapter retains test-oriented forwarding seams

Current evidence:

- `src/squire_core/transport/discord/runtime_adapter_command.py:117`
- `src/squire_core/transport/discord/runtime_adapter_command.py:118`
- `src/squire_core/transport/discord/runtime_adapter_command.py:149`
- `src/squire_core/transport/discord/runtime_adapter_command.py:157`

Problem:

- The file explicitly says the seams exist for focused test patching.
- These wrappers mostly forward into `runtime_adapter_utils.py` or shared modules.
- This is migration residue, not domain logic.

Required fix:

1. Remove these module-level forwarding seams from the end state.
2. Tests must patch canonical shared functions or use explicit fake runtime objects instead.
3. If a behavior truly requires Discord-specific logic, keep that logic in the real Discord runtime class, not in a fake seam layer.

### C2. `runtime_adapter_utils.py` is an unnecessary Discord-local indirection layer

Current evidence:

- `src/squire_core/transport/discord/runtime_adapter_utils.py:15`
- `src/squire_core/transport/discord/runtime_adapter_utils.py:46`
- `src/squire_core/transport/discord/runtime_adapter_utils.py:57`

Problem:

- The module largely forwards to shared transport logic plus light logging helpers.
- Its existence is hard to justify from first principles.
- It adds one more layer that readers must mentally collapse.

Required fix:

1. Remove `src/squire_core/transport/discord/runtime_adapter_utils.py`.
2. Move canonical behavior to the existing shared modules that already own it:
   - index refresh helpers -> `src/squire_core/transport/mutations.py`
   - derived/written-path ID extraction -> `src/squire_core/transport/mutations.py`
   - numbered-mutation/NL confirm logging:
     - either inline at the real Discord runtime class if genuinely Discord-owned
     - or move to a shared tracing/logging helper module if shared ownership is clearer
3. Do not replace this module with another one-function-per-wrapper file.

### C3. Discord message-entry composition is indirect and migration-shaped

Current evidence:

- `src/squire_core/transport/discord/message_entry.py:47`
- `src/squire_core/transport/discord/message_entry.py:63`
- `src/squire_core/transport/discord/message_entry.py:89`

Problem:

- `message_entry.py` builds command, routing, and inbound runtimes through nested factories.
- The dependency graph is encoded through lambdas instead of explicit composition.
- This makes the runtime stack harder to read than necessary.

Required fix:

1. Replace `_build_command_runtime(...)`, `_build_routing_runtime(...)`, and `_build_inbound_runtime(...)` with explicit composition.
2. Introduce one canonical per-message Discord runtime object:
   - recommended name: `DiscordMessageRuntime`
   - recommended file: `src/squire_core/transport/discord/message_runtime.py`
3. That object should implement the methods needed by:
   - `src/squire_core/transport/commands.py`
   - `src/squire_core/transport/routing.py`
   - `src/squire_core/transport/inbound.py`
   - `src/squire_core/transport/pending_interactions.py`
4. `message_entry.py` should instantiate `DiscordMessageRuntime` directly for the current message and pass it into shared transport modules.

### C4. Discord IO ownership is split across too many forwarding layers

Current evidence:

- `src/squire_core/transport/discord/io.py:14`
- `src/squire_core/transport/discord/io.py:22`
- `src/squire_core/transport/discord/adapter.py:15`
- `src/squire_core/transport/discord/adapter.py:33`
- `src/squire_core/transport/discord/message_entry.py:115`
- `src/squire_core/transport/discord/message_entry.py:119`

Problem:

- The actual IO behavior is implemented in `adapter.py`.
- `io.py` is a thin pass-through.
- `message_entry.py` adds another pass-through layer for `_safe_add_reaction(...)` and `_send_response(...)`.

Required fix:

1. Make `src/squire_core/transport/discord/io.py` the canonical home for Discord IO behavior.
2. Move the actual logic for:
   - `safe_add_reaction(...)`
   - `swap_reaction(...)`
   - `send_response(...)`
   into `io.py`.
3. Remove the forwarding IO functions from `adapter.py`.
4. Remove `_safe_add_reaction(...)` and `_send_response(...)` wrappers from `message_entry.py`.
5. Call `discord.io` directly from `message_entry.py` and the message runtime class.

### C5. Runtime registry is speculative generalization with one transport

Current evidence:

- `src/squire_core/transport/runtime_registry.py:9`
- `src/squire_core/transport/runtime_registry.py:36`

Problem:

- The application supports exactly one runtime transport: Discord.
- A dynamic registry + environment-variable selection mechanism is not justified by current product reality.
- This reads as future-proofing rather than intentional simplicity.

Required fix:

1. Remove `src/squire_core/transport/runtime_registry.py`.
2. Remove `SQUIRE_TRANSPORT` selection support.
3. Change `src/squire_core/runtime.py` to import and call `squire_core.transport.discord.runtime.main` directly.
4. Update docs and tests accordingly.

Decision:

If a second real transport is implemented in the future, transport selection may be reintroduced then, based on actual needs and actual runtime modules, not pre-emptive indirection.

### C6. `discord/runtime.py` exports a thin message trampoline

Current evidence:

- `src/squire_core/transport/discord/runtime.py:44`
- `src/squire_core/transport/discord/runtime.py:53`

Problem:

- The exported `handle_message(...)` function only forwards into `message_entry.handle_message(...)`.
- No external boundary value is created by that export.

Required fix:

1. Remove the exported `handle_message(...)` trampoline from `src/squire_core/transport/discord/runtime.py`.
2. Keep `main()` as the only public runtime entrypoint from that module.
3. The bot callback in `runtime.py` should call `message_entry.handle_message(...)` directly.

## Target End State

### Top-Level Runtime

`src/squire_core/runtime.py`

1. Directly imports Discord runtime main.
2. Contains no dynamic runtime registry lookup.
3. Remains very small.

Expected shape:

```python
from squire_core.transport.discord.runtime import main

if __name__ == "__main__":
    main()
```

### Discord Transport Files

Keep:

- `src/squire_core/transport/discord/runtime.py`
- `src/squire_core/transport/discord/message_entry.py`
- `src/squire_core/transport/discord/context.py`
- `src/squire_core/transport/discord/io.py`
- `src/squire_core/transport/discord/views.py`
- `src/squire_core/transport/discord/adapter.py`
- `src/squire_core/transport/discord/command_contract.py`
- `src/squire_core/transport/discord/scheduler.py`

Add:

- `src/squire_core/transport/discord/message_runtime.py`

Remove:

- `src/squire_core/transport/runtime_registry.py`
- `src/squire_core/transport/discord/runtime_adapter_command.py`
- `src/squire_core/transport/discord/runtime_adapter_routing.py`
- `src/squire_core/transport/discord/runtime_adapter_inbound.py`
- `src/squire_core/transport/discord/runtime_adapter_utils.py`

### Shared Transport Files

Keep using the existing shared canonical modules:

- `src/squire_core/transport/commands.py`
- `src/squire_core/transport/routing.py`
- `src/squire_core/transport/inbound.py`
- `src/squire_core/transport/mutations.py`
- `src/squire_core/transport/pending_interactions.py`
- `src/squire_core/transport/targeting.py`
- `src/squire_core/transport/tracing.py`
- `src/squire_core/transport/archive_clear.py`

No new shared wrapper layer should be introduced as part of this cleanup.

## Required New Discord Composition Shape

### `DiscordRuntimeServices`

Add a small explicit dependency bundle for process-scoped Discord runtime services.

Recommended fields:

```python
@dataclass
class DiscordRuntimeServices:
    runtime_state: RuntimeStateStore
    llm_provider: LLMProvider | AsyncLLMProvider
    embedding_provider: LLMProvider | AsyncLLMProvider | None
    llm_model: str
    due_time_reminder_notifier: Callable[..., Any] | None
```

Placement:

- either in `src/squire_core/transport/discord/runtime.py`
- or in `src/squire_core/transport/discord/message_runtime.py`

Rule:

Do not create a second services/config wrapper around this wrapper. One explicit dependency bundle is sufficient.

### `DiscordMessageRuntime`

Add one canonical per-message runtime adapter object.

Recommended constructor:

```python
DiscordMessageRuntime(
    message: discord.Message,
    services: DiscordRuntimeServices,
)
```

Responsibilities:

1. expose the methods required by shared command/routing/inbound/pending modules
2. own Discord-specific IO/view/context behavior
3. own access to process-scoped runtime state and providers
4. keep actual behavior in methods, not in module-level patch seams

This class should be the only adapter object shared transport modules talk to for Discord message flows.

## Detailed File-Level Requirements

### `src/squire_core/runtime.py`

Required changes:

1. Remove `run_selected_transport` import and call.
2. Import `main` directly from `src/squire_core/transport/discord/runtime.py`.
3. Keep file minimal.

### `src/squire_core/transport/discord/runtime.py`

Required changes:

1. Keep startup/bootstrap/provider initialization here.
2. Create the process-scoped `RuntimeStateStore`.
3. Build the `DiscordRuntimeServices` object here.
4. Register the Discord bot callback here.
5. Remove exported `handle_message(...)`.
6. Export only `main`.

The bot callback should call `message_entry.handle_message(message, config, services=services)` directly.

### `src/squire_core/transport/discord/message_entry.py`

Required changes:

1. Remove:
   - `_build_command_runtime(...)`
   - `_build_routing_runtime(...)`
   - `_build_inbound_runtime(...)`
   - `_safe_add_reaction(...)`
   - `_send_response(...)`
2. Instantiate `DiscordMessageRuntime` directly where needed.
3. Call `discord.io` directly for archive-clear response/reaction paths.
4. Keep this module focused on message-entry orchestration:
   - raw event creation/write
   - archive-clear intercept
   - explicit command vs NL routing vs capture fallthrough branching

This module should not contain hidden runtime construction graphs.

### `src/squire_core/transport/discord/message_runtime.py`

This should become the canonical Discord message-flow adapter module.

It should absorb the behavior currently split across:

- `runtime_adapter_command.py`
- `runtime_adapter_routing.py`
- `runtime_adapter_inbound.py`
- `runtime_adapter_utils.py`

Required responsibilities:

1. command runtime methods currently used by `transport.commands`
2. routing runtime methods currently used by `transport.routing`
3. inbound runtime methods currently used by `transport.inbound`
4. pending-interaction runtime methods currently used by `transport.pending_interactions`
5. view factory methods:
   - `create_pending_action_view(...)`
   - `create_mutation_pending_view(...)`
   - `create_auto_apply_feedback_view(...)`
6. IO methods:
   - `swap_reaction(...)`
   - `send_response(...)`

Guidance:

1. If the class becomes long, extract only genuinely reusable pure helpers to shared transport modules.
2. Do not split it back into command/routing/inbound adapter files unless a fresh reader would independently choose that split.
3. Do not add a new `*_utils.py` as a pressure valve.

### `src/squire_core/transport/discord/io.py`

Required changes:

1. Move the actual implementations of Discord IO behavior here from `adapter.py`.
2. This file should own:
   - message reaction add/swap
   - thread creation fallback behavior
   - Discord send error handling
3. Tests should patch or fake this module directly when behavior requires IO isolation.

### `src/squire_core/transport/discord/adapter.py`

Required changes:

1. Retain only Discord client/bot behavior here.
2. Remove standalone IO helper functions.
3. `DiscordSquireBot` remains here unless a rename materially simplifies the architecture.

Allowed contents:

1. bot class
2. bot lifecycle hooks
3. bot-specific handler type alias if still useful

Disallowed contents:

1. send/reaction helper functions now owned by `io.py`
2. duplicated message-flow orchestration

### `src/squire_core/transport/discord/views.py`

No major behavioral redesign is required.

However:

1. View code should depend on `DiscordMessageRuntime` as the concrete runtime object.
2. It should not be rewritten to reference deleted adapter module names.
3. It should remain a thin UI shell over shared pending workflows.

### `src/squire_core/transport/discord/command_contract.py`

Keep this file if it still clearly owns Discord command copy/constants/formatting.

Do not move behavior here that belongs in runtime composition or shared transport modules.

### `src/squire_core/transport/archive_clear.py`

Keep as-is unless a concrete simplification is clearly warranted.

This module is not one of the identified problem seams and should not be churned without a specific design reason.

## Test Architecture Requirements

### Core Rule

Tests must validate behavior through canonical modules and runtime objects, not through compatibility-style scaffolding.

### Required test changes

1. Remove tests that patch module-level forwarding seams in deleted runtime adapter files.
2. Remove tests that only prove one wrapper delegates to another wrapper.
3. Update tests to use one of these patterns:
   - instantiate `DiscordMessageRuntime` with explicit fakes
   - patch shared canonical modules such as:
     - `transport.mutations`
     - `transport.pending_interactions`
     - `discord.io`
     - `pending_actions`
   - assert user-visible behavior, stored state, and trace effects
4. Remove `tests/test_transport_runtime_registry.py`.
5. If a replacement test is added for top-level runtime entry, it must validate real entrypoint behavior, not wrapper delegation for its own sake.

### Test anti-patterns explicitly prohibited

1. “Module-level seams intentionally kept for test patching”
2. tests whose sole purpose is to ensure the code no longer uses an old shape
3. tests that assert a wrapper was called instead of asserting the produced behavior
4. reintroducing intermediate helper modules just because tests were patching them before

## Documentation Updates Required

1. `docs/configuration.md`
   - remove `SQUIRE_TRANSPORT` if documented
2. Any docs mentioning runtime registry/runtime selection
   - update to direct Discord runtime entry
3. If developer docs mention `runtime_adapter_*` files
   - update to `message_runtime.py`

## Recommended Implementation Order

### Phase 0: Add the End-State Runtime Object

1. create `src/squire_core/transport/discord/message_runtime.py`
2. add `DiscordRuntimeServices`
3. add `DiscordMessageRuntime`
4. move behavior into it incrementally from the adapter modules

Gate:

1. file compiles
2. no behavior changes yet required outside the new file

### Phase 1: Make `io.py` Canonical

1. move concrete IO behavior from `adapter.py` to `io.py`
2. remove `message_entry.py` helper wrappers around `io.py`
3. update call sites to use `discord.io` directly

Gate:

1. Discord command/archive-clear/message entry tests still pass

### Phase 2: Collapse Runtime Adapter Split

1. move needed command/routing/inbound methods into `DiscordMessageRuntime`
2. update `message_entry.py` to instantiate `DiscordMessageRuntime` directly
3. update shared-module call sites to use the new runtime object
4. delete:
   - `runtime_adapter_command.py`
   - `runtime_adapter_routing.py`
   - `runtime_adapter_inbound.py`
   - `runtime_adapter_utils.py`

Gate:

1. command tests pass
2. inbound/routing tests pass
3. pending interaction tests pass
4. tracing tests pass

### Phase 3: Remove Runtime Registry and Thin Trampolines

1. simplify `src/squire_core/runtime.py` to direct Discord import
2. remove `src/squire_core/transport/runtime_registry.py`
3. remove `discord/runtime.py::handle_message(...)`
4. update docs/tests for the de-generalized runtime entry

Gate:

1. runtime entry tests/bootstrap tests pass
2. no code references `runtime_registry`
3. no code references deleted runtime trampoline

### Phase 4: Behavior-First Test Cleanup

1. update tests to patch canonical seams only
2. remove tests tied to deleted wrapper modules
3. confirm no test comments or structure refer to compatibility scaffolding

Gate:

1. focused suites pass
2. full suite passes

## Validation Requirements

Minimum focused suites:

1. `tests/test_discord_commands.py`
2. `tests/test_transport_inbound.py`
3. `tests/test_otel_tracing.py`
4. `tests/test_discord_schedule.py`
5. `tests/test_transport_pending_interactions.py`
6. any tests currently covering top-level runtime/bootstrap behavior

Then run:

1. full suite: `.venv/bin/python -m pytest -q`

Static checks:

1. `python3 -m py_compile` across touched modules
2. repo symbol scan for removed module names and deleted seam names

Required post-implementation audits:

1. no references remain to:
   - `runtime_adapter_command`
   - `runtime_adapter_routing`
   - `runtime_adapter_inbound`
   - `runtime_adapter_utils`
   - `runtime_registry`
2. no comments remain saying code exists for test patching or migration compatibility

## Risks and Guardrails

### Main Risk

The main risk is replacing too much structure at once and accidentally preserving behavior only partially while simplifying the module graph.

### Guardrails

1. Preserve all current command, routing, pending, and archive-clear behavior.
2. Do not add replacement indirection just to make the change incremental.
3. If a helper is pure and transport-agnostic, prefer shared transport ownership.
4. If a helper is truly Discord-specific, put it on the real Discord runtime object or in `discord/io.py`, not in a wrapper layer.
5. Prefer deleting migration-shaped seams rather than renaming them.

## Completion Criteria

This follow-on is complete when:

1. `src/squire_core/runtime.py` directly runs Discord runtime without a registry.
2. `src/squire_core/transport/discord/io.py` is the canonical owner of Discord IO behavior.
3. `src/squire_core/transport/discord/message_entry.py` contains no runtime-construction factory graph and no local IO pass-through wrappers.
4. one explicit `DiscordMessageRuntime` object is the canonical Discord adapter for shared message flows.
5. `runtime_adapter_command.py`, `runtime_adapter_routing.py`, `runtime_adapter_inbound.py`, `runtime_adapter_utils.py`, and `runtime_registry.py` are removed.
6. tests validate behavior through canonical seams only.
7. a fresh reader would not infer that the codebase previously depended on intermediate adapter split layers, registry-based transport selection, or wrapper-oriented test seams.
