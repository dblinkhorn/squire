# Surfacing Strategy

## Push (Scheduled)

The v1 daily digest includes overdue and due-today admin items (plus any due soon within the configured window),
admin items without due dates beneath the scheduled admin sections, all open projects,
and people with `next_contact` due.
Sections are list-only and do not include suggested next actions. Digests include a compact summary count line,
emoji-prefixed section headers, and human-readable date rendering with near-term relative labels.
Daily digest empty sections are shown as `All clear` for predictable scanning.

Due-time reminders can also surface open admin items with `due_at` at configured pre-due offsets.
The default offsets are `90` and `15` minutes when the key is omitted; set `schedule.due_time_reminder_offsets_minutes: []`
to disable. These reminders are separate from the daily digest and are intended for time-specific nudges throughout the day.

Weekly review is also supported on a separate weekly schedule. It includes done notes this week, admin items
without due dates, blocked projects, people overdue for contact, and optional recently updated ideas.
The `Done this week` section is omitted when empty; other weekly sections still use `All clear` when applicable.

Scheduled daily digests and weekly reviews use the same numbered presentation as `!status` and `!weekly`. Each
scheduled report replaces the current numbered cursor for its destination channel, so follow-up commands such as
`!done 1`, `!append 2 <text>`, and `!fix 3 ...` can be sent from the channel or any Squire-created thread beneath it.

## Pull (Interactive)

Pull surfacing is driven by contextual queries via commands and is designed for quick review and drill-down.

## Design Rules

Outputs should be small and predictable. The system never invents data and always keeps outputs actionable.

Current surfacing output is deterministic and local-data driven (no LLM formatting pass in the surfacing layer).

ID behavior:

- scheduled digests/reviews omit IDs by default (configurable via `surfacing.output.show_ids_daily_weekly`)
- manual pull lists (`!recent`, `!find`, `!show`) include IDs
- `!active` groups open notes by type and includes numbered rows for follow-up commands

## Configuration Defaults

Surfacing rules are configurable in `config.yaml`. Default behavior is:

- Admin: overdue, due-today, and due-soon sections.
- Admin: an additional without-due-dates section appears after the scheduled admin sections.
- Projects: daily digest shows all open projects; weekly review shows open projects with `blocked_reason`.
- Active list: `!active` shows open notes grouped by type.
- Ideas: included in the weekly review when `surfacing.ideas.weekly_review` is true.
- People: surfaced when `next_contact` is due.
