# System Architecture

## High-Level Flow

Discord bot DM → Ingest → Raw Event (git) → Interpretation (LLM) → Derived Artifact (git) → Canonical Objects (git) → Index (SQLite) → Surfacing (push or pull) → User feedback / repair.

## Runtime Model

The system runs as a single deployable service (squire-core) with modular internal packages and stable interfaces. Optional integrations are enabled via configuration and designed for self-hosted, always-on operation.

## Transport Runtime Structure

Runtime behavior is split into:

1. transport-agnostic shared runtime/application modules
2. transport adapter modules (Discord and future interfaces, for example Slack)

Design rules:

1. shared modules should not depend on transport SDK objects directly
2. adapter modules translate platform events/messages into shared contracts
3. startup wiring lives behind a runtime composition root (`squire_core.runtime`)

Runtime composition:

1. `/Users/dblinkhorn/squire/src/squire_core/runtime.py` is the runtime composition root and startup entrypoint.
2. Runtime transport selection resolves through `/Users/dblinkhorn/squire/src/squire_core/transport/runtime_registry.py` (currently defaults to `discord`, configurable with `SQUIRE_TRANSPORT`).
3. Discord transport entrypoint resolves through `/Users/dblinkhorn/squire/src/squire_core/transport/discord/runtime.py`.
4. Discord-specific adapter code is split under `/Users/dblinkhorn/squire/src/squire_core/transport/discord/`:
`adapter.py` (client lifecycle/IO), `context.py` (Discord message to `TransportMessageContext` translation), `io.py` (Discord IO wrappers), `views.py` (Discord UI interactions), and `scheduler.py` (digest/reminder loops).
5. Discord runtime owns startup wiring and runtime-adapter class composition, including process-scoped `RuntimeStateStore` injection; Discord message ingress lives in `/Users/dblinkhorn/squire/src/squire_core/transport/discord/message_entry.py`.

## Trust Model

Raw input is immutable, derived interpretations are versioned, canonical state is repairable, and the index can be rebuilt at any time. Canonical objects are the only mutable artifacts and are treated as the source of truth for surfacing and queries.
Canonical objects record source_event_ids so any item can be traced back to the raw events that created or modified it.

## Update/Append Pipeline

For inferred updates, the system retrieves candidate objects from the local index, runs a candidate-aware capture
interpretation that extracts fields and proposes create/update/append operations in one model call, then applies
deterministic gates before mutation:

- auto-apply only when confidence and matching gates pass for a single target
- create a pending action for confirmation when confidence is moderate or ambiguity remains
- fall back to create in decision routing when update/append confidence is low

Pending actions can be confirmed/cancelled via Discord UI controls or text commands. Decision and matching trace
artifacts are persisted for auditability.
