# Squire AI Context

## Purpose

- This file is a durable handoff for future fresh-context agents.
- Keep only need-to-know decisions, constraints, and loose ends that materially affect future work.

## Product Scope (Current)

- Primary runtime interface is Discord.
- Runtime uses one active LLM provider selected by `config.yaml` (`llm.provider`, `llm.model`); current implemented provider support is OpenAI.
- Natural-language command routing is in active use for read and mutation intents.
- High-level future ideas belong in `.agent/future-plans.md`.

## Core Invariants

- Raw events are immutable; derived artifacts are versioned; canonical objects are mutable source-of-truth.
- Explicit `!` commands have highest precedence.
- `!clear-archive` + `DELETE`, `!confirm`, and `!cancel` remain explicit-only controls.
- Mutation writes stay confirmation-first unless explicitly designed otherwise.
- Canonical object audit linkage (`source_event_ids`, `last_decision_id`) must be preserved.

## Durable Runtime Decisions

### Unified Lifecycle

- All canonical note types now use one lifecycle field: `status` with values `open|done`.
- `!done` sets `status=done` and records `done_at`; `!reopen` sets `status=open` and clears `done_at`.
- `blocked_reason` remains as informational metadata; blocked project/admin surfacing is derived from whether that field is present.
- No compatibility seams remain for `archived`, `completed_at`, or legacy per-type active status vocabularies.

### Numbered Mutation UX

- `!done`, `!reopen`, `!append`, and `!fix` support `<id|number>` targets.
- Numbered targets resolve from one current cursor per conversation root: the dedicated Discord channel and all
  Squire-created threads beneath it share the same cursor.
- Cursor sources include `!recent`, `!active`, `!find`, `!status`, `!weekly`, and scheduled daily/weekly reports.
- Every newly displayed daily/weekly report replaces the cursor, including reports with no actionable items.
- Result cursors remain current until replaced or the runtime restarts; they do not expire.

### Pull Surfacing

- `!recent` now supports optional category filtering in addition to the existing limit:
  - `!recent [number] [category]`
  - `!recent [category]`
- Accepted categories normalize singular/plural aliases onto canonical object types:
  - `admin`
  - `project` / `projects`
  - `person` / `people`
  - `idea` / `ideas`

### Matching and Retrieval

- Hybrid lexical + semantic retrieval is active with conservative defaults.
- Matching retrieval now uses the raw inbound message directly; the separate candidate-query LLM expansion step was removed.
- Semantic retrieval can use `matching.semantic_provider` + `matching.semantic_model`; when `semantic_provider` is omitted it defaults to `llm.provider`.
- Runtime now threads a dedicated embedding provider through startup semantic sync and command/mutation index refresh flows (separate from primary interpret provider).
- If semantic provider init/probe fails at startup, semantic matching is auto-disabled with warning and runtime falls back to lexical-only matching.
- Deterministic auto-apply gates include minimum score and margin checks.
- When retrieval is degraded/unavailable, runtime follows safe fallbacks (no unsafe auto-apply).

### LLM Config Validation

- `llm.provider` and `llm.model` are required config keys; startup fails fast when either is missing/empty.
- Legacy `llm.interpreter_model` fallback is removed.
- `matching.semantic_model` is required when `matching.semantic_weight > 0`; startup fails fast if missing/empty.
- `matching.semantic_provider` is optional; when provided it must be a non-empty string and is normalized to lowercase.

### Canonical Write Safety

- Canonical frontmatter writes now use YAML serialization (`yaml.safe_dump`) instead of manual `key: value` string assembly.
- The canonical writer normalizes designated free-text fields to strings before schema validation/write.
- Canonical frontmatter now round-trips through the no-dates YAML loader before write; writes fail fast if serialized YAML does not deserialize back to the intended mapping.

### Explicit Command Gate

- Any message beginning with `!` now stays in explicit-command mode only.
- Unknown `!` commands are rejected locally with a deterministic error and, when the fuzzy match is strong enough, a suggested valid command.
- Unknown `!` commands do not fall through to note capture or NL/LLM routing.

### Test-Mode Startup

- `SQUIRE_ENV=test` runs destructive reset + deterministic seed + index rebuild.
- Guardrails require a test-safe archive path.
- `test_archive_root` can override `archive_root` only in test mode.
- Test seed includes timed open admin notes, including one with `blocked_reason`, so reminder scheduling can be smoke-tested immediately.

### Due-Time Reminders

