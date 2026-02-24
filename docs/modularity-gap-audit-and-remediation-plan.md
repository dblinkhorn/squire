# Modularity Gap Audit and Remediation Plan

Date: 2026-02-23
Scope: post-flow removal modularity hardening for transport architecture

## Goal

Make the application structure reflect true ground-up modular design:

1. transport-agnostic composition root
2. explicit adapter boundaries
3. no compatibility aliases/shims preserved as architecture
4. no hidden cross-runtime mutable coupling

## Current Findings

### M1. Runtime composition root is transport-coupled

Evidence:
- `src/squire_core/runtime.py` imports `squire_core.transport.discord.runtime` directly.

Why this is a gap:
- top-level runtime is not truly transport-agnostic
- adding a new transport still requires editing composition root code

Target state:
- runtime resolves transport via registry/selector, not direct adapter import

Status:
- Resolved in this pass via `src/squire_core/transport/runtime_registry.py` + `src/squire_core/runtime.py` cutover.

### M2. Discord orchestration remains a transitional compatibility surface

Evidence:
- historical issue: `src/squire_core/transport/discord/orchestration.py` had aggregated helpers/state/constants and remained a test import target.

Why this is a gap:
- old monolith shape still influences boundaries
- heavy module remains a coupling hub for unrelated concerns

Target state:
- thin event-entry orchestration only
- transport/business helpers live in focused modules

Status:
- Resolved in this pass:
  - removed `src/squire_core/transport/discord/orchestration.py`
  - added focused Discord ingress module `src/squire_core/transport/discord/message_entry.py`
  - rewired `src/squire_core/transport/discord/runtime.py` to use message-entry directly

### M3. Compatibility alias remnants (`SquireBot`)

Evidence:
- historical alias locations during migration:
  - `src/squire_core/transport/discord/adapter.py`
  - `src/squire_core/transport/discord/__init__.py`
  - `src/squire_core/transport/discord/orchestration.py`

Why this is a gap:
- preserves old naming compatibility beyond migration necessity

Target state:
- canonical class name only: `DiscordSquireBot`

Status:
- Resolved in this pass; alias removed and tests migrated to canonical class/seams.

### M4. Runtime adapter module is too broad

Evidence:
- `src/squire_core/transport/discord/runtime_adapters.py` contains large command/routing/inbound adapters in one file.

Why this is a gap:
- mixed responsibilities reduce replaceability and test isolation

Target state:
- split by flow responsibility (`command_adapter`, `routing_adapter`, `inbound_adapter`) or equivalent composition

Status:
- Resolved in this pass:
  - added focused adapter modules:
    - `src/squire_core/transport/discord/runtime_adapter_command.py`
    - `src/squire_core/transport/discord/runtime_adapter_routing.py`
    - `src/squire_core/transport/discord/runtime_adapter_inbound.py`
  - removed adapter hub file `src/squire_core/transport/discord/runtime_adapters.py`
  - adapter-shared helpers now live in `src/squire_core/transport/discord/runtime_adapter_utils.py`

### M5. Shared transport state uses process-global mutable singletons

Evidence:
- historical issue: transport state was held in module-level mutable containers.

Why this is a gap:
- implicit cross-runtime coupling
- harder multi-transport/process isolation
- tests must mutate global module state directly

Target state:
- injected state store/context per runtime/transport instance

Status:
- Fully resolved:
  - added `RuntimeStateStore` in `src/squire_core/transport/state.py`
  - Discord runtime now creates a dedicated `RuntimeStateStore` per process instance and injects it through message-entry/runtime adapters
  - cursor/affinity/archive-clear/clarification operations now execute through injected store paths
  - removed `DEFAULT_RUNTIME_STATE` and all module-level fallback activation from `src/squire_core/transport/state.py`
  - `tests/conftest.py` now provides explicit per-test `RuntimeStateStore` fixtures (no singleton monkeypatch seam)

### M6. Reminder schedule change notification is hidden in config dict key

Evidence:
- historical issue: reminder schedule update callbacks were plumbed via hidden config dict key.

Why this is a gap:
- hidden dependency contract
- magic key coupling, weak type/contract visibility

Target state:
- explicit scheduler notifier interface/callback dependency

Status:
- Resolved in this pass:
  - removed config-key notifier coupling from `src/squire_core/transport/reminders.py`
  - runtime path now passes explicit due-time reminder notifier callback from Discord runtime/scheduler into message-entry/runtime-adapters
  - transport command/routing/inbound/mutation protocols now call notifier through explicit runtime method with no config-key dependency

