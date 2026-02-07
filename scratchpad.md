Decisions/assumptions:
- Use config.yaml for local configuration; it is git-ignored.
- config.yaml.example is the committed reference template.
- Two-step LLM interpretation (classify then extract) with strict bucket schemas.
- Interpreter falls back to title as next_action for admin items when unclear.
- Prompt overrides are file-based via llm.classify_prompt_path and llm.interpreter_prompt_path.
- Derived events are persisted under events/derived/<raw_event_id>/ with raw model output and error logs.
- IDs use python-ulid for time-sortable ULID strings; raw events are prefixed (e.g., R_).
- Planned: optional GitHub repo creation/backup for the archive storage.
- Next: link raw events to canonical objects (source_event_ids) and add pending action confirmation flow.
- Possible future task: add a scripts/utils directory for helper commands (e.g., clear-archive).
- Possible future functionality: support locally hosted LLMs as an optional backend.
- PDD: update/append thresholds (auto >=0.85 single match; confirm 0.65–0.84 or multiple; create <0.65 or reject).
- Added decision config defaults: auto_apply_threshold 0.85, confirm_threshold 0.65, candidate_limit 3, candidate_score_threshold 0.2.
- PDD: confirmations via buttons, select menus for multiple candidates, text fallback; add “Was this incorrect?” button even on auto-apply.
- PDD: pending actions should track last_updated; canonical objects should record last_decision_id.
- Pending actions are stored as JSON under events/pending with status transitions (pending/confirmed/cancelled/failed).
- Decision gating: update/append auto-apply only for single target at/above auto_apply_threshold; otherwise create pending actions at/above confirm_threshold; otherwise force create.
- PDD: Slack integration and multi-user support are out of scope for now; production hardening gets its own plan.

Notes:
- Add bot-side logging (raw id, classification, apply result) later.
- Replace title-based IDs with short random alphanumeric IDs (no extra deps).
- Tests: config defaults test skips when PyYAML is unavailable in the environment.
- Normalize `paths.index_db` under `archive_root`; rebuild SQLite index on bot startup if missing.
- Canonical admin due fields are mutually exclusive during apply: setting `due_at` clears `due_date`, and setting `due_date` clears `due_at`.
- Extraction prompt now asks for timezone offsets in `due_at` values to reduce ambiguity across hosts/timezones.
- Matching roadmap documented in `docs/matching-spec.md` with a phased plan:
  deterministic boosts + stricter auto-apply gate first, optional semantic retrieval second.
- Surfacing priority updated: implement `docs/surfacing-spec.md` first, with note-only outputs
  (no suggested next actions, no object IDs in user-facing surfaced lists by default).
- Implemented surfacing phase 1:
  `!recent`, `!find`, `!show <number>` with cursor TTL, digest sections updated to note-only surfacing,
  and configurable no-ID output (default false for include_ids).
- Removed unused `surfacing.admin.include_open_limit` from code/example config.
- Deployment readiness added:
  - `src/squire_core/cli_init.py` implemented (working `make init` target path).
  - `Dockerfile`, `.dockerignore`, and baseline `docker-compose.yml` added.
  - docs updated for Docker Compose + homelab integration and `/data/archive` mount path.

Session notes (2026-01-21):
- ./specs directory not found; confirm where specs live.
- Need decisions on pending action storage format/location and surfacing scheduler model.
- Reviewed docs/; implementation plan aligns with current task list.
- Confidence gating + pending action confirm/cancel flows are in place; next task is Discord UI confirmations/select + auto-apply feedback.
