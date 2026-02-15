# Future Plans (High-Level)

Purpose:
- Track non-spec, high-level future ideas outside runtime reference docs.
- Keep user-facing docs (`README.md`, `docs/*.md` reference guides) focused on implemented behavior only.

## Near-Term Priorities

- Natural-language command routing for read-only intents (`status`, `weekly`, `recent`, `find`, `show`).
- Numbered mutation ergonomics (`!done 2`, `!append 3 ...`, `!fix 1 ...`).

## Command UX Ideas

- Potential follow-on commands: `!due [days]`, `!archive`, `!help`, `!rebuild-index`.
- Optional Discord bulk-maintenance controls on surfaced lists (for example multi-select done flow).

## Integration Ideas

- Calendar integration (for example event creation from admin items) if explicitly prioritized.
- Optional archive backup automation (for example remote git backup workflow).

## Extensibility Ideas

- Formal provider interfaces for alternate ingest channels and model backends.
- Clear plugin boundaries for extension without changing core runtime modules.
