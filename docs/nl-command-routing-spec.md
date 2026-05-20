# Natural-Language Routing Spec (Multi-Operation Mutation Model)

## Problem

Earlier NL mutation implementations were single-operation and did not reliably handle:

- multi-target phrasing (`mark 1 and 2 done`)
- multi-action phrasing (`mark 1 done, append "x" to 2`)
- clarification continuation in natural language without falling into capture flow

This creates brittle behavior where valid user intent can be partially interpreted, then lost.

## Goals

- Keep LLM as the primary interpretation layer for NL command intent.
- Support multi-operation and multi-target NL mutation requests.
- Preserve deterministic safety/validation as final authority before writes.
- Keep explicit `!` command behavior unchanged.
- Keep mutation writes confirmation-first.
- Support NL clarification continuation with strict scope controls.

## Non-Goals

- Replacing explicit command handlers.
- Auto-applying NL mutations without confirmation.
- Allowing clarification turns to expand or rewrite the original plan scope.

## Invariants

- Explicit `!` commands keep highest precedence.
- `clear-archive`, `confirm`, and `cancel` remain explicit-only from NL input.
- Mutation writes are never applied without explicit confirmation.
- Mutation routing must be grounded by target evidence: an explicit row/object target, a scoped clarification target, or a strong local match against eligible existing notes.
- Runtime validators remain authoritative for allowed fields/value formats.
- Clarification flow is immutable-scope: unresolved parts only.

## Scope

Applies to non-`!` DM messages routed through NL command routing.

Read intents:

- `status`, `weekly`, `recent`, `find`, `show`

Mutation intents:

- `done`, `reopen`, `append`, `fix`
- now interpreted as one or more structured operations

## Architecture Overview

### 1) Route Interpretation

Route output decides:

- `read_command`
- `mutation_plan`
- `clarify`
- `capture_fallthrough`
- `blocked_explicit_only`

### 2) Mutation Plan Model

For `mutation_plan`, model output is operation-based (no command strings):

- `mutation_plan.operations[]`
- each operation can target one or many references (`target_refs[]`)

Runtime normalizes each operation independently, assigns status, and only allows resolved operations to proceed.

### 3) Clarification State Machine

When any operation is unresolved:

1. freeze original plan snapshot (immutable scope)
2. ask one clarification question covering unresolved operations only
3. interpret user clarification reply as a delta against unresolved operations only
4. if still unresolved after this single turn, mark those operations `cancelled_unresolved`
5. show summary and ask user to confirm resolved operations only

If no operations are resolved, cancel entire plan and do not apply anything.

### 4) Interpretation Boundary (LLM vs Runtime)

- LLM handles semantic interpretation and plan proposal.
- Runtime is final authority for:
  - target resolution
  - field resolution
  - value normalization
  - allowlist/type validation
  - operation status assignment

## Data Contracts

## A) Triage Schema (`message_triage_v1`)

Required top-level fields:

- `schema_version`
- `route`
- `intent`
- `risk_tier`
- `confidence`
- `ambiguities`
- `read_command` (nullable)
- `mutation_plan` (nullable)
- `clarification` (nullable)

`route` values:

- `read_command|mutation_plan|clarify|capture_fallthrough|blocked_explicit_only`

## B) Mutation Plan Schema (`nl_mutation_plan_v1`)

Required:

- `operations` (array, min 1)
- `confidence` (overall plan confidence)

Each operation requires:

- `operation_id` (stable identifier in this plan, e.g. `op_1`)
- `action_type` (`mark_done|append_body|set_fields`)
- `target_refs` (array, min 1)
  - each target ref:
    - `kind` (`row_number|object_id`)
    - `value` (string/int)
- `append_text` (nullable; used by `append_body`)
- `field_updates` (array; used by `set_fields`)
  - each update:
    - `value_text`
    - `source_phrase` (nullable)
    - `field_candidates`:
      - `primary` (`field_id`, `confidence`) nullable
      - `alternates[]` (`field_id`, `confidence`)
- `confidence` (per-operation confidence)
- `requires_clarification` (bool)
- `clarification_reason` (nullable string)

## C) Normalized Plan Artifact (`nl_mutation_normalized_v1`)

Persist before pending-action creation.

Includes:

- `raw_event_id`
- `plan_input`
- `operations[]` with normalization outcome:
  - `operation_id`
  - `action_type`
  - `target_ref`
  - `target_token`
  - `target_resolved_id`
  - `target_object_type`
  - `op_status` (`resolved|unresolved|cancelled_unresolved`)
  - `reason_code` (nullable)
  - `normalization_notes[]`
  - `normalized_fields`
  - `proposed_operation` (nullable concrete canonical op)
