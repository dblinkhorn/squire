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

## Shared Transport Runtime (Refactor Direction)

The approved refactor direction introduces shared modules for transport-agnostic runtime behavior (for example command
orchestration, NL routing/normalization, cursor/clarification state handling, and reminder scheduling helpers).

These modules are intended to be reused across Discord, Slack, and future interfaces.

## Transport Adapters (Refactor Direction)

Adapters are responsible for platform-specific concerns only:

1. event ingestion and platform identity/channel resolution
2. platform message send/edit/reaction/action mechanics
3. platform UI components and scheduler loop wiring

Design intent:

1. keep transport SDK imports in adapter modules
2. keep shared runtime logic transport-neutral
3. use adapter-to-shared contracts instead of passing SDK-native objects into shared layers

Current Discord adapter/runtime split:

1. `/Users/dblinkhorn/squire/src/squire_core/transport/discord/adapter.py`:
event translation (`discord.Client` lifecycle hooks) and Discord message/reaction IO helpers.
2. `/Users/dblinkhorn/squire/src/squire_core/transport/discord/views.py`:
Discord UI views (`PendingActionView`, `MutationPendingView`, `AutoApplyFeedbackView`) and interaction callbacks.
3. `/Users/dblinkhorn/squire/src/squire_core/transport/discord/scheduler.py`:
digest/reminder scheduling loops and Discord delivery channel resolution.
4. `/Users/dblinkhorn/squire/src/squire_core/runtime.py`:
runtime composition root that wires shared transport modules, adapter callbacks, and startup behavior.

## Optional Providers

Current runtime ships with Discord ingest and OpenAI model provider integration. Additional providers or ingest
interfaces are not currently available and require explicit implementation.
