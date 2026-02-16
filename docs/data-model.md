# Data Model

## Canonical Definition

Canonical objects are the current source-of-truth records used for search, surfacing, and maintenance. They are
human-readable markdown with YAML frontmatter and a durable note body. Canonical objects are the only mutable artifacts;
raw and derived artifacts are immutable and versioned.

## Raw Event (immutable)

Raw events are stored as markdown. Frontmatter includes id, source, source_message_id, and timestamp. The body contains the raw user text.

## Derived Event (immutable, versioned)

Derived events are stored as JSON. Fields include raw_event_id, intent/type, extracted fields, confidence (0–1), proposed operations, model name, prompt version, schema version, and timestamp. Derived JSON must match schema exactly with no extra keys. Classification and extraction use separate bucket-specific schemas to keep strict validation while avoiding ambiguous unions.

## Decision Artifact (immutable, versioned)

Decision artifacts capture the update/append decision against candidate matches. They are stored as JSON alongside derived events and include schema_version, raw_event_id, object_type, confidence, candidates (id/title/snippet/score), proposed operations (op + target_id), model, prompt_version, and timestamp. These artifacts are for auditability and do not mutate canonical objects on their own.

## Pending Action (mutable)

Pending actions capture updates that require user confirmation. They are stored as JSON under `events/pending/` and include pending_action_id, raw_event_id, object_type, status (pending|confirmed|cancelled|failed), created_at, last_updated, the derived payload to apply, and the decision payload that triggered the confirmation request. Status changes update last_updated but do not alter the raw or derived artifacts.

## Canonical Objects (mutable)

Canonical objects are stored as markdown with YAML frontmatter. Supported object types are people, projects, ideas, and admin. Common required fields are id, type, title, created_at, updated_at, and archived (bool, default false). Common optional fields are tags, links (array of {to, rel}), source_event_ids, and last_decision_id.
source_event_ids links a canonical object to the raw events that created or updated it. Each apply operation appends the current raw_event_id to this list for auditability.
last_decision_id stores the most recent decision artifact identifier (relative path under events/derived) that influenced an update or create.

Updates and appends merge with existing frontmatter; only the provided fields are changed, while required fields are preserved from the existing object.

### People

People are relationship records that evolve over time. Required fields include the common fields plus name (also used as title). Optional fields include context, follow_ups, last_contacted (YYYY-MM-DD), and next_contact (YYYY-MM-DD).

### Projects

Projects track ongoing work with state and next actions. Required fields include the common fields plus status (planning|in_progress|blocked|completed|on_hold) and next_action. Optional fields include goal, due (date or ISO8601), blocked_reason, and stakeholders (list of strings).

### Ideas

Ideas are captured insights or proposals. Required fields include the common fields plus one_liner. Optional fields include status (seed|incubating|active|parked|done, default seed) and next_step.

### Admin

Admin items are tasks and commitments that need completion (including calendarable items). Required fields include the common fields plus status (open|done|blocked, default open) and next_action. Optional fields include due_date (YYYY-MM-DD) or due_at (ISO datetime with timezone offset), priority (low|normal|high), blocked_reason, completed_at (set when done), and gcal_event_id when a calendar event is created.

`due_date` and `due_at` are mutually exclusive in canonical state. When an update sets `due_at`, existing `due_date` is removed. When an update sets `due_date`, existing `due_at` is removed.

## SQLite Index (derived)

The SQLite index is derived and rebuildable.

Current tables:

- `objects`: canonical object rows used for filtering/ranking metadata.
- `objects_fts`: FTS5 virtual table over title/body for lexical search.

When semantic matching is enabled, additional derived semantic tables are maintained in the same SQLite database:

- `semantic_objects`
- `semantic_meta`
