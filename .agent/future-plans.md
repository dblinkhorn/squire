# Future Plans (High-Level)

Purpose:

- Track non-spec, high-level future ideas outside runtime reference docs.
- Keep user-facing docs (`README.md`, `docs/*.md` reference guides) focused on implemented behavior only.

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
- Discord/runtime consistency follow-on:
  - simplify the remaining migration-shaped Discord runtime layers described in `docs/discord-runtime-consistency-hardening-spec.md`
  - goal: one explicit Discord message runtime, canonical Discord IO ownership, no registry-based runtime selection until a second real transport exists, and no test-oriented seam scaffolding in the final architecture
- Make target naming follow-on (post-Stage-6, separate PR):
  - rename Discord run targets to explicit transport names: `run-discord-bot` and `run-discord-bot-test`
  - do not add Slack make targets until Slack integration is actually implemented
  - do not keep legacy aliases (`run-bot`, `run-bot-test`) once the rename lands

## Routing Hardening Ideas

- If natural-language mutation requests become too large/noisy in practice, add deterministic plan-size guardrails:
  - max operations per plan
  - max targets per operation
