# Natural-Language Note Matching Spec

## Problem

Squire's update/append flow depends on finding the correct existing canonical object from free-form user text. The current retrieve-then-decide path works, but matching confidence is still variable for paraphrases and short correction messages (for example, "actually call dentist at 4pm").

This spec defines a pragmatic upgrade path that improves edit reliability without sacrificing auditability or safety.

## Goals

- Increase correct target selection for natural-language edits.
- Keep auto-apply conservative; prefer confirmation over silent wrong edits.
- Preserve rebuildability and local-first architecture (git canonical store + derived indexes).
- Keep behavior explainable in logs/artifacts.

## Non-Goals

- Replacing current pending-action confirmation flows.
- Making LLM output authoritative.
- Introducing multi-user identity resolution in this phase.

## Current Baseline

- Candidate retrieval: SQLite FTS5 over title/body (`indexer.find_candidates`).
- Optional query expansion: LLM-generated short retrieval queries.
- Decision: LLM chooses create/update/append from candidate shortlist.
- Gate: thresholds decide auto-apply vs confirm vs create fallback.

## Proposed Design

### 1) Hybrid Candidate Retrieval

Use multiple signals to build a better shortlist before decisioning:

1. Lexical retrieval (existing FTS5 path).
2. Structured boosts:
   - exact token overlap with title/next_action
   - object type match (already scoped, keep strict)
   - recency boost (`updated_at`)
   - conversation affinity boost (recently touched object IDs in current DM/thread)
3. Optional semantic retrieval (phase 2):
   - local embedding index over canonical objects (derived artifact, rebuildable)
   - initial embedding provider/model should use the existing OpenAI integration for fast rollout
   - fuse lexical and semantic signals with deterministic weighted scoring.

Deterministic scoring model:

- Retrieve an expanded pool per query using `candidate_pool = min(max_candidate_pool, candidate_limit * candidate_multiplier)`.
- Lexical normalization uses:
  - `lexical_score = 1 / (1 + max(0, bm25_rank))`
- Normalize remaining component signals to 0..1 and compute:
  - `final_score = lexical_weight*lexical + recency_weight*recency + affinity_weight*affinity + semantic_weight*semantic`
- Normalize configured weights to sum to `1.0` over active signals at runtime.
- De-duplicate by object ID, keep highest-scoring row, then return top `candidate_limit`.

Notes:

- Semantic matching ships enabled with a conservative default (`semantic_weight: 0.15`) and should be tuned with telemetry.
- This design intentionally keeps retrieval deterministic and inspectable for easier threshold tuning.
- Affinity defaults for phase 1:
  - track recently touched IDs per DM/thread
  - keep the last `20` IDs
  - apply affinity decay over `7 days`
  - cap affinity contribution to a small additive boost (for example `<= 0.15`) so affinity helps tie-breaks but cannot dominate lexical relevance

### 2) Deterministic Confidence Gate Before Auto-Apply

Keep LLM decision, but require additional deterministic match quality constraints for auto-apply:

- exactly one target operation proposed
- decision confidence >= `auto_apply_threshold`
- top candidate score >= `matching.auto_min_score`
- `(top_score - second_score) >= matching.auto_min_margin` when a second candidate exists

If any condition fails, create a pending action (confirmation required).

### 3) Matching Trace Artifact

Write a derived matching trace per event (JSON) with:

- `schema_version`
- `raw_event_id`
- `object_type`
- `timestamp`
- `queries` (retrieval queries used)
- `retrieval_mode` (`hybrid`, `lexical_only`, `semantic_only`, `none`)
- `fallback_reason` (optional; populated when degraded)
- `candidate_pool`:
  - `before_dedupe`
  - `after_dedupe`
  - `returned_k`
- `weights` (normalized values used for this run):
  - `lexical`
  - `recency`
  - `affinity`
  - `semantic`
- `candidates` (shortlisted rows):
  - `id`
  - `title`
  - `component_scores` (`lexical`, `recency`, `affinity`, `semantic`)
  - `final_score`
- `ranking`:
  - ordered IDs/scores
  - `top_score`
  - `second_score`
  - `margin`
- `gate`:
  - `decision_confidence`
  - `auto_min_score`
  - `auto_min_margin`
  - `outcome` (`auto_apply`, `needs_confirmation`, `create`)

This keeps matching behavior debuggable and auditable.

### 4) Retrieval Resilience and Index Freshness

Matching should degrade gracefully and never hard-fail ingestion because one retrieval path is unavailable.

Fallback behavior:

- If semantic retrieval is unavailable, continue with lexical + structured boosts.
- If lexical retrieval is unavailable, continue with semantic + structured boosts.
- If both retrieval paths are unavailable, never auto-apply:
  - freeform capture path: fallback to `create` (non-blocking capture)
  - explicit mutation command path (`!append`, `!fix`, `!done`): fail with actionable error; do not create implicitly
- Always record degraded mode in the matching trace artifact.

Semantic index freshness/versioning:

- Persist semantic index metadata including:
  - `embedding_provider`
  - `embedding_model`
  - `chunk_size`
  - `chunk_overlap`
  - `embedding_text_schema_version`
  - `index_schema_version`
