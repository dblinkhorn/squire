# Squire AI Context

## Product Scope (Current)

- Primary runtime interface is Discord.
- Primary LLM provider is OpenAI.
- Slack/multi-user/extra providers are not active priorities unless explicitly reopened.

## Behavior and Audit Invariants

- Raw events are immutable; derived artifacts are versioned; canonical objects are mutable source-of-truth.
- Update/append decisions use conservative gating with pending-action confirmation when confidence is not high and uniquely targeted.
- Canonical objects must preserve audit linkage via `source_event_ids` and `last_decision_id`.
- Pending actions are stored under `events/pending` and transition through explicit statuses.

## Active Roadmap Pointers

- Current top implementation priority: matching reliability improvements in `docs/matching-spec.md`.
- High-level future ideas: `.agent/future-plans.md`.
- Command/config contracts: `docs/commands.md` and `docs/configuration.md`.

## AI Workflow Conventions

- Canonical workflow rules live in `AGENTS.md`.
- Tracked working files: `.agent/plan.md` and `.agent/context.md`.
- Local-only session notes: `.agent/scratchpad.md`.
- Deprecated workflow artifacts: `.sop/`, `.ralph/`, `PROMPT.md`, `ralph.yml`.

## Recent Changes (2026-02-10)

- Implemented surfacing readability Phase 1 in digest/review rendering:
  emoji-prefixed headers, summary count lines, `All clear` empty-state rows, and human-readable dates with near-term relative labels.

- Kept surfacing behavior deterministic and list-first; no ranking/selection logic changes.
- Deferred explicit done/edit UI actions to a later phase; existing text commands remain the mutation path.

## Matching Decisions (2026-02-14)

- Matching spec now locks lexical normalization to `1 / (1 + max(0, bm25_rank))`.
- Phase 1 affinity defaults: last 20 touched IDs per DM/thread, 7-day decay TTL, capped additive boost (`<= 0.15`).
- Matching trace artifact shape is explicitly documented (`schema_version`, queries, mode/fallback, pool stats, weights, per-candidate component scores, ranking margins, gate outcome).
- Degraded retrieval behavior: never auto-apply when retrieval is unavailable; freeform capture falls back to create, explicit mutation commands fail with actionable error.

## Matching Clarifications (2026-02-14)

- Semantic retrieval rollout is explicitly OpenAI-first (`matching.semantic_provider`/`matching.semantic_model`) with provider abstraction retained for future local backends.
- Semantic lifecycle is now specified: create/update are incremental, done/closed notes remain searchable, archived/deleted notes are excluded from active semantic retrieval.
- Semantic artifacts (vector index, embedding cache, metadata) must live under `archive_root`; `make clear-archive` is expected to clear them.
- Full semantic reindex triggers now include `embedding_text_schema_version` changes (not only model/chunking/index schema changes).

## Matching Implementation (2026-02-14)

- Added `MatchingConfig` loading and deterministic auto-apply score/margin gates in `DecisionConfig` (`auto_min_score`, `auto_min_margin`).
- Implemented semantic index + hybrid retrieval in `src/squire_core/matching.py`:
  - local semantic rows stored in SQLite (`semantic_objects`, `semantic_meta`) under the existing `index_db`
  - OpenAI embedding generation via `OpenAIProvider.embed`
  - incremental sync keyed by deterministic embedding text hash
  - metadata-triggered full semantic reset/reindex
  - hybrid lexical/recency/affinity/semantic fusion with normalized weights and affinity cap
- Integrated matching flow in `discord_bot`:
  - retrieval now uses hybrid builder (superseded by `build_matching_candidates_async` in runtime async flow)
  - matching trace artifacts written per event (`matching_trace_v1_*.json`) with schema validation
  - affinity memory tracked per user/channel key and updated on apply/confirm paths
  - semantic sync logs added for startup and index refresh paths
- Added schema + tests:
  - `config/schemas/matching_trace_v1.json`
  - new tests for matching config and semantic/hybrid behavior (`tests/test_matching_config.py`, `tests/test_matching.py`)
- Shipped default now enables semantic matching conservatively (`matching.semantic_weight = 0.15`) rather than disabled.
- Pending action UI now uses a dynamic two-step confirmation flow in-message:
  - primary actions: `Confirm`, `Create New`, `Cancel`
  - second step confirmation with `No, go back` restoring original controls
  - cancel copy explicitly states it does nothing.

## Planned UX Routing (2026-02-15)

- Added spec for natural-language command routing to prevent command-like text from entering capture/create flows:
  `docs/nl-command-routing-spec.md`.

- Initial scope targets read-only command intents (`status`, `weekly`, `recent`, `find`, `show`) with clarification on ambiguity.

## Runtime Stability Planning (2026-02-15)

- Transport work was planned in phased form (off-loop bridge + timeouts first, async-native provider second).
- Implementation is now complete; the temporary implementation spec doc was removed after completion.

## Runtime Stability Implementation (2026-02-15)