### M7. Test architecture still anchors to orchestration surface

Evidence:
- historical issue: Discord tests imported and patched orchestration internals.

Why this is a gap:
- tests can preserve transitional architecture
- raises friction for removing compatibility surfaces

Target state:
- tests patch canonical seams (`transport.*`, `runtime_adapters`, explicit adapter modules)
- orchestration internals not primary seam for behavior tests

Status:
- Resolved in this pass:
  - migrated Discord tests off orchestration imports to canonical seams (`message_entry`, `transport.commands`, `transport.routing`, `transport.state`, `transport.targeting`, `transport.discord.command_contract`, `transport.discord.views`)

## Remediation Plan

## Phase A: Composition Root Decoupling

1. introduce transport runtime registry/selector
2. remove direct Discord import from `src/squire_core/runtime.py`
3. default behavior remains Discord with no UX change

Gate:
- runtime-focused compile/tests pass

Status:
- Complete (2026-02-23)

## Phase B: Remove Explicit Compatibility Aliases

1. remove adapter-level `SquireBot` alias/export
2. migrate remaining internal test usage to `DiscordSquireBot` or canonical seam
3. remove orchestration alias/surface once no call sites remain

Gate:
- Discord schedule/startup tests pass

Status:
- Complete (2026-02-23): adapter/export naming alias removal plus orchestration surface removal

## Phase C: Orchestration Slimming

1. split orchestration responsibilities into focused modules (command entry, inbound entry, archive-clear flow)
2. keep `orchestration.py` as thin delegator or remove when fully migrated

Gate:
- command/routing/inbound parity suite passes
- no compatibility-only re-exports remain

Status:
- Complete (2026-02-23): message/archive ingress moved to focused module and orchestration module removed.

## Phase D: State/Notifier Contract Hardening

1. replace module-global state containers with injected state store
2. replace `_due_time_reminder_notify` magic config key with explicit notifier contract

Gate:
- cursor/clarification/reminder tests pass with isolated runtime state instances

Status:
- Complete (2026-02-23):
  - state-store injection completed for active Discord runtime path
  - notifier contract hardening completed; Phase D gate suite passing
  - follow-on cleanup complete: tests now isolate and reference explicit per-test runtime stores.

## Phase E: Adapter Module Decomposition

1. split `runtime_adapters.py` into narrower modules
2. retain same runtime contracts during split

Gate:
- contract-bridge tests pass
- import graph remains acyclic

Status:
- Complete (2026-02-23):
  - adapter decomposition landed with focused command/routing/inbound modules.
  - removed transitional adapter hub module (`runtime_adapters.py`) and moved imports/tests to direct module seams.
  - focused validation suite: `83 passed` across Discord contract/runtime + shared transport tests.

## Post-Phase Hardening (Strict DI Cleanup)

1. remove Discord runtime-path fallback to global `DEFAULT_RUNTIME_STATE`
2. remove adapter re-export surface from runtime composition module
3. stop adapter modules from instantiating sibling adapters internally; use explicit factory injection from composition boundary

Status:
- Complete (2026-02-23):
  - runtime-path state is now explicitly injected through `message_entry` and adapter constructors.
  - shared state helper APIs now require explicit `RuntimeStateStore` injection, with no global fallback path.
  - `src/squire_core/transport/discord/runtime.py` exports only runtime entrypoints (`main`, `handle_message`).
  - adapter-to-adapter construction moved to explicit factories wired in `src/squire_core/transport/discord/message_entry.py`.
  - focused validation suite: `66 passed` for Discord/transport modular seams exercised in this pass.
  - full suite baseline unchanged except sandbox socket-bind limitation (`142 passed`, `2 failed` in `tests/test_health_server.py` under restricted sandboxing).

## Validation Policy for Every Phase

1. preserve Discord UX and command behavior text parity
2. run focused suite:
   - `tests/test_discord_commands.py`
   - `tests/test_discord_contract_bridge.py`
   - `tests/test_discord_schedule.py`
   - `tests/test_nl_multi_operation_clarification.py`
   - `tests/test_surfacing_cursor.py`
   - `tests/test_test_mode_startup.py`
   - `tests/test_nl_mutation_normalization.py`
   - `tests/test_transport_routing.py`
3. do not add new compatibility shims as permanent architecture
