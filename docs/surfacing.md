# Surfacing Strategy

## Push (Scheduled)

The v1 daily digest includes overdue and due-today admin items (plus any due soon within the configured window),
projects needing attention (blocked/stale), and people with `next_contact` due.
Sections are list-only and do not include suggested next actions.

Weekly review is also supported on a separate weekly schedule. It includes recently changed notes, open unscheduled
admin items, blocked/stale projects, people overdue for contact, and optional recently updated ideas.

## Pull (Interactive)

Pull surfacing is driven by contextual queries via commands and is designed for quick review and drill-down.

## Design Rules

Outputs should be small and predictable. Rules come first and LLMs come second. The system never invents data and always keeps outputs actionable.

LLM-assisted surfacing uses structured inputs and fixed response formats to keep outputs consistent and auditable.
By default, surfaced list output omits object IDs and uses numbered result lists.

## Configuration Defaults

Surfacing rules are configurable in `config.yaml`. Default behavior is:

- Admin: overdue, due-today, and due-soon sections.
- Projects: surfaced when status is blocked or stale.
- Ideas: included in the weekly review when `surfacing.ideas.weekly_review` is true.
- People: surfaced when `next_contact` is due.
