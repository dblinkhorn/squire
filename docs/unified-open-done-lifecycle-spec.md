# Unified Open/Done Lifecycle Spec

Date: 2026-03-30
Status: Draft for review before implementation

## Purpose

Replace Squire's mixed lifecycle model (`done`, `completed`, `archived`, per-type active status vocabularies) with one shared user and system contract:

- every canonical note has `status: open|done`
- `!done` is the primary close/remove-from-surfacing action for every note type
- `!reopen` is the inverse for every note type
- user-visible wording should say `done` / `mark done` / `reopen`, not `complete` / `completed`
- the final codebase should read as if Squire had always used this model; no compatibility seams, transitional abstractions, legacy aliases, or mixed old/new lifecycle support should remain

This spec is intended to be detailed enough for a fresh agent session to implement the change without re-discovering scope.

## Product Decisions Already Made

These were explicitly confirmed in this session:

1. `done` is the only public “finish” action.
2. Every note type should use the same lifecycle vocabulary.
3. Reopening an item must preserve any existing due date/time.
4. If a reopened item is now in the past, it should surface as overdue immediately.
5. User-facing wording that currently says `complete` / `completed` should become `done` / `mark done`.
6. Using `done` for people notes is acceptable; it means “I’m done with this for now / stop surfacing it.”
7. Existing legacy lifecycle statuses should be collapsed away rather than preserved.
8. Code paths for former lifecycle statuses should be removed rather than adapted.
9. Natural-language reopen should be supported alongside explicit `!reopen`.
10. `completed_at` should be renamed to `done_at` everywhere.
11. No lifecycle migration/compatibility support is needed for old notes; unsupported old notes may simply be recreated manually.

## Goals

1. One mental model for active vs inactive notes.
2. One explicit close command (`!done`) and one inverse (`!reopen`).
3. Consistent status handling across explicit commands, NL routing, capture defaults, surfacing, reminders, matching, and docs.
4. Remove `archived` and per-type close semantics from the active runtime/data model.
5. Preserve searchability and auditability for done notes.

## Non-Goals

1. Reworking transport/runtime architecture.
2. Adding new note types.
3. Reworking pending-action status (`pending|confirmed|cancelled|failed`); that status is unrelated.
4. Changing due date/time semantics beyond reopen behavior.
5. Preserving backwards compatibility for legacy canonical lifecycle formats.

## Day-One Consistency Rule

This implementation must follow the same standard used in prior refactor cleanup work:

If a module, type, field, function, branch, fallback, test seam, or doc phrase would only exist because Squire used to have lifecycle concepts like `archived`, `completed`, `blocked`, `planning`, `in_progress`, `on_hold`, `seed`, `incubating`, `active`, or `parked`, it should not exist in the final state.

Practical implications:

1. No long-lived dual-format lifecycle support.
2. No compatibility aliases like “done or completed”.
3. No transitional helper layers whose purpose is to map old lifecycle states to new ones at runtime.
4. No tests that preserve legacy lifecycle branches “just in case”.
5. No docs that describe the old lifecycle as still meaningful behavior.
6. The finished code should look like the app was designed this way from day one.

## Current State Audit

The current implementation spreads lifecycle logic across several separate concepts:

### Canonical/Data Model

- [`config/schemas/canonical_object_v1.json`](../config/schemas/canonical_object_v1.json)
  - base requires `archived: boolean`
  - `people` currently has no `status`
  - `projects.status` enum: `planning|in_progress|blocked|completed|on_hold`
  - `ideas.status` enum: `seed|incubating|active|parked|done`
  - `admin.status` enum: `open|done|blocked`
  - `admin` uses `completed_at`
- [`config/schemas/derived_event_people_v1.json`](../config/schemas/derived_event_people_v1.json)
  - no status field today
- [`config/schemas/derived_event_admin_v1.json`](../config/schemas/derived_event_admin_v1.json)
  - includes `blocked` and `completed_at`
- [`config/schemas/derived_event_projects_v1.json`](../config/schemas/derived_event_projects_v1.json)
  - includes current project status enum
- [`config/schemas/derived_event_ideas_v1.json`](../config/schemas/derived_event_ideas_v1.json)
  - includes current idea status enum

### Explicit Commands / NL Routing

- [`src/squire_core/transport/commands.py`](../src/squire_core/transport/commands.py)
  - `!done` writes `status=done` plus `completed_at`
  - no `!reopen`
- [`src/squire_core/transport/mutations.py`](../src/squire_core/transport/mutations.py)
  - blocks `status=done` for non-admin items
- [`src/squire_core/transport/routing.py`](../src/squire_core/transport/routing.py)
  - NL `mark_done` only resolves for admin items
  - no reopen intent/action
