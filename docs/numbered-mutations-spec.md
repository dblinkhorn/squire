# Numbered Mutations Spec

## Problem

Squire's mutation commands currently require explicit canonical IDs (`!done <id>`, `!append <id>`, `!fix <id> ...`).
This is precise but high-friction after list-style review flows where users think in row numbers (for example after `!recent`,
`!find`, `!status`, or `!weekly`).

Goal example:

- User sees a surfaced row `2` and sends `!done 2` or natural language "mark 2 done".

## Goals

- Allow safe numbered mutations using recently surfaced list positions.
- Keep existing ID-based commands fully supported.
- Preserve determinism and avoid accidental wrong-target updates.
- Work across both pull lists and digest/review surfaces.

## Non-Goals

- Replacing ID-based commands.
- Free-form agentic mutation of multiple rows in one step.
- Changing canonical schema or pending-action schema.

## Scope

### In Scope (v1)

- `!done <number>` resolution from active numbered cursor.
- `!append <number> <text>` resolution from active numbered cursor.
- `!fix <number> <field=value> [field=value ...]` resolution from active numbered cursor.
- Numbered cursor creation from:
  - `!recent [number]`
  - `!find <query>`
  - `!status`
  - `!weekly`

### Out of Scope (v1)

- Natural-language mutation shortcuts (for example "mark 2 done") in this spec's implementation phase.
  This can be layered via NL command routing once numbered command primitives are stable.

## Current State

- `!recent` and `!find` already store a numbered result cursor for `!show <number>`.
- `!status` and `!weekly` do not currently populate a cursor for mutation targeting.
- Mutation commands require ID-like argument in position 1 and do not resolve row numbers.

## Proposed Design

## 1) Unified Action Cursor

Introduce a unified per-user/per-channel action cursor that maps row numbers to canonical object IDs and metadata:

- `object_id`
- `object_type`
- `source_view` (`recent`, `find`, `status`, `weekly`)
- `row_number`
- `created_at`
- `expires_at`

Cursor key remains scoped by `(author_id, channel_id)` to preserve current safety boundary.

### TTL

- Reuse existing cursor TTL setting (`surfacing.pull.cursor_ttl_minutes`) for v1 to avoid config sprawl.
- Later, split pull-vs-digest TTL if needed.

## 2) Numbering in Surfaced Output

To support deterministic row targeting:

- `!recent` and `!find` keep existing numbered rows.
- `!status` and `!weekly` add explicit numeric row labels for actionable items.

Numbering rules:

- Use a single global sequence (`1..N`) across sections in one message.
- Do not assign numbers to non-item placeholders (for example `All clear`).
- Include a short footer hint:
  - "Use `!done <number>`, `!append <number> ...`, or `!fix <number> ...` on this list."

Tips policy (required):

- `!recent` footer includes:
  - `!show <number>`
  - `!done <number>`
  - `!append <number> <text>`
  - `!fix <number> <field=value>`
  - `!recent [number]` supports up to `50`
- `!find` footer includes:
  - `!show <number>`
  - `!done <number>`
  - `!append <number> <text>`
  - `!fix <number> <field=value>`
- `!status` and `!weekly` include a concise mutation tip footer:
  - before numbered mutation support is active for those views, point users to `!recent`/`!find` for actionable numbering
  - after numbered support is active, use the same numbered mutation tip set as `!recent`/`!find`

## 3) Command Resolution Rules

For `!done`, `!append`, `!fix` first argument:

- If token is numeric:
  - resolve against active action cursor.
  - if missing/expired/out-of-range, return clear guidance to rerun a listing command.
- If token is non-numeric:
  - use existing ID-based behavior unchanged.

This preserves full backward compatibility.

## 4) Safety Gates

- Numeric resolution must be single-target and deterministic.
- Enforce type checks already present in command apply path:
  - `!done` only for admin object type.
- If row points to unsupported type for requested mutation, fail with actionable message.
- Never guess across stale cursors.

## 5) NL Layer Integration (Follow-on)

