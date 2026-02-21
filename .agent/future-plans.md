# Future Plans (High-Level)

Purpose:

- Track non-spec, high-level future ideas outside runtime reference docs.
- Keep user-facing docs (`README.md`, `docs/*.md` reference guides) focused on implemented behavior only.

## Near-Term Priorities

- Natural-language command routing with LLM intent interpretation for read + mutation intents,
  using confirmation-first mutation handling and explicit-only destructive controls.
- Numbered mutation ergonomics (`!done 2`, `!append 3 ...`, `!fix 1 ...`).

## Command UX Ideas

- Potential follow-on commands: `!due [days]`, `!archive`, `!help`, `!rebuild-index`.
- Optional Discord bulk-maintenance controls on surfaced lists (for example multi-select done flow).
- UX gap to address: users can naturally ask to "archive number X", but there is no item-level archive command yet, and NL phrasing can be misread as destructive global `clear-archive`.
- Proposed fix path:
  - add `!archive <id|number>` for all object types (`archived=true`) to remove items from normal surfacing
  - expand `!done <id|number>` beyond admin where type semantics are clear (`projects -> completed`, `ideas -> done`)
  - keep `people` out of `!done`; use `!archive` or explicit `!fix`
  - add symmetry commands `!reopen`/`!unarchive`
  - add NL guardrail so item-targeted "archive <number>" requests never map to global `clear_archive`

## Integration Ideas

- Calendar integration (for example event creation from admin items) if explicitly prioritized.
- Optional archive backup automation (for example remote git backup workflow).

## Extensibility Ideas

- Formal provider interfaces for alternate ingest channels and model backends.
- Clear plugin boundaries for extension without changing core runtime modules.

## Routing Hardening Ideas

- If natural-language mutation requests become too large/noisy in practice, add deterministic plan-size guardrails:
  - max operations per plan
  - max targets per operation
