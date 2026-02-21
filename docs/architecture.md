# System Architecture

## High-Level Flow

Discord bot DM → Ingest → Raw Event (git) → Interpretation (LLM) → Derived Artifact (git) → Canonical Objects (git) → Index (SQLite) → Surfacing (push or pull) → User feedback / repair.

## Runtime Model

The system runs as a single deployable service (squire-core) with modular internal packages and stable interfaces. Optional integrations are enabled via configuration and designed for self-hosted, always-on operation.

## Multi-Transport Direction (Approved Refactor Plan)

The approved direction is to split runtime behavior into:

1. transport-agnostic shared runtime/application modules
2. transport adapter modules (Discord, Slack, future interfaces)

Design intent:

1. shared modules should not depend on transport SDK objects directly
2. adapter modules translate platform events/messages into shared contracts
3. startup wiring should converge on a runtime composition root (`squire_core.runtime`)

Migration note:

1. `/Users/dblinkhorn/squire/src/squire_core/discord_bot.py` remains a temporary compatibility shim during staged extraction
2. target end-state removes the monolithic `discord_bot.py` once entrypoints, tests, and adapter wiring are migrated

## Trust Model

Raw input is immutable, derived interpretations are versioned, canonical state is repairable, and the index can be rebuilt at any time. Canonical objects are the only mutable artifacts and are treated as the source of truth for surfacing and queries.
Canonical objects record source_event_ids so any item can be traced back to the raw events that created or modified it.

## Update/Append Pipeline

For inferred updates, the system retrieves candidate objects from the local index, asks the decision prompt to propose
create/update/append operations, then applies deterministic gates before mutation:

- auto-apply only when confidence and matching gates pass for a single target
- create a pending action for confirmation when confidence is moderate or ambiguity remains
- fall back to create in decision routing when update/append confidence is low

Pending actions can be confirmed/cancelled via Discord UI controls or text commands. Decision and matching trace
artifacts are persisted for auditability.
