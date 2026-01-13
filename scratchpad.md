Decisions/assumptions:
- Canonical frontmatter schema is defined in JSON Schema and referenced as the source of truth.
- Schema validation uses jsonschema if installed; otherwise raises a clear error.
- raw_event_id uses ULID format with prefix R_ and uppercase Crockford base32.
- Project uses a Python src/ layout with minimal pyproject.toml.

Progress note:
- Implemented derived and canonical JSON schemas plus a schema loader/validator.
- Reorganized Python layout to src/squire_core.
- Next up: raw-event writer, then canonical object writer.
