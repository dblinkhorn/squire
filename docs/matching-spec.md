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
   - fuse lexical and semantic ranks with weighted reciprocal rank fusion.

Return top `candidate_limit` after de-duplication and scoring.

### 2) Deterministic Confidence Gate Before Auto-Apply

Keep LLM decision, but require additional deterministic match quality constraints for auto-apply:

- exactly one target operation proposed
- decision confidence >= `auto_apply_threshold`
- top candidate score >= `matching.auto_min_score`
- `(top_score - second_score) >= matching.auto_min_margin` when a second candidate exists

If any condition fails, create a pending action (confirmation required).

### 3) Matching Trace Artifact

Write a derived matching trace per event (JSON) with:

- retrieval queries used
- shortlisted candidates and component scores
- final fused ranking
- gating outcome (auto_apply/needs_confirmation/create)

This keeps matching behavior debuggable and auditable.

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
  semantic_weight: 0.0
  auto_min_score: 0.55
  auto_min_margin: 0.20
  candidate_limit: 5
```

Notes:
- `semantic_weight` stays `0.0` until semantic index is implemented.
- `candidate_limit` here applies to retrieval; decision layer may still cap final list separately.

## Data/Schema Changes

- No canonical schema changes required.
- Add derived schema for matching trace artifact (for example `config/schemas/matching_trace_v1.json`).
- Extend index (or helper cache) to expose fields needed for structured boosts (for example `next_action`, `updated_at` already present; add lightweight affinity cache keyed by channel/thread).

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

## Rollout Plan

1. Phase 1 (deterministic improvements):
   - structured boosts, affinity boost, tighter auto-apply gate, matching trace artifact.
2. Phase 2 (optional semantic retrieval):
   - local embeddings index, rank fusion, threshold retune.
3. Phase 3:
   - feedback-driven tuning using confirm/cancel/fix outcomes.

## Risks and Mitigations

- Risk: over-tuned thresholds reduce convenience.
  - Mitigation: monitor confirmation rate and retune with eval set.
- Risk: semantic retrieval increases false positives.
  - Mitigation: keep semantic weight low by default and require margin checks.
- Risk: complexity/regression in matching path.
  - Mitigation: keep changes layered, with trace artifacts and regression tests per phase.
