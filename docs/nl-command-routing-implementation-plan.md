# NL Routing Implementation Plan (Multi-Operation Mutation Model, Implemented)

Purpose:

- Preserve the execution-ready plan used to implement multi-operation NL mutation routing.
- Document expected behavior for maintenance and regression checks.

Status:

- Implemented on 2026-02-18.

## 1) Starting Context

Assume current runtime:

- NL routing entrypoint exists in `src/squire_core/discord_bot.py`.
- At planning time, route schema/prompt supported single-operation mutation plans.
- Pending-action confirm/apply pipeline is implemented and must be reused.
- Explicit command flows (`!done`, `!append`, `!fix`, `!confirm`, `!cancel`, `!clear-archive`) are stable.

Do not change:

- Explicit command syntax/behavior.
- Archive clear `!clear-archive` + `DELETE` safety.
- Confirmation-first mutation policy.

## 2) Deliverables

Required deliverables:

1. Schema updates:
   - `config/schemas/nl_route_intent_v1.json`
   - `config/schemas/nl_mutation_plan_v1.json`
   - `config/schemas/nl_mutation_normalized_v1.json`
2. Prompt update:
   - `config/prompts/nl_command_routing_v1.txt`
3. Runtime implementation:
   - `src/squire_core/discord_bot.py`
   - optional helper extraction into `src/squire_core/nl_planner.py`
4. Clarification runtime state:
   - in-memory (or equivalent runtime state) clarification context keyed by user+channel with TTL.
5. Tests:
   - route parsing and multi-operation interpretation
   - normalization and conflict handling
   - clarification scope enforcement and one-turn behavior
   - regressions for explicit command stability
6. Docs:
   - `docs/nl-command-routing-spec.md`
   - `docs/commands.md` (if user-visible behavior text changes)

## 3) High-Level Architecture

Two-stage NL handling:

1. Route stage:
   - `read_command | mutation_plan | clarify | capture_fallthrough | blocked_explicit_only`
2. Mutation-plan stage:
   - normalize/validate each operation
   - set operation statuses (`resolved|unresolved|cancelled_unresolved`)
   - clarification pass for unresolved operations (max one turn)
   - create pending action from resolved operations only

Execution boundary:

- LLM interpretation drives plan proposal.
- Runtime is final authority for target/field/value validation.
- Reuse existing pending-action + apply/index-refresh path.
- Do not reuse capture/create decision prompts for mutation routing.

## 4) Detailed Work Breakdown

### Step A: Schema + Prompt Upgrade

Files:

- `config/schemas/nl_route_intent_v1.json`
- `config/schemas/nl_mutation_plan_v1.json`
- `config/schemas/nl_mutation_normalized_v1.json`
- `config/prompts/nl_command_routing_v1.txt`

Tasks:

- Move mutation payload to operation model:
  - `mutation_plan.operations[]`
  - per operation: `operation_id`, `action_type`, `target_refs[]`, action payload, confidence, clarification flags.
- Ensure strict schema compatibility with OpenAI response-format requirements:
  - object `required` arrays include every property key.
  - nullable/default placeholder fields explicitly represented.
- Prompt must include:
  - clear distinction between multi-target single action vs multiple distinct actions
  - examples for conjunctions (`and`, commas, ranges, mixed actions)
  - strict output-shape placeholder discipline
  - clarification-scope behavior (unresolved operations only)

Acceptance check:

- Schema validation catches malformed outputs.
- Prompt examples include:
  - `mark 1 and 2 done`
  - `mark 1 done, append "x" to 2`
  - ambiguous mixed-field update requiring clarification.

### Step B: Route Interpreter Refactor

Files:

- `src/squire_core/discord_bot.py` (or `src/squire_core/nl_planner.py`)

Tasks:

- Consume `nl_route_intent_v1`.
- Preserve existing read-command behavior and explicit-only blocking.
- Route mutation plans into new multi-operation normalizer path.

Acceptance check:

- Existing read intent tests still pass.
- Explicit-only intents remain blocked.

### Step C: Multi-Operation Normalization Layer

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- For each operation:
  1. resolve each target ref (`row_number|object_id`)
  2. resolve object type
  3. resolve field via LLM-provided candidates with deterministic due-date/due-at disambiguation
  4. normalize values (enum/date/datetime)
  5. validate against strict allowlists
- Assign per-operation status:
  - `resolved`
  - `unresolved`
  - `cancelled_unresolved` (after failed clarification turn)
- Detect and mark conflicts (`operation_conflict`) for same target+field conflicting writes.
- Produce normalized artifact (`nl_mutation_normalized_v1`) with per-operation reason codes.

