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

Tags are optional user-defined categories. Users can include inline hashtags (for example, `#work`) or update tags later with `!fix`. The system can support queries like `!find tag:work` and a summary command (for example, `!tags`) to list tags and counts.

Squire can also interpret natural-language queries via the LLM, translate them into structured query payloads, and then execute them locally against the SQLite index. Invalid or ambiguous queries trigger clarification instead of execution.

## Nice-to-Have (v1.1+)

Planned follow-ons include `!due [days]`, `!archive`, `!help`, `!rebuild-index`, and `!confirm <pending_action_id>` for proposed calendar actions.

Commands never generate new canonical objects unless explicitly stated.
