# Data Model

## Canonical Definition

Canonical objects are the current truthy records used for querying, surfacing, and maintenance. They are human-readable markdown with YAML frontmatter and an append-only body. Canonical objects are the only mutable artifacts; raw and derived artifacts are immutable and versioned.

## Raw Event (immutable)

Raw events are stored as markdown. Frontmatter includes id, source, source_message_id, and timestamp. The body contains the raw user text.

## Derived Event (immutable, versioned)

Derived events are stored as JSON. Fields include raw_event_id, intent/type, extracted fields, confidence (0–1), proposed operations, model name, prompt version, schema version, and timestamp. Derived JSON must match schema exactly with no extra keys. Classification and extraction use separate bucket-specific schemas to keep strict validation while avoiding ambiguous unions.

## Canonical Objects (mutable)

Canonical objects are stored as markdown with YAML frontmatter. Supported object types are people, projects, ideas, and admin. Common required fields are id, type, title, created_at, updated_at, and archived (bool, default false). Common optional fields are tags, links (array of {to, rel}), and source_event_ids.

### People

People are relationship records that evolve over time. Required fields include the common fields plus name (also used as title). Optional fields include context, follow_ups, last_contacted (YYYY-MM-DD), and next_contact (YYYY-MM-DD).

### Projects

Projects track ongoing work with state and next actions. Required fields include the common fields plus status (planning|in_progress|blocked|completed|on_hold) and next_action. Optional fields include goal, due (date or ISO8601), blocked_reason, and stakeholders (list of strings).

### Ideas

Ideas are captured insights or proposals. Required fields include the common fields plus one_liner. Optional fields include status (seed|incubating|active|parked|done, default seed) and next_step.

### Admin

Admin items are tasks and commitments that need completion (including calendarable items). Required fields include the common fields plus status (open|done|blocked, default open) and next_action. Optional fields include due_date (YYYY-MM-DD) or due_at (ISO datetime), priority (low|normal|high), blocked_reason, completed_at (set when done), and gcal_event_id when a calendar event is created.

## SQLite Index (derived)

The SQLite index is derived and rebuildable. Core tables include objects, FTS over title plus body, links, raw_events, and derivations.