- [`config/prompts/message_triage_v1.txt`](../config/prompts/message_triage_v1.txt)
  - says NL done means “mark one admin item done”

### Validation / Fix Guidance

- [`src/squire_core/transport/validation.py`](../src/squire_core/transport/validation.py)
  - type-specific status enums
  - admin-only `completed_at`
- [`src/squire_core/transport/discord/runtime_adapter_command.py`](../src/squire_core/transport/discord/runtime_adapter_command.py)
  - `!fix` guidance examples mention `status=blocked`, `status=active`, etc.

### Surfacing / Reminders / Matching / Indexing

- [`src/squire_core/surfacing.py`](../src/squire_core/surfacing.py)
  - active filtering combines `archived`, `done`, `completed`, and people-with-no-status assumptions
  - weekly review “Completed this week” mixes archived admin/project/idea semantics
  - admin without due dates includes `open|blocked`
  - project attention depends on `blocked` and `planning|in_progress` stale logic
- [`src/squire_core/transport/discord/scheduler.py`](../src/squire_core/transport/discord/scheduler.py)
  - due reminders allow `open|blocked`
  - rechecks `archived`
- [`src/squire_core/indexer.py`](../src/squire_core/indexer.py)
  - persists `archived` column
- [`src/squire_core/matching.py`](../src/squire_core/matching.py)
  - semantic active-record collection excludes `archived`
  - status modifier special-cases `done|completed`

### Seed/Test Fixtures

- [`src/squire_core/test_seed.py`](../src/squire_core/test_seed.py)
  - includes blocked admin/projects, completed projects, active ideas, done ideas, no-status people

### Docs / Help Copy

- [`README.md`](../README.md)
- [`docs/commands.md`](./commands.md)
- [`docs/data-model.md`](./data-model.md)
- [`docs/surfacing.md`](./surfacing.md)
- [`docs/configuration.md`](./configuration.md)
- [`docs/querying.md`](./querying.md)
- [`docs/numbered-mutations-spec.md`](./numbered-mutations-spec.md)
- [`docs/due-time-reminders-spec.md`](./due-time-reminders-spec.md)
- [`docs/test-env-reset-seed-spec.md`](./test-env-reset-seed-spec.md)

These docs currently describe archived/completed/blocked status behavior that will become stale after the change.

## Target Model

### Canonical Lifecycle Contract

Every canonical object must have:

- `status: "open" | "done"`
- `done_at` as an optional timestamp set when a note is marked done

Required canonical rules:

- `status=open` means the note belongs in active surfacing unless another feature explicitly filters it out
- `status=done` means the note is removed from active surfacing, but still searchable/showable and eligible for “Done this week”
- `!done` sets `status=done` and `done_at=<now>`
- `!reopen` sets `status=open`
- `!reopen` clears `done_at`
- due dates/times are preserved when reopening

### Per-Type Handling

Required unified lifecycle meaning:

- `admin`: open or done
- `projects`: open or done
- `ideas`: open or done
- `people`: open or done

Additional agreed rule:

- `blocked_reason` remains as an optional text field
- “blocked” is not a stored lifecycle state
- if a note has a non-empty `blocked_reason`, any legacy “blocked” behavior should be derived from that fact alone rather than from a separate status value

### Active Filtering

Active/inactive logic should become:

- active if `status=open`
- inactive if `status=done`

No active filtering should depend on `archived`, `completed`, or per-type status enums after this change.

Staleness should not exist as a surfacing concept. There should be no stale-project threshold, no stale-project config, and no code that derives a stale state from `updated_at`.

### Weekly Review

Required rename:

- section title: `Done this week`

Required inclusion rule:

- include any item with `status=done` and `done_at` within the configured weekly lookback

### Reopen Behavior

`!reopen` and NL reopen should:

1. set `status=open`
2. clear `done_at`
3. leave due fields untouched
4. make the item eligible for normal active surfacing immediately
5. if the due date/time is now past, allow normal overdue surfacing/reminder logic to treat it as overdue

## Legacy Lifecycle Removal Policy

Because you do not need compatibility for existing notes, this work should remove legacy lifecycle concepts outright instead of shipping a migration layer.

Required policy:

1. Update schemas, runtime logic, fixtures, prompts, tests, and docs to the new model directly.
2. Remove runtime support for legacy lifecycle fields/statuses.
3. Do not add on-read normalization, fallback parsing, or compatibility branches for old lifecycle values.
4. If an old canonical note still exists locally after the change, it is acceptable for it to be considered unsupported until manually recreated or manually edited to the new schema.

Legacy lifecycle concepts to remove from the active app model:

- `archived`
- `completed`
- `completed_at`
- `blocked` as a lifecycle state
- `stale` as a surfacing concept
- `planning`
- `in_progress`
- `on_hold`
- `seed`
- `incubating`
- `active`
- `parked`

## Functional Changes By Area

### 1. Schemas

Update:

- [`config/schemas/canonical_object_v1.json`](../config/schemas/canonical_object_v1.json)
- [`config/schemas/derived_event_people_v1.json`](../config/schemas/derived_event_people_v1.json)
- [`config/schemas/derived_event_admin_v1.json`](../config/schemas/derived_event_admin_v1.json)
- [`config/schemas/derived_event_projects_v1.json`](../config/schemas/derived_event_projects_v1.json)
- [`config/schemas/derived_event_ideas_v1.json`](../config/schemas/derived_event_ideas_v1.json)

Required changes:

1. remove `archived` from canonical base required properties
2. add `status` to `people`
3. collapse all note status enums to `open|done`
4. rename `completed_at` to `done_at`
5. remove now-invalid enum mentions from derived schemas

### 2. Capture / Interpretation Prompts

Update:

- [`config/prompts/message_triage_v1.txt`](../config/prompts/message_triage_v1.txt)
- [`config/prompts/extract_v1.txt`](../config/prompts/extract_v1.txt)

Required changes:

1. make `done` apply to any note type, not just admin
2. add `reopen` intent/action to NL routing
3. update field catalogs to match new schemas
4. update extraction guidance so every created note gets `status=open`
5. remove references to `completed_at` and legacy status enums

### 3. Canonical Apply / Creation Defaults

Update:

- [`src/squire_core/operation_apply.py`](../src/squire_core/operation_apply.py)

Required changes:

1. stop defaulting `archived=False`
2. ensure all created object types default `status=open`
3. add people default status handling
4. remove project default `planning`
5. preserve `done_at` only when explicitly set

### 4. Validation / Fix Guidance

Update:

- [`src/squire_core/transport/validation.py`](../src/squire_core/transport/validation.py)
- [`src/squire_core/transport/discord/runtime_adapter_command.py`](../src/squire_core/transport/discord/runtime_adapter_command.py)

Required changes:

1. allowed `status` values become `open|done` for every type
2. `people` gains editable `status`
3. `completed_at` references become `done_at`
4. remove examples that teach legacy status values (`blocked`, `active`, `completed`, etc.)
5. update field display order for people/admin if the done timestamp field changes

### 5. Explicit Commands

Update:

- [`src/squire_core/transport/commands.py`](../src/squire_core/transport/commands.py)
- [`src/squire_core/transport/discord/command_contract.py`](../src/squire_core/transport/discord/command_contract.py)

Required changes:

1. broaden `!done <id|number>` to all note types
2. add `!reopen <id|number>`
3. `!done` should write `status=done` and `done_at=<now>`
4. `!reopen` should write `status=open` and clear `done_at`
5. help copy, usage text, numbered list tips, and examples must reflect the new commands and wording
6. any text saying “mark admin done” or “completed” must be replaced

### 6. Shared Mutation Logic

Update:

- [`src/squire_core/transport/mutations.py`](../src/squire_core/transport/mutations.py)

Required changes:

1. remove the guard that rejects `status=done` on non-admin types
2. make success/failure paths work uniformly for all types
3. ensure reminder refresh still fires when `!done`/`!reopen` changes admin due-item eligibility

### 7. NL Routing / Normalization

Update:

- [`src/squire_core/transport/routing.py`](../src/squire_core/transport/routing.py)
- [`config/schemas/message_triage_v1.json`](../config/schemas/message_triage_v1.json)

Required changes:

1. broaden `mark_done` normalization to any note type
2. add `reopen` as a mutation intent/action
3. normalize reopen to `status=open` and clearing `done_at`
4. remove admin-only “wrong type” behavior for done
5. keep explicit-only restrictions unchanged for `clear-archive`, `confirm`, `cancel`

### 8. Surfacing

Update:

- [`src/squire_core/surfacing.py`](../src/squire_core/surfacing.py)

Required changes:

1. remove `_is_archived(...)` checks from active filtering logic
2. active list should include only `status=open`
3. weekly review should rename `Completed this week` -> `Done this week`
4. done section should use `done_at` for all types
5. list/detail rendering should humanize only `open`/`done`
6. remove all staleness logic and terminology from project surfacing

Agreed direction:

- staleness is removed completely
- project surfacing must not depend on `updated_at`-based stale heuristics
- lifecycle-based surfacing must be driven by `status=open|done`
- daily digest project section should keep its current placement but show all open projects
- weekly review project section should keep its current placement but show open projects with a truthy `blocked_reason`
- people follow-up logic in weekly review should otherwise stay the same, except it should apply only to open people