- `summary`:
  - `total_operations`
  - `resolved_count`
  - `unresolved_count`
  - `cancelled_unresolved_count`
- `validation_outcome` (`ok|clarify|partial|blocked`)

## D) Clarification Context (Ephemeral Runtime State)

Store per user+channel with TTL:

- `raw_event_id`
- immutable `base_plan_input` snapshot
- `unresolved_scope` keyed by operation ID (action/reason/target details)
- `expires_at`

This context is checked before normal NL routing/capture for the next user reply.
The clarification context is consumed on that reply, enforcing the single-turn clarification policy.

## Normalization Rules

### 1) Target Resolution

For each `target_ref`:

- `row_number`: resolve via existing numbered cursor behavior
- `object_id`: resolve direct canonical object lookup
- missing target references: before clarification, runtime may ground a single-target mutation candidate against eligible existing notes using local matching only

Failure reasons include:

- missing cursor
- expired cursor
- out-of-range row number
- unknown object ID
- no grounded local target

Eligibility for local target grounding is intent-specific:

- `append`, `fix`, and `done`: open notes only
- `reopen`: done notes only

When no local target is grounded and capture classification is confident, runtime falls through to normal capture instead of asking mutation-oriented clarification.

### 2) Field Resolution

Use LLM candidate fields, then deterministic runtime resolution:

1. accept canonical `primary.field_id` if valid for object type
2. evaluate alternates
3. if both `due_date` and `due_at` are viable, choose by time-hint in `value_text`
4. if multiple non-compatible candidates remain, mark unresolved (`field_ambiguous`)

### 3) Value Normalization

- date-like -> ISO date (`YYYY-MM-DD`)
- datetime-like -> ISO datetime with timezone where required
- enum-like -> canonical enum token
- parse/format failures -> unresolved

### 4) Validation

Apply existing strict validators and allowlists.
Never bypass validation based on model confidence.

## Multi-Operation Semantics

### 1) Execution Ordering

- Resolved operations are applied in original operation order.

### 2) Conflicts

If multiple resolved operations conflict on the same target+field in one plan:

- mark conflicting operations unresolved with `reason_code=operation_conflict`
- include in clarification (single turn policy)
- if still unresolved after clarification, mark `cancelled_unresolved`

### 3) Limits

Runtime should enforce hard caps (deterministic guardrails), for example:

- max operations per plan
- max targets per operation

Ranges (`1-5`) are interpreted by the LLM into explicit `target_refs`; runtime validates each target deterministically.

## Clarification Policy (Strict)

### 1) Single Clarification Turn

- Exactly one clarification reply is allowed (`max_turns=1`).
- Remaining unresolved operations after this turn become `cancelled_unresolved`.

### 2) Immutable Scope

During clarification, hard-block out-of-scope replies that add/change unrelated actions.

Required user-facing message:

`Before I can proceed with any other actions, I need clarification on the unresolved parts of the previous request. You may cancel your last action if you'd like to take a new action now.`

Also include a concise unresolved summary line.

### 3) Final Confirmation

After clarification pass:

- if `resolved_count == 0`: cancel entire plan, no apply
- if `resolved_count > 0`: ask for confirmation to apply resolved operations only
- unresolved/cancelled operations are listed explicitly

## Reason Codes

Use reason codes for telemetry/artifacts and user-facing mapping:

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

## Safety Policy

- Explicit-only controls remain blocked from NL execution.
- No mutation writes without confirmation.
- Clarification cannot expand plan scope.
- Partial apply requires explicit confirmation after unresolved cancellation.

## Telemetry

Retain existing route telemetry and add/extend:

- `nl_plan_generated`
- `nl_plan_normalized`
- `nl_plan_clarified`
- `nl_plan_blocked`
- `nl_plan_pending_created`
- `nl_plan_confirm_applied`
- `nl_plan_unresolved_cancelled`
- `nl_clarification_scope_blocked`

Each event should include operation-level reason codes where applicable.

## Config

This behavior is the default once shipped.

No separate feature flag for multi-operation clarification mode should be required.
Existing NL routing config remains for confidence gates and trace behavior.

## Acceptance Criteria

- `mark 1 and 2 done` is interpreted as one operation with two targets and reaches confirmation (when both valid admin targets).
- `mark 1 done, append "hello" to 2` is interpreted as two operations and reaches confirmation for resolved operations.
- Out-of-scope clarification reply is hard-blocked with required message and unresolved summary.
- After one clarification turn, still-unresolved operations become `cancelled_unresolved`.
- Resolved operations are never applied without explicit user confirmation.
- Existing explicit commands continue to behave unchanged.

## Related Docs

- `docs/commands.md`
- `docs/data-model.md`
- `docs/nl-command-routing-implementation-plan.md`
