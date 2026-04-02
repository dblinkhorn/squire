# Surfacing Baseline Spec (v1)

## Objective

Define a practical first-pass surfacing experience that helps users see important notes quickly, without extra interpretation layers.

This baseline explicitly avoids:

- suggested next actions
- object IDs in scheduled digest output

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
- Open projects
- People to follow up (where `next_contact` is due)

Rules:

- no suggested next action section
- no object IDs in output
- each line shows concise title + due/status context
- include digest-level summary counts (overdue/today/soon/projects/people)
- use lightweight, consistent section emoji prefixes for scanability
- use human-readable dates with near-term relative labels (`today`, `tomorrow`, `yesterday`, `in N days`, `N days ago`)
- if section is empty, show `All clear`

### Weekly Review

Schedule:

- once weekly (configurable day/time)

Sections:

- Done this week (notes with `status=done` and recent `done_at`)
- Admin items without due dates (oldest first; open items only)
- Blocked projects
- People overdue for contact
- Ideas updated recently (optional)

Rules:

- no recommendations; list-only output
- no object IDs in output
- include a weekly summary count line
- use the same section emoji and human-readable date style as daily digest
- omit `Done this week` when empty
- for other weekly sections, if empty show `All clear`

## Pull Surfacing

### Commands

- `!status`: returns the same sectioned digest view used for daily push.
- `!recent [number]`: returns recent notes as numbered rows with IDs.
- `!find <query>`: returns ranked matches as numbered rows with IDs.
- `!show <number>`: expands the selected row from the last `!recent` or `!find` result set and includes the ID.

### Result Cursor Model

To support quick drill-down by list position:

- store the last surfaced result list per user/thread
- map `1..N` to canonical object IDs internally
- expire cursor entries after a short TTL (for example, 30-60 minutes)
- if cursor is missing/expired, prompt user to run `!find` or `!recent` again

## Output Format

For list views (`!status`, `!recent`, `!find`):

- include section title
- include up to configured limit rows
- each row is plain text with compact metadata
- `!recent` and `!find` include canonical IDs
- `!status` follows digest no-ID behavior by default

Daily/weekly readability format:

- header includes a digest/review icon and human-readable date
- summary count line appears under the header
- section rows use bullet markers
- dates prioritize readability while retaining precision

For detail view (`!show <number>`):

- title
- key fields by type (status, due fields, etc.)
- first body lines (truncated)

## Configuration Additions

Add/extend in `config.yaml`:

```yaml
surfacing:
  output:
    show_ids_daily_weekly: false
  admin:
    due_soon_days: 1
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

- `show_ids_daily_weekly` defaults to `false` for this baseline.
- `!recent`, `!find`, and `!show` include IDs regardless of this setting.

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
- no IDs in digest output when `show_ids_daily_weekly` is `false`
- pull commands include IDs

## Acceptance Criteria

- Daily digest contains only note surfacing sections (no suggestion section).
- `!recent`, `!find`, and `!show` are functional and include IDs.
- Cursor-based `!show <number>` works for active cursor windows.
- Surfacing output stays concise and deterministic across repeated runs.

## Future Extensions

- richer filtering (`!due`, tags, types)
- optional LLM summary mode behind explicit config flag
- explicit digest actions for `done` and `edit` via Discord components (with text-command fallback)
