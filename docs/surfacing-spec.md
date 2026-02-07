# Surfacing Baseline Spec (v1)

## Objective

Define a practical first-pass surfacing experience that helps users see important notes quickly, without extra interpretation layers.

This baseline explicitly avoids:

- suggested next actions
- object IDs in user-facing surfacing output

## What "Responses" Means

In this spec, "responses" means the bot messages shown to the user in Discord for both scheduled digests and interactive commands.

## Design Principles

- Keep outputs short, scannable, and deterministic.
- Surface notes, not recommendations.
- Do not mutate canonical state from surfacing commands.
- Prefer simple ranking rules over LLM summarization.

## Push Surfacing

### Daily Digest

Schedule:
- run once daily at configured local time (`schedule.daily_digest_time`)

Sections:
- Admin overdue
- Admin due today
- Admin due soon (within configurable window)
- Blocked or stale projects (max 1-3)
- People to follow up (where `next_contact` is due)

Rules:
- no suggested next action section
- no object IDs in output
- each line shows concise title + due/status context
- if section is empty, show `None`

### Weekly Review

Schedule:
- once weekly (configurable day/time)

Sections:
- Recently changed notes (last 7 days)
- Open admin items without due dates (oldest first)
- Blocked/stale projects
- People overdue for contact
- Ideas updated recently (optional)

Rules:
- no recommendations; list-only output
- no object IDs in output

## Pull Surfacing

### Commands

- `!status`: returns the same sectioned digest view used for daily push.
- `!recent [N]`: returns recent notes as numbered rows.
- `!find <query>`: returns ranked matches as numbered rows.
- `!show <number>`: expands the selected row from the last `!recent` or `!find` result set.

### Result Cursor Model

To avoid exposing object IDs while still allowing drill-down:

- store the last surfaced result list per user/thread
- map `1..N` to canonical object IDs internally
- expire cursor entries after a short TTL (for example, 30-60 minutes)
- if cursor is missing/expired, prompt user to run `!find` or `!recent` again

## Output Format

For list views (`!status`, `!recent`, `!find`):
- include section title
- include up to configured limit rows
- each row is plain text with compact metadata

For detail view (`!show <number>`):
- title
- key fields by type (status, due fields, etc.)
- first body lines (truncated)

## Configuration Additions

Add/extend in `config.yaml`:

```yaml
surfacing:
  output:
    include_ids: false
  admin:
    due_soon_days: 1
    include_open_limit: 5
  projects:
    stale_days: 14
    blocked_limit: 3
  people:
    next_contact_days: 0
  pull:
    default_recent_limit: 10
    default_find_limit: 5
    cursor_ttl_minutes: 45
schedule:
  weekly_review_day: "SUN"
  weekly_review_time: "10:00"
```

Notes:
- `include_ids` defaults to `false` for this baseline.
- IDs may still be used internally for command execution and logs.

## Implementation Plan

1. Extend pull commands:
- implement `!recent`, `!find`, `!show <number>`
- add per-user/thread cursor storage for numbered selections

2. Refine digest composition:
- remove suggested next actions from digest
- add weekly review scheduler and formatter

3. Add config plumbing:
- load new surfacing output/pull/weekly settings with safe defaults

4. Add tests:
- digest section inclusion/exclusion
- numbered cursor behavior (`!find` -> `!show 2`)
- no IDs in surfaced output when `include_ids` is `false`

## Acceptance Criteria

- Daily digest contains only note surfacing sections (no suggestion section).
- `!recent`, `!find`, and `!show` are functional without exposing IDs.
- Cursor-based `!show <number>` works for active cursor windows.
- Surfacing output stays concise and deterministic across repeated runs.

## Future Extensions

- optional toggle to include IDs for power users
- richer filtering (`!due`, tags, types)
- optional LLM summary mode behind explicit config flag