- Optional due-time reminders are implemented for `admin` items with `due_at` only.
- Eligibility is strict: `status=open`; `due_date`-only items are excluded.
- Scheduling model is event-driven + reconcile:
  - startup queue build
  - local-midnight queue rebuild
  - event-driven queue rebuild after canonical write paths (`!done`/`!append`/`!fix`, confirm/apply flows, mutation buttons)
  - periodic full reconcile (`schedule.due_time_reminder_reconcile_minutes`, default `60`)
- Runtime sleeps until next fire time (no per-minute scan loop) and uses dedupe persistence across restarts via:
  - `paths.events_derived/runtime/due_time_reminder_sent_ledger_v1.json`
- If `schedule.due_time_reminder_offsets_minutes` is omitted, runtime defaults to offsets `(90, 15)`; set explicit `[]` to disable reminders.

## Natural-Language Routing (Current Contract)

- Non-command messages now use a single message-triage call (`message_triage_v1`) for NL command routing plus capture classification.
- Runtime uses triage schema `message_triage_v1` and mutation schemas `nl_mutation_plan_v1` / `nl_mutation_normalized_v1`.
- Default triage prompt is `config/prompts/message_triage_v1.txt`.
- Capture flow no longer uses a separate classify prompt/schema.

### Mutation Plan Behavior

- Mutation plans support multi-operation and multi-target requests.
- Runtime normalizes per target and assigns operation status:
  - `resolved`
  - `unresolved`
  - `cancelled_unresolved`
- Conflicting writes on the same target+field are marked `operation_conflict`.
- Pending actions are created from resolved operations only.
- Confirm/apply path supports mixed object-type batches (`pending.object_type = "mixed"`).
- NL `set_fields` normalization now supports time-only `admin.due_at` edits when the target note already has an anchored date:
  - existing `due_at` contributes its local date in the runtime timezone; the new time is interpreted in that runtime timezone
  - existing `due_date` is promoted to `due_at` using the supplied time and runtime timezone
  - time-only `due_at` edits still fail when no existing date anchor is present

### Clarification Policy

- One-turn clarification only.
- Clarification scope is immutable and limited to unresolved operations.
- Out-of-scope clarification replies are blocked with required policy copy.
- If unresolved operations remain after the one clarification turn, they are cancelled.

### NL Routing Config Surface

- Active keys:
  - `enabled`
  - `clarify_on_ambiguous`
  - `allow_nl_mutations`
  - `plan_trace_enabled`
  - `read_auto_min_confidence`
  - `mutation_confirm_min_confidence`
  - `max_recent_limit`
- Removed keys (do not reintroduce without explicit decision):
  - `mutation_plan_enabled`
  - `plan_auto_aliasing`

## Transport Architecture

- Shared runtime behavior lives under `src/squire_core/transport/*`; Discord SDK behavior is isolated under
  `src/squire_core/transport/discord/*`.
- The canonical runtime entrypoint is `python -m squire_core.runtime`.
- Shared command/routing contracts use `TransportMessageContext`.
- Runtime state is explicitly injected through `RuntimeStateStore`; no module-global fallback state remains.
- Pending interaction orchestration lives in `src/squire_core/transport/pending_interactions.py`; Discord views remain UI shells.
- Slack adapter/runtime behavior remains out of scope. Any Slack integration should start as separate follow-on work.
- Remaining Discord runtime cleanup is defined in `docs/discord-runtime-consistency-hardening-spec.md`.
- Transport-boundary rules are currently doc/review guidance, not a CI-enforced import gate.

## Known Constraints and Loose Ends

- Clarification context is in-memory runtime state and does not persist across process restarts.
- Plan-size guardrails (max operations per plan / max targets per operation) are intentionally deferred for now.
  - Track this in `.agent/future-plans.md` under routing hardening.
- Due-time reminder scheduler assumes single-process runtime ownership of queue/ledger writes.
- Canonical frontmatter parsing is still permissive at write time. Index rebuild is now tolerant of malformed canonical files and logs explicit warnings while skipping them, but malformed writes should still be prevented earlier in the write path as follow-on hardening.
- Daily digest and weekly review now share the `Admin without due dates` section for open admin items lacking due dates; blocked presentation is derived from `blocked_reason`.

## Canonical References

- Workflow rules: `AGENTS.md`
- Commands: `docs/commands.md`
- Configuration: `docs/configuration.md`
- Architecture/data model: `docs/architecture.md`, `docs/data-model.md`, `docs/modules.md`
- Surfacing/querying: `docs/surfacing.md`, `docs/querying.md`
- Active Discord runtime cleanup: `docs/discord-runtime-consistency-hardening-spec.md`