Acceptance check:

- Multi-target single-action mutation normalizes to multiple concrete operations.
- Mixed-action request normalizes into separate operation groups.
- Conflict cases are deterministically marked unresolved.

### Step D: Clarification Context + State Machine

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- Add clarification context store keyed by `(user_id, channel_id)` with TTL.
- Store immutable plan snapshot + unresolved operation descriptors.
- Intercept next user message when context is active:
  - treat as clarification delta for unresolved operations only
  - reject out-of-scope additions/rewrites
- Enforce one-turn clarification policy:
  - `max_turns = 1`
  - unresolved after this turn -> `cancelled_unresolved`
- On out-of-scope clarification, return required copy:
  - `Before I can proceed with any other actions, I need clarification on the unresolved parts of the previous request. You may cancel your last action if you'd like to take a new action now.`
  - include unresolved summary line.

Acceptance check:

- Clarification reply updates unresolved operations only.
- Out-of-scope reply is hard-blocked with required copy.
- No second clarification turn is allowed.

### Step E: Confirmation + Apply Semantics

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- After clarification pass:
  - if no resolved operations remain: cancel flow and send deterministic no-apply response.
  - if resolved operations remain: ask for confirm/cancel and apply resolved operations only.
- Reuse existing pending-action view and apply path.
- Keep explicit confirmation requirement.

Acceptance check:

- Partial apply is confirm-gated.
- Unresolved/cancelled operations are explicitly listed before confirmation.

### Step F: Telemetry and Reason Codes

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- Emit existing NL plan telemetry plus:
  - `nl_plan_unresolved_cancelled`
  - `nl_clarification_scope_blocked`
- Attach operation-level reason codes in trace logs/artifacts.

Reason codes to support:

- `target_missing`
- `target_no_cursor`
- `target_expired`
- `target_out_of_range`
- `target_unknown_id`
- `target_wrong_type`
- `field_unknown`
- `field_ambiguous`
- `value_parse_failed`
- `validation_failed`
- `operation_conflict`
- `out_of_scope_clarification`
- `clarification_insufficient`
- `clarification_timeout`
- `explicit_only`

## 5) Test Plan (Required)

Add/extend tests in:

- `tests/test_discord_commands.py`
- `tests/test_nl_command_routing_config.py`
- `tests/test_nl_mutation_normalization.py`
- new: `tests/test_nl_multi_operation_clarification.py`

Must-cover scenarios:

1. Multi-target same action:
   - `mark 1 and 2 done` -> one plan operation, two targets, confirmation flow.
2. Multi-action mixed request:
   - `mark 1 done, append "x" to 2` -> two operations.
3. Range handling:
   - `mark 1-3 done` -> expanded target refs and validated.
4. Partial legality:
   - one target valid admin, one wrong type -> unresolved reason and partial confirm path.
5. Conflict handling:
   - conflicting field writes on same target -> unresolved conflict.
6. Clarification success:
   - unresolved op clarified in one turn -> status moves to resolved.
7. Clarification failure:
   - unresolved remains unclear after one turn -> `cancelled_unresolved`.
8. Out-of-scope clarification reply:
   - blocked with required copy + unresolved summary.
9. No resolved operations remain:
   - flow cancels without writes.
10. Explicit command regressions unchanged.

## 6) Validation Commands

Run at minimum:

1. `.venv/bin/python -m pytest -q tests/test_discord_commands.py tests/test_nl_command_routing_config.py tests/test_nl_mutation_normalization.py tests/test_nl_multi_operation_clarification.py`
2. `.venv/bin/python -m pytest -q tests/test_surfacing.py tests/test_discord_schedule.py`
3. `.venv/bin/python -m py_compile src/squire_core/discord_bot.py src/squire_core/config_utils.py`

If tests fail, fix before handoff.

## 7) Rollout Strategy

- This behavior is default-on once implemented (no new feature flag).
- Keep existing route confidence thresholds.
- Remove legacy single-operation-only assumptions in prompt/schema/runtime.

## 8) Handoff Checklist

Before handoff, ensure:

- new schemas and prompt committed
- runtime state-machine behavior implemented and tested
- required out-of-scope block message exactly matches spec
- operation statuses and reason codes are persisted in normalized trace artifact
- tests passing for touched areas
- `.agent/context.md` updated with summary + test results

## 9) Out-of-Scope Follow-ups

- Auto-apply without confirmation.
- Undo/revert transaction framework.
- Persisting clarification context to durable storage across restarts.
