# System Architecture

## High-Level Flow

Discord bot DM → Ingest → Raw Event (git) → Interpretation (LLM) → Derived Artifact (git) → Canonical Objects (git) → Index (SQLite) → Surfacing (push or pull) → User feedback / repair.

## Runtime Model

The system runs as a single deployable service (squire-core) with modular internal packages and stable interfaces. Optional integrations are enabled via configuration and designed for self-hosted, always-on operation.

## Trust Model

Raw input is immutable, derived interpretations are versioned, canonical state is repairable, and the index can be rebuilt at any time. Canonical objects are the only mutable artifacts and are treated as the source of truth for surfacing and queries.
