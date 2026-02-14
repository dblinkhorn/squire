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
