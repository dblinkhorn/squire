# Natural-Language Routing Spec (Operation Plan Model)

## Problem

The current NL mutation path is too coupled to command syntax:

- NL is interpreted into command-shaped args (`!fix field=value`, `!done 2`, etc.).
- This causes brittle failures when user language is semantically clear but command arguments are not canonical (for example `date` vs `due_date`).
- Safety is present, but usability is constrained by command grammar.

This spec defines a better architecture:

- NL interpretation should produce a structured mutation plan.
- Deterministic normalization/validation should enforce safety.
- Confirmation should apply the plan, not a reconstructed command string.

## Goals

- Keep LLM as the primary interpretive layer for user intent.
- Remove command-string coupling for NL mutation handling.
- Preserve deterministic safety gates and explicit-only destructive controls.
- Support robust synonym handling (`date`, `deadline`, `move to`, etc.) through normalized field semantics.
- Keep explicit `!` commands unchanged.

## Non-Goals

- Replacing explicit command handlers.
- Adding new destructive operations.
- Removing strict validation from mutation writes.

## Invariants

- Explicit `!` commands keep highest precedence.
- `DELETE` archive confirmation flow remains unchanged.
- `clear-archive`, `confirm`, `cancel` remain explicit-only from NL input.
- NL mutation intents remain confirmation-first.
- All writes continue to flow through canonical apply + index refresh + semantic sync behavior.

## Scope

Applies to non-`!` DM messages.

Read intents still map to existing command handlers (`status`, `weekly`, `recent`, `find`, `show`).

Mutation intents move to a new plan-first flow (`done`, `append`, `fix` semantics via structured plans).

## Architecture Overview

### 1) NL Route Interpretation

Run a route interpreter pass first.
It decides one of:

- `read_command`
- `mutation_plan`
- `clarify`
- `capture_fallthrough`
- `blocked_explicit_only`

### 2) Read Command Handling

For `read_command`, execute existing command handlers directly.
Keep current confidence/clarification policy.

### 3) Mutation Plan Handling

For `mutation_plan`, do **not** build a command string.
Use a typed plan object that contains:

- target reference
- mutation action type
- field changes / append text
- confidence and ambiguity markers

### 4) Deterministic Normalization + Validation

Normalize plan semantics against canonical model:

- target resolution (numbered cursor or explicit ID)
- object type resolution
- field alias normalization
- value parsing/normalization (dates, datetimes, enums)
- strict allowlist validation by object type

If normalization is incomplete or unsafe, clarify instead of writing.

### 5) Confirmation + Apply

High-confidence valid mutation plans create pending actions and show Confirm/Cancel buttons.
Confirm applies the normalized plan through existing apply path.

## Data Contracts

### A) Route Output Schema (`nl_route_intent_v2`)

Required top-level fields:

- `schema_version`
- `route` (`read_command|mutation_plan|clarify|capture_fallthrough|blocked_explicit_only`)
- `intent`
- `risk_tier` (`read|mutation|destructive|control|none`)
- `confidence` (0..1)
- `ambiguities` (array)
- `read_command` (nullable object)
- `mutation_plan` (nullable object)
- `clarification` (nullable object)

### B) Mutation Plan Schema (`nl_mutation_plan_v1`)

Required:

- `action_type` (`mark_done|append_body|set_fields`)
- `target_ref`:
  - `kind` (`row_number|object_id`)
  - `value` (string/int)
- `field_updates` (array of typed updates; empty unless `set_fields`)
- `append_text` (nullable string; only for `append_body`)
- `raw_user_phrases` (optional map for traceability)
- `confidence` (0..1)

Optional:

- `object_type_hint`
- `requires_clarification`
- `clarification_reason`

### C) Normalized Plan Artifact (`nl_mutation_normalized_v1`)

Persisted as derived artifact before pending action write.

Includes:

- `raw_event_id`
- `plan_input`
- `target_resolved_id`
- `target_object_type`
- `normalized_fields`
- `normalization_notes`
- `validation_outcome` (`ok|clarify|blocked`)

## Normalization Rules

### 1) Target Resolution

- `row_number` resolves via existing cursor context (`!recent|!find|!status|!weekly`).
- Respect cursor expiry and thread-parent fallback behavior.
- Out-of-range/missing cursor -> clarify with actionable guidance.

### 2) Field Alias Resolution

Apply deterministic alias maps by object type before validation.

Examples (admin):

- `date` / `deadline` / `due` -> `due_date` unless time-of-day is present
- `time` / `at` with date/time phrase -> `due_at`
- `task` / `title` phrase -> `title`

Examples (projects):

- `deadline` -> `due`

Alias resolution must be deterministic and logged.

### 3) Value Normalization

- Date-like values normalize to ISO date (`YYYY-MM-DD`) when field requires date.
- Datetime-like values normalize to ISO datetime with timezone when required.
- Enum-like values normalize to canonical enum tokens.
- Unknown/unparseable value -> clarify.

### 4) Canonical Validation

After normalization, reuse strict existing allowlists and validators.
No relaxed writes are allowed.

## Clarification Policy

Send clarification (not write) when any of these are true:

- intent confidence below mutation threshold
- target ambiguous or unresolved
- field aliases conflict (for example both `due_date` and `due_at` candidates with equal evidence)
- value normalization fails
- update set would violate object-type validation

Clarification messages should propose 2-3 concrete options and avoid command jargon where possible.

## Safety Policy

- `clear_archive`, `confirm_pending`, `cancel_pending` remain blocked from NL execution.
- NL mutation remains confirmation-first.
- `Was this incorrect?` remains post-write recovery only.
- No auto-apply of NL mutation plans without explicit confirmation in this phase.

## Telemetry

Keep existing route telemetry and add plan-stage logs:

- `nl_plan_generated`
- `nl_plan_normalized`
- `nl_plan_clarified`
- `nl_plan_blocked`
- `nl_plan_pending_created`
- `nl_plan_confirm_applied`

Include reason codes for failures:

- `target_missing`
- `target_out_of_range`
- `field_unknown`
- `value_parse_failed`
- `validation_failed`
- `explicit_only`

## Config

Retain `nl_command_routing` settings and add:

- `nl_command_routing.mutation_plan_enabled` (default `true`)
- `nl_command_routing.plan_auto_aliasing` (default `true`)
- `nl_command_routing.plan_trace_enabled` (default `true`)

Read/mutation confidence gates remain:

- `read_auto_min_confidence`
- `mutation_confirm_min_confidence`

## Migration Plan

Phase 1:

- Keep existing read-command routing.
- Add new mutation plan schemas + prompt.
- Keep old mutation command-string path behind temporary fallback flag.

Phase 2:

- Switch NL mutation path to plan-first normalization + pending action creation.
- Keep fallback disabled by default.

Phase 3:

- Remove old mutation command-string conversion path.
- Keep all explicit `!` commands unchanged.

## Acceptance Criteria

- NL mutation phrasing like `change number 2 date to feb 18` resolves to canonical due-field update path (or clear clarification), not raw-field rejection on `date`.
- Mutation writes are never applied without confirmation.
- Explicit-only controls remain blocked from NL.
- Existing explicit commands continue to pass unchanged.
- Telemetry and derived plan artifacts make route decisions auditable.

## Related Docs

- `docs/commands.md`
- `docs/data-model.md`
- `docs/matching-spec.md`
- `docs/nl-command-routing-implementation-plan.md`
