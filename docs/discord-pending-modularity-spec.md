# Discord Pending Interaction Modularity Spec

## Purpose

Define an implementation-ready follow-on refactor that finishes the Discord-side pending interaction cleanup left after the larger transport modularization work.

This spec is intentionally written as an end-state design, not a transitional migration note. The target code should read as if pending interaction business logic was always meant to live in shared transport modules, with Discord views acting only as UI shells.

## Problem Statement

The transport split is largely in good shape, but one important boundary is still too porous:

- `src/squire_core/transport/discord/views.py` still owns a substantial amount of business logic for pending-action confirmation, create-new, and cancel flows.
- `src/squire_core/transport/discord/runtime_adapter_inbound.py` still bypasses the Discord IO wrapper for one response path.

The result is that Discord UI callbacks still directly orchestrate:

- pending action load/write transitions
- canonical apply calls
- index refresh
- affinity touch recording
- canonical-change hooks
- success/failure message construction

That is the wrong side of the transport boundary. A future transport should be able to reuse the same pending-action workflow logic without re-implementing Discord view internals.

## Goals

1. Move pending interaction business logic out of `src/squire_core/transport/discord/views.py`.
2. Keep Discord views responsible only for Discord UI mechanics.
3. Introduce one canonical shared module for pending interaction orchestration.
4. Preserve current behavior, pending action semantics, and trace outcomes.
5. Route all Discord message sending through `src/squire_core/transport/discord/io.py`.
6. Avoid compatibility wrappers or transitional abstractions that preserve the old shape.

## Non-Goals

1. Redesign pending action UX text or button layout.
2. Change pending action schema or canonical storage behavior.
3. Merge pending action orchestration into unrelated modules like `inbound.py` or `routing.py`.
4. Generalize transport interaction APIs beyond what is needed for the current pending flows.
5. Refactor `AutoApplyFeedbackView` unless required incidentally for consistency.

## Current Boundary Violations

### `src/squire_core/transport/discord/views.py`

The following responsibilities currently live in Discord views and should move to shared transport code:

- `_write_pending_with_status(...)`
- `_force_create_derived(...)`
- `_apply_pending(...)`
- `_create_new_pending(...)`
- `_cancel_pending(...)`
- `MutationPendingView.confirm_button(...)`
- `MutationPendingView.cancel_button(...)`

These branches directly call or depend on:

- `apply_operations(...)`
- `load_pending_action(...)`
- `write_pending_action(...)`
- `load_frontmatter(...)`
- index refresh hooks
- affinity hooks
- canonical-change hooks

This is the main remaining modularity violation.

### `src/squire_core/transport/discord/runtime_adapter_inbound.py`

`send_unrecognized_category(...)` currently uses `self._message.channel.send(...)` directly instead of the Discord IO wrapper.

That should be corrected as part of this follow-on so the adapter obeys the same IO boundary consistently.

## Target End State

### Shared Module

Add a new shared module:

- `src/squire_core/transport/pending_interactions.py`

This module becomes the canonical home for pending interaction orchestration across transports.

It should own:

1. loading pending actions
2. validating pending action state
3. confirming capture pending updates
4. confirming capture "create new" flows
5. confirming NL mutation pending flows
6. cancelling pending actions
7. writing pending status transitions
8. applying canonical operations
9. refreshing the index
10. invoking canonical-change hooks
11. recording affinity touches
12. generating the final user-facing result message for the adapter to send

### Discord Views

`src/squire_core/transport/discord/views.py` should remain responsible only for:

1. select/button rendering
2. selection state for alternate candidate choice
3. author guard checks
4. Discord interaction response/edit mechanics
5. clearing or disabling the view after a shared workflow completes
6. Discord-specific root span naming for interaction entrypoints

The view layer should not directly call `apply_operations`, `load_pending_action`, `write_pending_action`, `load_frontmatter`, or any index/affinity hook.

### Discord Inbound Adapter

`src/squire_core/transport/discord/runtime_adapter_inbound.py` should use `src/squire_core/transport/discord/io.py` for `send_unrecognized_category(...)` so all Discord send mechanics pass through the same IO seam.

## Required Module Contract

### New Protocol

