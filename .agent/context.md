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

## Agent Harness + O11y Planning (2026-02-15)

- Added implementation spec: `docs/agent-harness-spec.md`.
- Core direction is dual-mode observability:
  - local ephemeral stack for dev/agent sessions (Alloy + Loki + Tempo + Prometheus, Grafana optional UI).
  - production collector-agnostic export (OTLP + structured logs).
- Session enforcement direction: local `make verify-session` gate is mandatory for executable-behavior changes and must emit `.agent/runs/<run_id>/session_gate.json`.
- Gate includes both deterministic harness checks and live Discord smoke checks; docs-only changes may skip smoke with recorded reason.
- CI-level attestation/smoke enforcement is a planned follow-on, not required in the initial rollout.
- Telemetry correlation standard should include `run_id` across logs/traces/artifacts; avoid `run_id` metric labels (high cardinality).
- Entropy-control direction: keep `AGENTS.md` as table-of-contents style navigation and reduce duplicated procedural instructions.

## Agent Harness Phase 0 Implementation (2026-02-15)

- Refactored `AGENTS.md` into table-of-contents style with canonical doc links and workflow/gate references.
- Added `docs/agent-harness-runbook.md` to separate current baseline workflow from target harness workflow.
- Added `docs/observability.md` to separate currently implemented runtime signals from planned telemetry model.
- Phase 0 intentionally does not claim `make verify-session` is implemented yet; command remains planned in spec.

## Agent Harness Phase 1 Implementation (2026-02-15)

- Added `src/squire_core/observability.py` with:
  - JSON logging formatter + stdout/stderr split logging configuration.
  - startup/session `run_id` context and generation.
  - stage observation helper (`observe_stage`) with duration logging and failure counters.
  - metrics helper scaffolding (`increment_counter`, `observe_histogram`, in-memory snapshots for tests).
  - OTLP initialization path (enabled by config/env, with OTel as required runtime dependency).
- Integrated observability hooks in `src/squire_core/discord_bot.py`:
  - startup now loads observability config, configures logging format, sets `run_id`, and initializes telemetry.
  - stage hooks added for message receive/raw write/classify/retrieve/decision/extract/apply/response/matching-trace-write.
  - counters added for messages, decision outcomes, and pending-action status transitions.
- Added tests in `tests/test_observability.py` for JSON log fields, stage metric behavior, and config loading.
- Updated docs:
  - `docs/configuration.md` with observability env/config keys.
  - `docs/observability.md` to reflect Phase 1 implemented behavior vs planned items.
- Local test note: sandbox disallows binding local sockets, so `tests/test_health_server.py` cannot run in this environment.

## Architecture Principles + Dependency Policy Update (2026-02-15)

- `AGENTS.md` now includes a dedicated architecture principles section covering boundaries, dependency direction, core-vs-IO separation, observability, DI, and testability.
- `AGENTS.md` now includes a dependency sync policy:
  - when dependency manifests change, run env sync (`uv sync` preferred; `pip install -e ".[dev]"` fallback) before validating.
  - fail fast when required dependencies for enabled features are missing.
- Future ideas were added to `.agent/future-plans.md`:
  - extract transport-agnostic orchestration from `discord_bot` for future chat interfaces.
  - run DI audit and incremental dependency inversion where it improves extensibility/testability.
  - reduce side-effect surface in orchestration paths.
  - automate dependency sync/validation in `verify-session`, including attested check results.
- Observability initialization now fails fast when explicitly enabled but required OTLP configuration is missing.

## Logging Policy Update (2026-02-15)

- Runtime logging format is now JSON-only across all environments; `SQUIRE_LOG_JSON` and `observability.logs.json` are removed from code/docs.
- `stage_complete` log events now emit at `DEBUG` level regardless of environment and are visible when `observability.log_level: "DEBUG"` is set in `config.yaml`.
- Stage duration logging now enforces a minimum `duration_ms` of `1` to avoid zero-duration rows from timing roundoff.
- Runtime verbosity defaults from `observability.log_level` in config (default `INFO`) and can be overridden at launch with `SQUIRE_LOG_LEVEL` (used by `make run-bot log_level=...`).
- Key lifecycle lines (`session_started`, `raw_event_written`, `matching_trace_written`, `response_sent`) now emit as structured events via `log_event(...)` with top-level fields.
- `discord_bot.main()` still sets runtime environment from config/env for telemetry labeling consistency.
- Startup now applies config log level after loading `config.yaml` by re-running `configure_logging(observability_config.log_level)`.

## Run Mode Selection Update (2026-02-15)

- `make run-bot` now supports local mode selection directly: `make run-bot env=dev|test|prod`.
- `SQUIRE_ENV` is applied at launch via Make target and now has precedence over `observability.environment` config in `load_observability_config`.
- This keeps runtime mode selection explicit for local agent sessions without introducing logging format switches or extra enforcement checks.

## Phase 2 Spec Hardening (2026-02-15)

- Expanded `docs/agent-harness-spec.md` with a normative Phase 2 execution contract that now fixes:
  - required local o11y services, ports, and config files
  - Alloy routing expectations (logs->Loki, traces->Tempo, metrics->Prometheus remote write)
  - required make target behavior and run env file semantics
  - required query endpoints + baseline query set
  - required artifact filenames and JSON schema keys
  - pass/failed/blocked semantics plus retry/timeout defaults
