Data Model

Canonical Definition

Canonical objects are the current truthy records used for querying, surfacing, and maintenance. They are human-readable markdown with YAML frontmatter and an append-only body. Canonical objects are the only mutable artifacts; raw and derived artifacts are immutable and versioned.

Raw Event (immutable)

Raw events are stored as markdown. Frontmatter includes id, source (discord), discord_message_id, and timestamp. The body contains the raw user text and attachment references.

Derived Event (immutable, versioned)

Derived events are stored as JSON. Fields include raw_event_id, intent/type, extracted fields, confidence (0–1), proposed operations, model name, prompt version, schema version, and timestamp. Derived JSON must match schema exactly with no extra keys.

Canonical Objects (mutable)

Canonical objects are stored as markdown with YAML frontmatter. Supported object types are note, idea, task, event, and person. Common required fields are id, type, title, created_at, updated_at, and archived (bool, default false). Common optional fields are tags, links (array of {to, rel}), and source_event_ids.

Note

A note is general capture that does not clearly imply a deliverable outcome or scheduled time. Required fields are the common fields only. Optional fields include summary, importance (low|normal|high), and pin.

Idea

An idea is explicitly a proposal or concept you might want to develop later and typically has a one-line insight. Required fields include the common fields and one_liner (empty allowed in v1 but preferred). Optional fields include status (seed|incubating|active|parked|done, default seed) and next_step.

Task

A task is an actionable commitment with a next action and an optional due date. Required fields include the common fields plus status (open|done|blocked, default open) and next_action (can be empty only if confidence is low and the item is flagged for review). Optional fields include due (date or ISO8601), priority (low|normal|high), blocked_reason, and completed_at (set when done).

Event

An event is a scheduled item with a start time and optional end. Required fields include the common fields plus start (ISO8601 datetime). Optional fields include end, location, reminders (list of minutes_before), gcal_event_id, and status (scheduled|done|canceled, default scheduled).

Person

A person is a contact or relationship record with follow-ups. Required fields include the common fields plus name (also used as title). Optional fields include context, follow_ups, last_contacted (YYYY-MM-DD), and next_contact (YYYY-MM-DD).

SQLite Index (derived)

The SQLite index is derived and rebuildable. Core tables include objects, FTS over title plus body, links, raw_events, and derivations.