Add a transport-neutral runtime protocol in `src/squire_core/transport/pending_interactions.py` for the shared orchestration layer.

Required methods:

1. `load_pending_action(...)`
2. `write_pending_action(...)`
3. `apply_operations(...)`
4. `refresh_index_async(...)`
5. `notify_due_time_reminder_schedule_changed(...)`
6. `extract_target_ids_from_derived(...)`
7. `extract_ids_from_written_paths(...)`
8. `record_affinity_touches(...)`
9. `load_frontmatter(...)`

The protocol may also accept already-existing hooks/callbacks for:

- `matching`
- `affinity_key`
- `on_canonical_change`
- `now_iso`
- `log_confirm_applied`

Do not pass Discord SDK types into this protocol.

### Result Type

Add one transport-neutral result object for shared workflows, for example:

```python
@dataclass(frozen=True)
class PendingInteractionResult:
    outcome: str
    response_text: str
    clear_pending_instructions: bool = False
```

The exact field names may differ, but the shared layer must return enough information for the Discord adapter to:

- send the response text
- decide whether to clear pending instructions from the message body
- preserve current success/failure/cancel behavior without reimplementing business rules in the view

### Canonical Shared Functions

The shared module should expose explicit top-level operations rather than one vague dispatcher.

Required functions:

1. `confirm_capture_pending_update(...)`
2. `confirm_capture_pending_create_new(...)`
3. `confirm_nl_pending(...)`
4. `cancel_pending_action(...)`

This keeps call sites readable and preserves the behavioral distinction between:

- capture pending confirmation against an existing candidate
- capture pending "create new"
- NL mutation pending confirmation
- generic cancellation

## Detailed Behavior Requirements

### Confirm Capture Pending Update

Input requirements:

- `pending_id`
- `pending_root`
- `objects_root`
- `index_db`
- `derived_schema_path`
- `selected_target_id`
- `default_target_id`
- `candidates`
- `matching`
- `affinity_key`
- optional `on_canonical_change`
- optional `now_iso`

Behavior:

1. load the pending action
2. reject if missing or not `pending`
3. if a non-default candidate was selected, rewrite the single proposed operation target in the derived payload
4. apply the operations
5. refresh the index
6. invoke canonical-change hook if configured
7. record affinity touches using target IDs from the derived payload
8. write the pending action as `confirmed`
9. return the current success message shape
10. preserve the existing fallback-title behavior when no title is available from written paths

### Confirm Capture Pending Create-New

Behavior:

1. load the pending action
2. reject if missing or not `pending`
3. force the derived payload to a `create` decision
4. apply the operations
5. refresh the index
6. invoke canonical-change hook if configured
7. record affinity touches using IDs derived from written paths
8. write the pending action as `confirmed`
9. return the current "Created a new note" response shape

The force-create helper must move out of `views.py` with the rest of the business logic.

### Confirm NL Pending

Behavior:

1. load the pending action
2. reject if missing or not `pending`
3. apply the pending derived operations
4. refresh the index
5. invoke canonical-change hook if configured
6. record affinity touches using both:
   - target IDs from the pending derived payload
   - IDs from written paths
7. write the pending action as `confirmed`
8. invoke the confirm-applied log hook when present
9. return the same apply-success message currently sent by the view

### Cancel Pending

Behavior:

1. load the pending action
2. reject if missing or not `pending`
3. write the pending action as `cancelled`
4. return the existing "Cancelled. No changes made." message

Cancellation must remain a shared workflow, not duplicated between capture and NL views.

## File-Level End State

### `src/squire_core/transport/pending_interactions.py`

This file should contain:

- the runtime protocol
- the result type
- pending status update helper
- create-force helper
- the four canonical pending workflow functions
- shared success message formatting helpers

If title extraction helpers are needed, prefer reusing canonical helpers from `src/squire_core/transport/mutations.py` rather than keeping near-duplicates in the view layer.

### `src/squire_core/transport/discord/views.py`

After refactor, this file should contain only:

- `_truncate_text(...)`
- `_candidate_title(...)`
- `_candidate_display_title(...)`
- `_strip_pending_controls_from_message(...)`
- `_disable_view(...)`
- `_interaction_attributes(...)`
- `_CandidateSelect`
- `PendingActionView`
- `MutationPendingView`
- `AutoApplyFeedbackView`

