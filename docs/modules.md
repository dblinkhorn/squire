# Module Responsibilities

## Ingest (Discord)

The ingest module receives Discord DM messages, distinguishes command versus capture paths, and writes a raw event
record before downstream processing. If raw event writes fail, the pipeline stops for that message.

## Interpreter (LLM)

The interpreter classifies intent, extracts structured fields, and emits strict-schema derived JSON artifacts.
When configured, it also runs decision and candidate-query prompts for update/append routing. Validation failures
write invalid artifacts and return clarification responses without mutating canonical state.
OpenAI transport is async-native in runtime message handling; async interpreter paths await provider calls directly
and fall back to off-loop execution only for providers without async methods.

## Store (Git Canonical Store)

The store applies create/update/append operations to canonical markdown objects with schema validation and audit linkage
(`source_event_ids`, `last_decision_id`). Operations run sequentially, and apply failures halt further processing for
that message while surfacing the error to the user.

## Index (SQLite)

The index parses canonical objects and builds derived SQLite search tables (`objects`, `objects_fts`).
When semantic matching is enabled, semantic rows/metadata are also maintained in the same database.
The index is rebuildable from canonical objects and is not authoritative state.

## Surfacer

The surfacer provides push (scheduled daily/weekly digest/review) and pull (interactive commands), with configurable
ID visibility for scheduled outputs and ID-inclusive manual pull lists. It is read-only and does not mutate canonical state.

## Optional Providers

Current runtime ships with Discord ingest and OpenAI model provider integration. Additional providers or ingest
interfaces are not currently available and require explicit implementation.
