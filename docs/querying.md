# Querying

## Current Behavior

Squire currently supports lexical querying via Discord commands:

- `!find <query>`: runs SQLite FTS search over canonical object title/body and returns numbered results.
- `!show <number>`: expands one row from the latest numbered cursor (`!recent`, `!active`, `!find`, `!status`, or `!weekly`).
- `!detail <number>`: shows the full raw note object for one row from the latest numbered cursor.
- `!active [number] [category]`: lists active notes grouped by type with numbered rows.

The dedicated Discord channel and its Squire-created threads share one current numbered cursor. Every newly displayed
numbered list replaces it, including scheduled daily digests and weekly reviews. The cursor remains current until it is
replaced or the runtime restarts.

This path is deterministic and local-first:

1) User runs `!find <query>`.
2) Query terms are normalized and executed against local SQLite FTS.
3) Matching canonical objects are formatted into numbered rows.
4) `!show <number>` uses the active cursor to display details for one result.
5) `!detail <number>` uses the same active cursor to show the full raw note object for one result.

There is no separate LLM query-to-JSON translation layer in current runtime querying behavior.

## Related Runtime Flows

Squire does use LLMs for capture interpretation and update/append decision routing. That retrieve-then-decide
pipeline is documented in:

- `docs/commands.md` (update/append strategy section)