- Implemented Phase 1 transport hardening:
  - `OpenAIProvider` now uses explicit request timeouts for interpret/embed HTTP calls.
  - `urllib` network failures are wrapped with clearer transport-level runtime errors.
  - Added `interpret_text_async` (`asyncio.to_thread`) and switched Discord message handling to use it for classify/decision/extract/candidate-query calls.
  - Matching retrieval (`build_matching_candidates`) is now offloaded via `asyncio.to_thread` from async message handling.
  - Index rebuild/semantic sync remains required for consistency but now runs off-loop via awaited `_refresh_index_async`.
- Added provider transport tests in `tests/test_openai_provider.py` (timeout wiring + URL error wrapping).

## Runtime Stability Implementation (2026-02-15, Phase 2)

- Migrated OpenAI transport to async-native HTTP in `OpenAIProvider` using `aiohttp`:
  - added await-native `interpret_async` and `embed_async`.
  - retained sync wrappers for non-async call sites (`sync_semantic_index`, startup sync) with guardrails against use inside active loops.
- Updated interpreter async path to prefer provider-native async interpretation and only fall back to thread offload for providers lacking async support.
- Added async retrieval path in matching (`build_matching_candidates_async`) so message handling can await semantic embedding calls directly.
- Discord message handling now uses await-native matching retrieval (no `to_thread` for LLM retrieval path).
- Added/updated tests for async provider and async matching retrieval (`tests/test_openai_provider.py`, `tests/test_matching.py`).
- Follow-up cleanup removed now-orphaned sync helper `interpret_text(...)` from `src/squire_core/interpreter.py`.
- Follow-up cleanup also removed now-unused sync matching retrieval path (`build_matching_candidates` and `_search_semantic_candidates`) from `src/squire_core/matching.py`; runtime and tests now target async retrieval path only.

## Numbered Mutation Planning (2026-02-15)

- Added dedicated spec for numbered mutation actions (`!done 2`, `!append 3 ...`, `!fix 1 ...`):
  `docs/numbered-mutations-spec.md`.

- Scope includes phased rollout from `!recent`/`!find` to digest/review (`!status`/`!weekly`) row targeting.
- Resolved v1 decisions: literal row numbering, include all surfaced types, and return confirmation-only (no auto re-render).
- Resolved UX decision: add concise command tips to `!recent`, `!find`, `!status`, and `!weekly` outputs; include `!recent N` max-50 reminder.
- Added future maintenance UX direction: Discord component-based bulk done flow (`Mark Items Done` button + multi-select + confirm, with optional undo) in `docs/numbered-mutations-spec.md` and summarized in `docs/commands.md`.

## Configuration Audit (2026-02-15)

- Synced config docs to runtime behavior:
  - removed unimplemented `GOOGLE_CALENDAR_CREDENTIALS` and `querying.*` references from config docs/template.
  - added missing implemented keys: `llm.interpreter_model`, `confidence.create_threshold`, and `surfacing.ideas.weekly_review`.
- `config.yaml.example` now omits the unused `querying` block to match current runtime parsing.

## Docs Accuracy Audit (2026-02-15)

- Updated runtime-facing docs to remove shipped-vs-planned drift:
  - `docs/architecture.md`: update/append pipeline now documented as implemented.
  - `docs/commands.md`: removed unimplemented NL-query/tag command claims; updated update/append section label.
  - `docs/data-model.md`: SQLite table descriptions now match actual index + semantic tables.
  - `docs/modules.md`: removed unsupported claims (single-user gate, provider registry, git-commit semantics).
  - `docs/querying.md`: rewritten to reflect lexical `!find`/`!show` current behavior; LLM querying marked planned.
  - `docs/surfacing.md`: removed LLM-assisted surfacing wording from current behavior.
  - `docs/calendar.md` and `docs/extensibility.md`: explicitly marked planned/future.
  - `docs/deployment.md`: startup sequence now includes semantic index sync behavior.
- Policy update:
  - runtime/reference docs should describe implemented behavior only.
  - planned/future work should live in working docs (`.agent/context.md`, `.agent/plan.md`, `.agent/future-plans.md`) and detailed specs.

## Workflow ToC Refresh (2026-02-16)

- Reworked `AGENTS.md` into a concise table-of-contents style workflow index.
- Added task-to-doc routing guidance so agents can quickly select relevant docs per task.
- Added a canonical docs index with brief descriptions for each doc in `docs/`.

## Numbered Mutations Phase A (2026-02-16)

- Implemented numbered target resolution for explicit mutation commands in `discord_bot`:
  - `!done <id|number>`
  - `!append <id|number> <text>`
  - `!fix <id|number> field=value ...`
- Numeric targets now resolve against the active per-user/per-channel result cursor populated by `!recent`/`!find`.
- Added deterministic failure copy for numeric resolution:
  - no active numbered cursor
  - out-of-range row number
- Added concise command tips to manual pull outputs:
  - `!recent`: numbered mutation tip + `!recent N` max-50 reminder
  - `!find`: numbered mutation tip
