# Discord Interface

## Capture

By default, any DM to the bot is treated as an admin item. A prefix like `admin:`, `project:`, `idea:`, or `person:` tells Squire how to classify the message without inference.

Prefixes:
- admin:
- project:
- idea:
- person:

## Minimal Command Set (v1)

`!status` returns the daily digest (admin overdue/today/soon sections, project attention, and people follow-ups).
`!weekly` returns the weekly review sections on demand.
`!recent [N]` shows the last N notes as a numbered list. `!find <query>` searches title and body via SQLite FTS and
returns numbered matches. `!show <number>` prints a compact view for an item from the latest `!recent` or `!find`
result set. Manual pull commands (`!recent`, `!find`, `!show`) include IDs to support intentional follow-up edits.
Scheduled/on-demand digest commands (`!status`, `!weekly`) remain list-first and avoid IDs by default.
Scheduled/on-demand digest commands are currently read-only; explicit `done`/`edit` action buttons are deferred and
text commands remain the mutation path.
`!done` sets an admin item status to done and sets completed_at. `!append` appends text to the body and updates updated_at. `!fix field=value
[field=value…]` modifies frontmatter fields from a strict per-type allowlist with value validation (for example enum-only status values and ISO date/time checks). For values with spaces, quote them (for example `next_action="Call dentist tomorrow at 4pm"`). `!confirm <pending_id>` applies a pending action,
and `!cancel <pending_id>` dismisses it. `!clear-archive` starts a destructive archive reset flow and requires a separate `DELETE` confirmation message within a short TTL before data is removed.

The following commands are currently implemented:
- `!status`
- `!weekly`
- `!recent [N]`
- `!find <query>`
- `!show <number>`
- `!append <id> <text>`
- `!done <id>`
- `!fix <id> field=value [field=value…]`
- `!confirm <pending_id>`
- `!cancel <pending_id>`
- `!clear-archive` (requires follow-up `DELETE` confirmation)

Tags are optional user-defined categories. Users can include inline hashtags (for example, `#work`) or update tags later with `!fix`. The system can support queries like `!find tag:work` and a summary command (for example, `!tags`) to list tags and counts.

Squire can also interpret natural-language queries via the LLM, translate them into structured query payloads, and then execute them locally against the SQLite index. Invalid or ambiguous queries trigger clarification instead of execution.

## Nice-to-Have (v1.1+)

Planned follow-ons include `!due [days]`, `!archive`, `!help`, and `!rebuild-index` for proposed calendar actions.
Future maintenance UX ideas include Discord component shortcuts on surfaced lists (for example a `Mark Items Done` button
that opens a multi-select menu for bulk completion with confirm/undo safeguards).

Commands never generate new canonical objects unless explicitly stated.

## Update & Append Strategy (Planned)

Squire aims to keep capture friction low while avoiding accidental mutations. The default behavior is create-on-capture with selective updates/append when confidence is high or the user confirms.

Core principles:
- Default to create unless the target is a uniquely identifiable existing record.
- If ambiguity exists, ask for confirmation before mutating canonical state.
- Prefer lightweight interactions (buttons/selects) when possible, but always provide a text fallback.

Retrieval + decision flow:
1) Run a local index search against the user’s message to find candidate objects.
2) Provide a small shortlist (IDs, titles, short snippets) to the LLM.
3) The LLM proposes create/append/update with a confidence score.
4) Apply automatically only when confidence is high and the match is unique; otherwise ask the user.

Discord interaction patterns:
- If a single strong match is found, reply with a confirmation button (e.g., “Append to Alex Chen?”).
- If multiple candidates exist, present a dropdown to choose the target.
- For multi-entity notes (e.g., “Met A and B”), propose a multi-update and confirm in one step.
- If the user is slow to respond, the system should store a pending action and allow `!confirm <id>` or `!cancel <id>` later.
- After auto-apply, include a quick “Was this incorrect?” action with a text fallback to `!fix`/`!append`.

Explicit commands remain available for precision:
- `!append <id> …` (force append)
- `!done <id>` (mark admin done)
- `!fix <id> field=value` (field updates)
