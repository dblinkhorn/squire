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
- CI/release baseline added:
  - GitHub Actions `CI` workflow runs compile checks + `pytest` on PRs and pushes to `main`.
  - GitHub Actions `Docker Publish` workflow publishes multi-arch images to Docker Hub on `vX.Y.Z` tags.
  - Required GitHub secrets: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
  - Release pattern is tag-driven SemVer (`git tag vX.Y.Z && git push origin vX.Y.Z`), no release automation bot required yet.

Session notes (2026-01-21):
- ./specs directory not found; confirm where specs live.
- Need decisions on pending action storage format/location and surfacing scheduler model.
- Reviewed docs/; implementation plan aligns with current task list.
- Confidence gating + pending action confirm/cancel flows are in place; next task is Discord UI confirmations/select + auto-apply feedback.

Session notes (2026-02-08):
- Repository familiarization pass completed across README, docs/, src/, and tests/.
- Surfacing phase 1 appears shipped (`!status`, `!recent`, `!find`, `!show` + cursor TTL + no-ID output mode).
- Highest-priority v1 gap: weekly review scheduling/formatting from `docs/surfacing-spec.md` is still not implemented in runtime/config plumbing.
- Next highest gap: matching-spec phase 1 deterministic improvements (hybrid retrieval boosts, deterministic auto-apply gate, matching trace artifact) are still pending.
- UX mismatch to resolve: with `surfacing.output.include_ids: false`, mutation commands still require raw IDs (`!done/!append/!fix <id>`), which weakens no-ID workflows.
- Local test command currently unavailable in this shell because `python3 -m pytest` cannot run without installing pytest in the environment.

Session notes (2026-02-08, task 1):
- Implemented weekly review surfacing composer (`build_weekly_review`) with list-only sections:
  recently changed notes, open admin without due dates, blocked/stale projects, people overdue for contact,
  and optional recent ideas.
- Added weekly schedule runtime support in Discord bot:
  `schedule.weekly_review_day` + `schedule.weekly_review_time` parsing, next-run calculation, and async send loop.
- Added config example keys for weekly scheduling and updated docs to reflect shipped weekly review behavior.
- Added tests:
  - `tests/test_surfacing.py`: weekly review composition + optional ideas section.
  - `tests/test_discord_schedule.py`: weekday parsing and next weekly run behavior.
- Validation:
  - `.venv/bin/python -m pytest -q` => `28 passed`.

Session notes (2026-02-08, task 1 follow-up):
- Added manual `!weekly` command so weekly review output can be triggered on demand without waiting for scheduler time.
- Updated command docs and added command-handler coverage in `tests/test_discord_schedule.py`.
- Validation:
  - `.venv/bin/python -m pytest -q` => `29 passed`.

Session notes (2026-02-08, env/tooling):
- Chose to stay on `pip` install paths for now (no migration to `uv` in CI/Makefile/Docker yet).
- Added `uv.lock` to `.gitignore` to avoid tracking lockfile drift while `pip` remains the install source of truth.

Session notes (2026-02-08, v1.0 gap assessment):
- Branch currently only changes `README.md`; test suite is green (`29 passed`).
- Highest remaining roadmap item is still matching reliability phase 1 from `docs/matching-spec.md`
  (hybrid retrieval boosts, deterministic auto-apply gate, matching trace artifact).
- Command-path consistency gap: `!confirm` applies pending actions but does not refresh SQLite index afterward.
- Product UX gap: `surfacing.output.include_ids: false` hides IDs while mutation commands (`!done/!append/!fix`) still require raw IDs.
- Safety gap: `!fix` currently accepts broad key/value updates instead of a strict allowlist.
- Docs/runtime alignment gaps remain for natural-language querying claims and other planned integrations.

Session notes (2026-02-08, task 2):
- Implemented command-path index refresh for `!confirm` so text confirmations now refresh SQLite immediately
  (parity with button-based confirmations).