- Trigger full semantic reindex when any metadata value changes.
- Trigger incremental/background sync on canonical object writes.
- On startup, run a background sync check and avoid blocking normal command handling.
- Queries should use the last successful index snapshot while background sync runs.

### 5) Semantic Embedding Generation and Lifecycle

Semantic indexing should be deterministic and incremental.

Embedding text composition:

- Build a deterministic embedding text payload from canonical content:
  - required: `id`, `type`, `title`
  - type-specific high-signal fields when present (for example `next_action`, `one_liner`, `status`)
  - canonical body text
- Keep the embedding text builder versioned via `embedding_text_schema_version`.
- In phase 2, default to one embedding per object first; introduce chunking only when needed for long-object recall.

Incremental update behavior:

- On create: generate embeddings for the new object and write semantic rows.
- On update/append: recompute embedding text hash and only re-embed changed objects/chunks.
- On unchanged content: reuse stored vectors (no re-embedding).

Object-state behavior:

- `status=done` (or equivalent closed status) remains indexed and searchable.
- Retrieval may apply a small deterministic down-rank for done/closed status, but must not hard-exclude those objects.
- `archived=true` objects are excluded from active semantic retrieval.
- If an object is deleted from canonical storage, remove its semantic rows.

Storage and clearing behavior:

- Semantic index files, embedding caches, and semantic metadata must live under `archive_root` as derived artifacts.
- `make clear-archive` must remove all semantic/vector artifacts along with other derived archive data.

## Timezone & Datetime Handling

Matching itself should not reinterpret user intent into a different timezone. Datetime handling rules:

- extraction continues using configured/reference timezone for relative phrases
- `due_at` must include timezone offset in extracted payloads
- update application keeps due fields mutually exclusive:
  - setting `due_at` clears `due_date`
  - setting `due_date` clears `due_at`

This avoids stale due-field conflicts while keeping matching logic independent from timezone math.

## Config Additions

Add a new `matching` block in `config.yaml`:

```yaml
matching:
  lexical_weight: 1.0
  recency_weight: 0.15
  affinity_weight: 0.25
  semantic_weight: 0.15
  semantic_provider: "openai"
  semantic_model: "text-embedding-3-small"
  candidate_multiplier: 4
  max_candidate_pool: 20
  affinity_recent_ids_per_thread: 20
  affinity_ttl_days: 7
  affinity_max_boost: 0.15
  auto_min_score: 0.55
  auto_min_margin: 0.20
  candidate_limit: 5
```

Notes:

- `semantic_weight` ships with a conservative non-zero default and can be adjusted per deployment.
- `semantic_provider`/`semantic_model` default to OpenAI for initial rollout because Squire already depends on OpenAI for interpretation.
- `candidate_limit` controls post-fusion shortlist size; `candidate_multiplier` and `max_candidate_pool` control pre-fusion recall depth.
- Weight values are normalized at runtime across active signals.

## Data/Schema Changes

- No canonical schema changes required.
- Add derived schema for matching trace artifact (for example `config/schemas/matching_trace_v1.json`).
- Extend index (or helper cache) to expose fields needed for structured boosts (for example `next_action`, `updated_at` already present; add lightweight affinity cache keyed by channel/thread).
- Add semantic index metadata storage (provider/model/chunking/embedding text schema/index schema version) to drive safe rebuild triggers.
- Add semantic embedding cache keyed by normalized embedding text hash + provider/model.
- Ensure semantic artifacts are archive-derived and live under `archive_root`.

## Evaluation Plan

Create a fixed evaluation set (JSONL) for edit scenarios:

- corrections ("actually ...")
- paraphrases with same target
- near-duplicate titles
- multi-candidate ambiguity requiring confirmation
- date-only -> datetime and datetime -> date-only transitions

Track:

- `top1_candidate_recall`
- `decision_target_accuracy`
- `auto_apply_precision`
- `confirmation_rate`

## Acceptance Criteria

- `decision_target_accuracy >= 0.90` on eval set.
- `auto_apply_precision >= 0.98` on eval set.
- no increase in unintended auto-applies vs baseline test corpus.
- all matching and gating outcomes logged in derived artifacts.
- done/closed objects remain retrievable via semantic search.
- archived/deleted objects are excluded from active semantic retrieval.

## Rollout Plan

1. Phase 1 (deterministic improvements):
   - explicit weighted fusion over lexical + deterministic boosts
   - candidate pool expansion (`candidate_multiplier`) and de-dup top-K
   - tighter auto-apply gate
   - richer matching trace artifact (component scores + margins + mode/fallback)
2. Phase 2 (optional semantic retrieval):
   - local embeddings index
   - semantic signal added to fusion (`semantic_weight > 0`)
   - index freshness/versioning + background sync/rebuild flow
   - threshold retune
3. Phase 3:
   - feedback-driven tuning using confirm/cancel/fix outcomes.

## Risks and Mitigations

- Risk: over-tuned thresholds reduce convenience.
  - Mitigation: monitor confirmation rate and retune with eval set.
- Risk: semantic retrieval increases false positives.
  - Mitigation: keep semantic weight low by default and require margin checks.
- Risk: complexity/regression in matching path.
  - Mitigation: keep changes layered, with trace artifacts and regression tests per phase.
