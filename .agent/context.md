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
- Working roadmap: `docs/implementation-plan.md`.
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
  - retrieval now uses hybrid builder (`build_matching_candidates`)
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