- Updated docs to match runtime:
  - `README.md` command list now documents `<id|number>` for mutation commands.
  - `docs/commands.md` command syntax and behavior now documents numbered targeting from recent/find.
- Added tests in `tests/test_discord_commands.py`:
  - numbered resolution success for `!done`
  - missing cursor and out-of-range failures
  - non-numeric ID fallback unchanged
  - tip footer assertions for `!recent`/`!find`.

## Numbered Mutations Phase B (2026-02-16)

- Extended numbered cursor support to digest/review command views:
  - `!status` and `!weekly` now number actionable rows and populate the same per-user/per-channel cursor used by `!show`, `!done`, `!append`, and `!fix`.
- Surfacing sections now carry deterministic per-line `object_ids` metadata in `DigestSection`; render output format remains unchanged by default.
- Command-only numbering is applied inside `discord_bot`:
  - scheduled push digests/reviews continue using unnumbered `render()` output.
  - on-demand `!status`/`!weekly` add numbered rows and include the numbered mutation tip footer when rows are actionable.
- Updated numbered-list guidance/error copy to include `!status`/`!weekly`:
  - `!show` missing-list message
  - numbered mutation resolution message.
- Added tests:
  - surfacing tests now assert digest/review `object_ids` alignment with lines.
  - discord command tests now cover `!status`/`!weekly` numbered rendering and cursor storage.
  - added end-to-end command test for `!status` cursor followed by `!done <number>` resolution.
- Follow-up fix:
  - resolved thread-context cursor lookup mismatch by adding parent-channel fallback when resolving numbered rows from thread replies (for example `!status` in channel, `!done 1` in the created thread).

## Numbered Mutations Follow-up (2026-02-16)

- Added structured numbered-mutation telemetry logs in `discord_bot`:
  - `numbered_mutation_resolved` (raw event id, command, source view, row number, object id)
  - `numbered_mutation_resolution_failed` (raw event id, command, reason, source view, row number)
- Added explicit expired-cursor behavior for numbered commands:
  - cursor resolution now distinguishes `expired` from `no_cursor`.
  - user-facing guidance now tells users to rerun a list command when the numbered list has expired.
- Added safe failure telemetry for wrong-type numbered `!done` attempts (`reason=wrong_type`).
- Added tests for missing phase-A/B coverage in `tests/test_discord_commands.py`:
  - numbered `!append` resolution path
  - numbered `!fix` resolution path
  - expired cursor guidance path
  - wrong-type numbered `!done` rejection
  - telemetry emission checks for resolved/failure paths.

## Weekly Completed Section UX (2026-02-16)

- Weekly review section title is now `Completed this week` (replacing legacy `Recently changed notes` language in tests/spec docs).
- `Completed this week` is intentionally omitted when it has no rows; it does not render with `All clear`.
- Other weekly sections keep existing empty-state behavior (`All clear`) for predictable scanning.

## Test Mode Reset+Seed Spec (2026-02-16)

- Added spec doc for separate PR: `docs/test-env-reset-seed-spec.md`.
- Proposed behavior: `SQUIRE_ENV=test` triggers startup reset + deterministic fixture seeding + index rebuild before normal bot startup flow.
- Includes explicit guardrails to fail closed unless `archive_root` is test-safe (`/tmp` or containing `squire-test`) and keeps `run-bot` non-destructive.

## Test Mode Reset+Seed Implementation (2026-02-16)

- Implemented startup test mode wiring in `discord_bot`:
  - new `_run_test_mode_reset_seed(...)` executes only when `SQUIRE_ENV=test`.
  - validates archive guardrails (`/tmp` or `squire-test` path segment) before destructive reset.
  - clears archive contents (preserving `.git`), writes deterministic seed fixtures, and rebuilds SQLite index.
  - emits structured startup logs:
    - `test_mode_startup_enabled`
    - `test_mode_reset_completed`
    - `test_mode_seed_completed`
    - `test_mode_rebuild_index_completed`
    - `test_mode_startup_failed` (on failure, startup exits).
- Added dedicated seed helper module: `src/squire_core/test_seed.py`.
  - uses fixed deterministic `TEST_*` IDs for admin/projects/people/ideas fixture records.
  - seed timestamps are UTC and relative to startup `now`.
- Added convenience make target:
  - `make run-bot-test` (exports `SQUIRE_ENV=test`).
  - kept `make run-bot` unchanged/non-destructive.
- Added tests:
  - `tests/test_test_seed.py`
  - `tests/test_test_mode_startup.py`
- Updated docs:
  - `README.md`
  - `docs/configuration.md`
  - `docs/deployment.md`

## Test Archive Root Override (2026-02-16)

- Added optional config key `test_archive_root`.
- Startup now applies a test-only archive-root override before archive-path normalization when `SQUIRE_ENV=test`.
- Behavior:
  - non-test env: unchanged, uses `archive_root`.
  - test env with `test_archive_root` set: uses `test_archive_root` as active archive root.
- Guardrails are unchanged and apply to the active root used by test mode.
- Added tests in `tests/test_test_mode_startup.py` for override gating and normalization interaction.