After numbered command primitives are stable, NL routing may map:

- "mark 2 done" -> `!done 2`
- "append to 3 ..." -> `!append 3 ...`

Only apply this when NL command routing classifies intent as command-like with high confidence.

## UX Copy

Errors:

- "No active numbered list. Run `!recent`, `!find`, `!status`, or `!weekly` first."
- "That number is out of range for your last list."
- "`!done` only applies to admin items."

Success:

- Keep current mutation success style, but include resolved title when available.

Tips (examples):

- `Tip: !show <number> · !done <number> · !append <number> <text> · !fix <number> <field=value>`
- `Tip: !recent [number] supports up to 50`

## Telemetry

Add structured logs:

- `numbered_mutation_resolved`:
  - `raw_event_id`
  - `command` (`done`, `append`, `fix`)
  - `source_view`
  - `row_number`
  - `object_id`
- `numbered_mutation_resolution_failed`:
  - `reason` (`no_cursor`, `expired`, `out_of_range`, `wrong_type`)

## Testing

Add/extend tests for:

- `!done 2` after `!recent` resolves correctly.
- `!append 1 ...` and `!fix 3 ...` resolve correctly.
- Fallback to ID path remains unchanged.
- Expired cursor returns guidance and does not mutate data.
- `!status` and `!weekly` output numbering maps to cursor correctly.
- Wrong-type mutations from numbered row are safely rejected.

## Rollout Plan

1. Phase A: numeric mutations for `!recent`/`!find` only (minimal risk).
2. Phase B: add numbering + cursor mapping for `!status`/`!weekly`.
3. Phase C: optional NL phrase support (`mark 2 done`) via NL routing layer.
4. Phase D (future): optional multi-action NL commands with explicit preview/confirm flow.

## Acceptance Criteria

- Users can run `!done 2` immediately after a numbered surfaced list and mutate the intended object.
- ID-based mutation commands continue to work without behavior changes.
- No mutation occurs when cursor context is missing/expired/ambiguous.
- Digest/review numbered rows can be safely targeted in the same channel within TTL.
- `!recent`, `!find`, `!status`, and `!weekly` render concise command tips aligned with the active capabilities of each view.

## Open Questions

All previously open questions are resolved for v1:

1. Numbering is literal row numbering in surfaced lists; users can reference those numbers in follow-up mutation commands while cursor context is active.
2. `!status`/`!weekly` numbering includes all surfaced item types.
3. Successful numbered mutations return confirmation only; no automatic list re-render.

## Future Multi-Action Commands (Out of Scope for v1)

Examples like:

- "mark 1 and 4 done"
- "change due date of 2 to YYYY-MM-DD"
- "update time of 3 to 4pm"

are feasible but should ship as a separate feature with stronger safeguards:

- parse into an explicit action list
- resolve each row number against the same cursor snapshot
- validate all actions before apply
- show a preview and require one confirm step
- define apply semantics (all-or-nothing vs best-effort with per-item errors)

## Future UI Maintenance Flows (Out of Scope for v1)

To reduce maintenance friction in Discord, add component-driven shortcuts on surfaced lists:

- Add a `Mark Items Done` button on `!recent`, `!find`, `!status`, and `!weekly` responses.
- On click, open a user-scoped multi-select menu of actionable admin items from the active cursor snapshot.
- Allow selecting one or many items, then require explicit confirm before apply.

Suggested UX refinements:

- Provide quick-action buttons for top surfaced rows (for example `Done Top 1`, `Done Top 2`) in digest/review views.
- Keep text-command fallback always available (`!done <id>` and numbered variants when enabled).
- Show concise result summary after apply:
  - count updated
  - count skipped (already done / invalid)
- Optional short-lived `Undo` action after bulk apply, gated by TTL.

Safety/consistency rules:

- All UI actions must resolve against a stable cursor snapshot tied to user + channel.
- Reject expired/stale actions with a prompt to rerun a surfacing command.
- Re-validate object type/state at apply time (no blind writes from stale UI state).