`PendingActionView` and `MutationPendingView` should call into shared pending workflow functions and then handle:

- `interaction.response.send_message(...)`
- `interaction.response.edit_message(...)`
- `_disable_view(...)`

They should not directly manipulate pending action persistence or canonical mutation behavior.

### `src/squire_core/transport/discord/runtime_adapter_inbound.py`

Change `send_unrecognized_category(...)` to go through the Discord IO wrapper.

This can be implemented either by:

1. adding a small helper to `src/squire_core/transport/discord/io.py`, or
2. routing the current message through an existing wrapper if one already fits the behavior cleanly

Do not keep the direct `channel.send(...)` call.

## Naming and Structure Rules

1. Do not introduce a compatibility wrapper such as `handle_pending_action_legacy(...)`.
2. Do not leave shared orchestration helpers inside `views.py` "temporarily".
3. Do not create a Discord-specific business-logic module parallel to the shared one.
4. Do not add a second overlapping pending workflow abstraction in `inbound.py` or `routing.py`.
5. The new shared module should be the obvious canonical implementation point to a fresh reader.

## Recommended Implementation Order

### Phase 0: Add Shared Pending Module

1. create `src/squire_core/transport/pending_interactions.py`
2. define the protocol and result type
3. move pending status update helper there
4. move force-create helper there
5. move success message formatting helpers there or canonicalize them via `transport/mutations.py`

### Phase 1: Extract Capture Pending Workflows

1. extract confirm-update orchestration from `PendingActionView._apply_pending(...)`
2. extract create-new orchestration from `PendingActionView._create_new_pending(...)`
3. extract cancel orchestration from `PendingActionView._cancel_pending(...)`
4. rewrite the view to call shared functions and only send/edit/disable UI

### Phase 2: Extract NL Pending Workflows

1. extract confirm orchestration from `MutationPendingView.confirm_button(...)`
2. extract cancel orchestration from `MutationPendingView.cancel_button(...)`
3. keep the Discord interaction spans in the view, but move the actual pending workflow into shared transport code

### Phase 3: Clean Up Duplicated Helpers

1. remove any now-obsolete pending helpers from `views.py`
2. remove title/message helper duplication if canonical equivalents already exist
3. verify that shared helper ownership is obvious and singular

### Phase 4: Fix Inbound IO Boundary

1. replace the direct `channel.send(...)` path in `runtime_adapter_inbound.py`
2. route it through `discord/io.py`
3. confirm behavior and wording stay unchanged

## Validation Requirements

Run focused tests covering:

1. pending interaction button flows
2. Discord command tests that instantiate `PendingActionView`
3. inbound adapter tests that exercise unrecognized-category handling
4. tracing tests that cover pending interaction spans/outcomes

Recommended test targets:

- `tests/test_discord_commands.py`
- `tests/test_transport_inbound.py`
- `tests/test_otel_tracing.py`

Then run the full suite.

Validation rule: do not change user-visible behavior as part of this refactor except where required to route through canonical helper seams.

## Risks and Guardrails

### Main Risk

The main risk is accidental behavior drift in pending confirmation flows while moving code out of Discord views.

### Guardrails

1. Preserve current pending statuses (`pending`, `confirmed`, `cancelled`, `failed`).
2. Preserve current success/failure/cancel response text unless there is a deliberate reason to change it.
3. Preserve trace outcome attributes currently emitted from interaction flows.
4. Preserve index refresh, affinity touch recording, and reminder schedule change hooks.
5. Do not remove `AutoApplyFeedbackView` or fold it into the new module; it is already a thin adapter concern.

## Completion Criteria

This follow-on is complete when:

1. `views.py` no longer imports or calls pending persistence or canonical apply helpers directly
2. the shared module is the canonical home for pending interaction orchestration
3. Discord views are thin interaction shells
4. `runtime_adapter_inbound.py` no longer uses direct `channel.send(...)`
5. tests pass
6. a fresh reader would not infer that pending interaction business logic originally lived inside Discord views
