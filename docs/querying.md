# Querying

## Current Behavior

Squire currently supports lexical querying via Discord commands:

- `!find <query>`: runs SQLite FTS search over canonical object title/body and returns numbered results with IDs.
- `!show <number>`: expands one row from the latest `!recent`/`!find` cursor.

This path is deterministic and local-first:

1) User runs `!find <query>`.
2) Query terms are normalized and executed against local SQLite FTS.
3) Matching canonical objects are formatted into numbered rows.
4) `!show <number>` uses the active cursor to display details for one result.

There is no separate LLM query-to-JSON translation layer in current runtime querying behavior.

## Related Runtime Flows

Squire does use LLMs for capture interpretation and update/append decision routing. That retrieve-then-decide
pipeline is documented in:

- `docs/matching-spec.md`
- `docs/commands.md` (update/append strategy section)