- Updated `docs/agent-harness-runbook.md` with Phase 2 implementation defaults mirroring the same contract for quick operator reference.

## Architecture Guidance Clarification (2026-02-15)

- `AGENTS.md` architecture principles now explicitly call out:
  - prefer pure functions where practical
  - isolate side effects (network/disk/db/clock/env/global state) in boundary/adapter modules

## Docs Alignment Follow-up (2026-02-16)

- Completed a focused docs drift review for logging/observability/run commands.
- `docs/configuration.md` now reflects current runtime behavior:
  - includes `SQUIRE_LOG_LEVEL` as a one-off runtime override path (primarily used by `make run-bot log_level=...`).
  - replaces stale `squire init` references with `make init` / `python -m squire_core.cli_init`.
- `docs/commands.md` corrected inline `!fix` syntax to include `<id>`.
- `docs/observability.md` and `docs/agent-harness-spec.md` now clarify that OTLP backend configuration is optional, while OpenTelemetry Python packages are required runtime dependencies.
- `docs/agent-harness-runbook.md` status section now explicitly lists implemented JSON logging/run_id correlation and stage metric hooks.

## Agent Harness Phase 2 Implementation (2026-02-16)

- Implemented Phase 2 harness baseline:
  - local headless o11y stack via `docker-compose.o11y.local.yml` using `alloy`, `loki`, `tempo`, `prometheus`.
  - no Grafana UI service in local compose profile (API-first agent querying).
- Adopted modern Alloy naming convention:
  - config path is now `config/observability/config.alloy`.
- Added local observability configs:
  - `config/observability/config.alloy`
  - `config/observability/prometheus.local.yml`
  - `config/observability/tempo.local.yml`
  - `config/observability/loki.local.yml`
- Added harness tooling scripts:
  - `tools/harness/run_harness.py` (lifecycle + verify-session + session_gate emission)
  - `tools/harness/query_o11y.py` (Loki/Prometheus/Tempo API queries)
  - `tools/harness/assert_o11y.py` (machine-readable pass/failed/blocked assertions)
- Added Make targets for harness workflows:
  - `harness-bootstrap`, `harness-up`, `harness-run`, `harness-inspect`, `harness-validate`,
    `harness-down`, `harness`, `verify-session`, `o11y-up`, `o11y-down`.
- Docs aligned to implementation:
  - `docs/agent-harness-spec.md` now references `config.alloy` and headless local stack profile.
  - `docs/agent-harness-runbook.md` now reflects implemented harness targets and updated workflows.
  - `README.md` now includes a short "Agent Harness (Phase 2)" usage section.
  - `docs/configuration.md` now documents harness env vars (`SQUIRE_HARNESS_MODE`, `SQUIRE_HARNESS_NOW`, `SQUIRE_SMOKE_COMMAND`).

## Agent Harness Phase 2 Stabilization (2026-02-16)

- `emit-telemetry` now writes JSON log lines directly to `.agent/runs/<run_id>/squire.log.jsonl` (the same file Alloy tails), including manual emission flows that do not redirect stdout.
- Harness telemetry emission now runs logging at `DEBUG` for this command path so `stage_complete` events are present for stage-coverage assertions.
- Deterministic telemetry emission no longer redirects subprocess stdout to the run log file, preventing duplicate log lines now that file logging is explicit in `emit-telemetry`.
- Tempo query logic now retries search attempts for a short window to handle eventual indexing delay before traces become searchable.
- Loki query logic now retries on empty run results to reduce inspect-time races with file-tail ingestion.
- Trace assertion logic now evaluates all successful Tempo attempts (not only the first successful response), preventing false failures when early attempts are empty but later attempts contain traces.
- OTel resource attributes now include `run_id` when available so trace search by run id is more reliable in Tempo.

## Agent Harness Workflow + Isolation Update (2026-02-16)

- `make verify-session` is now documented as the required gate for executable-behavior changes; docs-only changes may skip with explicit note.
- Added a runbook/README known-good smoke flow for manual local o11y validation (`bootstrap -> o11y-up -> emit-telemetry -> inspect -> validate`).
- Harness bootstrap now allocates run-scoped local o11y isolation values and writes them to `.agent/runs/<run_id>/run.env`:
  - `SQUIRE_O11Y_PROJECT`
  - run-scoped `SQUIRE_O11Y_*_PORT` values
- Harness up/down/inspect/emission paths now reuse run-scoped isolation values from `run.env` by default, reducing cross-run collisions on shared developer hosts.

## Live Discord Smoke Contract Drafted (2026-02-16)

- Added a new normative section in `docs/agent-harness-spec.md`:
  - `Live Discord smoke automation contract (normative)`
- The section defines:
  - portable multi-developer architecture (`squire` bot + dedicated `smoke-driver` bot)
  - required/optional smoke env contract
  - test-only bot-author allowlist safety constraints
  - smoke runner/scenario/artifact contracts (`tools/harness/smoke_discord.py`, `config/smoke/discord_smoke_v1.yaml`, `smoke.json`)
  - integration telemetry evidence requirements and acceptance criteria for implementation.