- Hardened `!fix`:
  - switched token parsing to `shlex.split` to support quoted multi-word values.
  - added strict per-type editable field allowlists.
  - blocked immutable/internal fields (`id`, `type`, `created_at`, `updated_at`, `source_event_ids`, `last_decision_id`).
  - added strict value validation for enums and ISO date/datetime fields (including timezone requirement for admin datetime fields).
- Updated `docs/commands.md` to reflect strict `!fix` validation and quoting behavior.
- Added command tests in `tests/test_discord_commands.py` covering:
  - `!confirm` refresh behavior
  - quoted `!fix` parsing
  - disallowed field rejection
  - invalid enum rejection
- Validation:
  - `.venv/bin/python -m pytest -q` => `33 passed`.

Session notes (2026-02-08, task 3):
- Added destructive bot command flow for archive reset:
  - `!clear-archive` starts a confirmation window for the same user/channel.
  - User must send plain `DELETE` within 2 minutes to execute.
  - Confirmation message `DELETE` is intercepted before capture, so it does not become a note.
- Archive clear behavior mirrors Makefile intent:
  removes all top-level entries under `archive_root` while preserving `.git`.
- Added tests for:
  - starting confirmation
  - successful DELETE-based clear (with `.git` preserved)
  - DELETE without pending confirmation warning.
- Updated README/docs command lists to include `!clear-archive`.
- Validation:
  - `.venv/bin/python -m pytest -q` => `36 passed`.

Session notes (2026-02-08, task 4):
- Updated command-level surfacing behavior so manual pull commands always include IDs:
  - `!recent`, `!find`, and `!show` now force `surfacing.output.include_ids=true` via command-local config override.
  - `!status` and `!weekly` remain unchanged and continue to follow digest/no-ID behavior.
- Added tests to verify command overrides for `!recent`, `!find`, and `!show` even when global config has `include_ids: false`.
- Updated `docs/commands.md` to document manual-command ID inclusion vs digest behavior.
- Validation:
  - `.venv/bin/python -m pytest -q` => `39 passed`.

Session notes (2026-02-08, task 5):
- Refined ID visibility config to be digest-specific:
  - renamed surfacing key to `surfacing.output.show_ids_daily_weekly`.
  - removed legacy `surfacing.output.include_ids` fallback; only the new key is honored.
- Manual pull surfacing (`!recent`, `!find`, `!show`) now includes IDs directly in `surfacing.py`
  and no longer relies on any command-level config override.
- Updated docs/config/template:
  - `config.yaml.example`, `docs/configuration.md`, `docs/surfacing-spec.md`.
- Updated tests:
  - `tests/test_surfacing.py` now expects IDs for manual pull output while preserving digest no-ID behavior by default.
  - `tests/test_discord_commands.py` verifies command handlers do not override digest ID config.
- Validation:
  - `.venv/bin/python -m pytest -q` => `39 passed`.

Session notes (2026-02-09, uptime monitoring):
- Added first-class HTTP liveness endpoint in runtime: `GET /health` served by a lightweight stdlib HTTP server.
- Health server config is env-based: `HEALTH_HOST` (default `0.0.0.0`), `HEALTH_PORT` (default `8080`, `0` disables).
- Docker health checks now probe `http://127.0.0.1:${HEALTH_PORT}/health` instead of env-var presence checks.
- Compose now exposes the health port and passes `HEALTH_HOST`/`HEALTH_PORT` into container env.
- Added unit tests for health-port parsing and `/health`/404 behavior.

Session notes (2026-02-10, docs genericity cleanup):
- Removed setup-specific monitoring/deployment references from public docs (for example `pi4`, Raspberry Pi, and Uptime Kuma-specific wording).
- Standardized health monitoring guidance around generic HTTP checks for `GET /health` with placeholder host/container values.
- Replaced region-specific timezone example (`America/Los_Angeles`) with a neutral IANA example (`Etc/UTC`) in docs/templates.