### 9. Due-Time Reminders

Update:

- [`src/squire_core/transport/discord/scheduler.py`](../src/squire_core/transport/discord/scheduler.py)
- [`src/squire_core/surfacing.py`](../src/squire_core/surfacing.py)

Required changes:

1. due reminder eligibility becomes `status=open` only
2. remove `archived` checks
3. reopened timed admin items should become eligible again if their due time is still in the future
4. reopened past-due timed admin items should not resurrect missed pre-due reminders automatically; they should simply surface as overdue

### 10. Matching / Indexing / Search

Update:

- [`src/squire_core/indexer.py`](../src/squire_core/indexer.py)
- [`src/squire_core/matching.py`](../src/squire_core/matching.py)

Required changes:

1. remove `archived` from derived index schema and row building
2. remove active-record exclusion based on `archived`
3. keep done notes searchable
4. keep or simplify small down-rank for done notes
5. remove `completed` special casing

### 11. Seed Data / Fixtures

Update:

- [`src/squire_core/test_seed.py`](../src/squire_core/test_seed.py)

Required changes:

1. every seeded note gets `status`
2. replace legacy status vocabularies with `open|done`
3. rename `completed_at` to `done_at`
4. remove `archived`
5. refresh seeded expectations for daily/weekly/reminder scenarios

## UX Text Replacement Rules

All user-visible strings should follow these rules:

1. replace `complete` / `completed` with `done` where the meaning is lifecycle completion
2. replace `mark complete` with `mark done`
3. weekly review section becomes `Done this week`
4. help/examples should say `!done` and `!reopen`
5. avoid teaching legacy status values in examples

This applies to:

- help copy
- command detail text
- surfacing headers
- fix-guidance examples
- any button labels introduced or already present in mutation flows
- docs
- test assertions on user-facing text

## Suggested Implementation Sequence

### Phase 1: Spec-Driven Schema Foundation

1. finalize open product decisions from this spec
2. update canonical/derived schemas
3. update apply defaults

Gate:

- schema validation passes for converted fixtures

### Phase 2: Command + Validation + NL Lifecycle Paths

1. broaden `!done`
2. add `!reopen`
3. update validation/fix guidance
4. update NL routing and prompts

Gate:

- explicit command tests pass
- NL routing tests pass

### Phase 3: Surfacing + Reminder + Matching Normalization

1. update active filtering and weekly review
2. update due reminder eligibility
3. update index/matching logic
4. refresh seed data
5. remove stale-project config and any stale wording from docs/tests

Gate:

- surfacing/reminder/index tests pass

### Phase 4: Docs + Copy Cleanup

1. update README/docs/help copy/spec docs
2. prune stale references to archived/completed legacy lifecycle terms

Gate:

- grep check shows no stale lifecycle terms in active docs/help paths except historical specs kept intentionally for reference

## Minimum Test Plan

At minimum, update/add coverage for:

### Commands

- `!done` works for admin/projects/ideas/people
- `!reopen` works for admin/projects/ideas/people
- `!done <number>` and `!reopen <number>` work after `!recent`, `!active`, `!find`, `!status`, `!weekly`
- help copy reflects new lifecycle contract

### Validation / Fix

- `!fix status=done/open` accepted for all types
- legacy status values rejected
- people fix guidance now includes `status`

### NL Routing

- “mark 2 done” works for any target type
- “reopen 2” works through NL routing
- mixed multi-target plans still normalize correctly

### Surfacing

- `!active` excludes only `status=done`
- weekly review shows `Done this week`
- reopened past-due admin item appears overdue
- no stale-project behavior remains anywhere

### Reminders

- open timed admin eligible
- done timed admin ineligible
- reopen future timed admin becomes eligible again

## Open Decisions Requiring User Confirmation

No unresolved product questions remain in this draft.

## Handoff Notes For The Implementing Agent

1. Read this spec first.
2. Then read:
   - [`docs/commands.md`](./commands.md)
   - [`docs/data-model.md`](./data-model.md)
   - [`docs/surfacing.md`](./surfacing.md)
   - [`docs/configuration.md`](./configuration.md)
3. Audit the exact files listed in “Functional Changes By Area” before editing.
4. Prefer migrating tests/fixtures early so failing expectations reveal all legacy assumptions.
5. Run the relevant focused tests first, then the broader suite.
6. Historical specs may still mention old behavior for audit history, but active docs/help/runtime code must not.
7. Surfacing decisions already locked in for this implementation:
   - `!status` keeps its current section structure, but the project section shows all open projects.
   - `!weekly` keeps its current section structure, but the project section shows only open projects with truthy `blocked_reason`.
   - People contact logic stays conceptually the same as today, but must respect `status=open`.
