# Module Responsibilities

## Ingest (Discord)

The ingest module receives DMs from a single allowed user via the Discord bot, distinguishes capture versus command, writes raw events, and emits normalized RawEvent objects. If raw writes fail, ingest returns an error and stops the pipeline.

## Interpreter (LLM)

The interpreter classifies intent, extracts structured fields, produces confidence scores, and emits DerivedEvent JSON with a strict schema. If validation fails, it stores a derivation_error artifact, responds to the user with a request to rephrase or prefix explicitly, and does not mutate canonical state. If confidence is below the configured threshold (default 0.6), it asks the user to clarify or provide an explicit prefix before creating or updating canonical objects.

## Store (Git Canonical Store)

The store applies operations to canonical objects, enforces soft-delete and append-only semantics, commits all changes, and never deletes raw or derived artifacts. Operations are validated atomically per raw event, and apply errors are recorded without partial mutation.

## Index (SQLite)

The index parses canonical objects, builds queryable tables and FTS, and is fully rebuildable from git with no authoritative data stored in SQLite.

## Surfacer

The surfacer provides push (scheduled daily/weekly digests) and pull (interactive commands), omits object IDs in user-facing list output by default, and never mutates state directly.

## Optional Providers

Optional integrations include alternative LLM backends and alternative ingest interfaces (for example, Slack, a CLI entry point, or generic webhooks). Email and SMS are potential future extensions.
