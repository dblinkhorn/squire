# Discord Interface

## Capture

By default, any DM to the bot is treated as an admin item. A prefix like `admin:`, `project:`, `idea:`, or `person:` tells Squire how to classify the message without inference.

Prefixes:
- admin:
- project:
- idea:
- person:

## Minimal Command Set (v1)

`!status` shows top due or overdue admin items and the three most recent captures needing attention. `!recent [N]` shows the last N canonical objects created or updated, with IDs. `!find` searches title and body via SQLite FTS and returns the top matches with IDs. `!show` prints a compact view with title, key fields, and the first several lines of the body. `!done` sets an admin item status to done and sets completed_at. `!append` appends text to the body and updates updated_at. `!fix field=value [field=value…]` modifies frontmatter fields from an allowlist.

The following commands are implemented for explicit updates (no LLM inference):
- `!append <id> <text>`
- `!done <id>`
- `!fix <id> field=value [field=value…]`

Tags are optional user-defined categories. Users can include inline hashtags (for example, `#work`) or update tags later with `!fix`. The system can support queries like `!find tag:work` and a summary command (for example, `!tags`) to list tags and counts.

Squire can also interpret natural-language queries via the LLM, translate them into structured query payloads, and then execute them locally against the SQLite index. Invalid or ambiguous queries trigger clarification instead of execution.

## Nice-to-Have (v1.1+)

Planned follow-ons include `!due [days]`, `!archive`, `!help`, `!rebuild-index`, and `!confirm <pending_action_id>` for proposed calendar actions.

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

Explicit commands remain available for precision:
- `!append <id> …` (force append)
- `!done <id>` (mark admin done)
- `!fix <id> field=value` (field updates)
