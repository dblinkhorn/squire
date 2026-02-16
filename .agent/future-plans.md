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

## Architecture Hardening Ideas

- Extract transport-agnostic application flow from `discord_bot` into reusable orchestration services so future chat interfaces (for example Slack) can reuse decision/apply/surfacing flows without duplicating transport logic.
- Run a dependency-injection audit and incrementally replace hardwired runtime dependencies (provider construction, clocks, transport hooks) with injected interfaces where it improves testability and extensibility.
- Reduce side-effect surface area in orchestration paths by moving deterministic decision logic into pure/helper modules.

Known current gaps this addresses:

- `discord_bot` remains a large orchestration surface with mixed responsibilities.
- Some dependencies are still constructed inline instead of being injected.
- Some business decisions are still close to transport and side-effect paths.

## Tooling and Gate Ideas

- Add automated dependency sync + dependency validation in `make verify-session` so agent sessions fail fast when manifests change but local environments are stale.
- Add a dedicated dependency check step in local attestation artifacts (for example `checks.dependency_sync.status`).
