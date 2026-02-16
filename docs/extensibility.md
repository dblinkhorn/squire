# Extensibility Model

## Stable Interfaces

Current state:

- Squire is modular by package (`discord_bot`, `matching`, `surfacing`, `operation_apply`, etc.) but does not yet
  expose formal provider registration interfaces.

- Prompt behavior is configurable via `config.yaml` prompt paths under `llm.*`.
- Additional ingest/store/index/provider integrations currently require code changes.
