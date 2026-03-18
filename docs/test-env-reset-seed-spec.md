# Test Environment Reset+Seed Spec

## Problem

Manual smoke testing currently requires hand-curating enough canonical data to exercise command flows (`!status`, `!weekly`, `!find`, `!show`, `!done`, `!append`, `!fix`). This is slow, repetitive, and easy to drift across test runs.

## Goals

- Provide a fast, deterministic way to boot Squire with useful test data.
- Make smoke-test startup one-step for local development.
- Keep normal local/dev/prod startup behavior unchanged.
- Prevent accidental destructive resets outside test storage.

## Non-Goals

- Changing canonical schemas.
- Replacing integration/unit tests.
- Introducing long-lived fixture management for production archives.

## Scope

### In Scope

- `SQUIRE_ENV=test` startup mode in the bot process.
- Automatic archive reset + canonical seed population before normal startup indexing/sync.
- Safety checks that fail closed if archive root is not test-like.
- New Make target for convenience (`make run-bot-test`).

### Out of Scope

- `SQUIRE_ENV=dev` destructive behavior.
- Multi-profile fixture packs in v1.
- Runtime command to reseed while bot is already running.

## Proposed Behavior

### Environment Semantics

- `SQUIRE_ENV` unset (or any value except `test`): current startup path, no reset/seed.
- `SQUIRE_ENV=test`: run reset+seed cycle during startup before index/semantic sync.

### Safety Guardrails

When `SQUIRE_ENV=test`, startup must refuse destructive reset unless archive storage is clearly test-only.

Guardrail checks (all required):

- `archive_root` path resolves successfully.
- `archive_root` is absolute.
- `archive_root` matches test-safe policy:
  - under `/tmp`, or
  - path segment contains `squire-test`.

If checks fail, startup exits with a clear error and does not connect to Discord.

### Startup Sequence (Test Mode)

1. Load `.env` and config.
2. Normalize archive paths from `config.yaml`.
3. If `SQUIRE_ENV=test`, run:
   - reset archive contents (preserving `.git` if present),
   - write deterministic seed canonical objects,
   - rebuild SQLite index.
4. Continue existing startup:
   - ensure index exists,
   - semantic sync (if enabled),
   - start health server,
   - connect bot.

### Reset Semantics

- Reuse existing archive clear behavior (`_clear_archive_contents`) to remove all top-level archive entries except `.git`.
- Reset includes raw/derived events, pending actions, canonical objects, index DB, and semantic artifacts under archive root.

### Seed Dataset (v1)

Create a compact but representative fixture set across all object types using deterministic IDs prefixed with `TEST_`.

Required coverage:

- Admin:
  - overdue open,
  - due today open,
  - due soon open,
  - items without due dates, including open and blocked examples,
  - timed (`due_at`) open,
  - timed (`due_at`) overdue open,
  - timed (`due_at`) blocked,
  - blocked,
  - done (with `completed_at`).
- Projects:
  - blocked with `blocked_reason`,
  - stale `in_progress`,
  - completed.
- People:
  - overdue `next_contact`,
  - due today `next_contact`.
- Ideas:
  - active recent,
  - done recent.

Date strategy:

- Use relative dates from startup `now` to keep surfacing behavior stable over time.
- Include timezone-aware ISO datetimes where required (`created_at`, `updated_at`, `due_at`, `completed_at`).

### Makefile UX

Add:

- `run-bot-test`: exports `SQUIRE_ENV=test` and runs the bot entrypoint.

Keep:

- existing `run-bot` behavior unchanged (non-destructive).

## Logging

Emit structured info logs in test mode:

- `test_mode_startup_enabled archive_root=...`
- `test_mode_reset_completed removed_entries=...`
- `test_mode_seed_completed admin=... projects=... people=... ideas=...`
- `test_mode_rebuild_index_completed`

On failure:

- `test_mode_startup_failed reason=...` then exit.

## Failure Handling

- Any reset/seed failure should fail startup fast.
- Bot must not start with partially seeded state.
- Errors should be actionable (guardrail mismatch, schema validation failure, write/index failure).

## Implementation Notes

- Add a dedicated helper module for seed construction and writes (for example `src/squire_core/test_seed.py`) instead of embedding fixture literals in `runtime.py`.
- Reuse canonical write path (`write_canonical_object`) and schema validation to ensure seeded data remains contract-valid.
- Keep seed IDs deterministic so smoke scripts and docs can reference known items.

## Testing Plan

Add tests for:

- `SQUIRE_ENV=test` triggers reset+seed path.
- non-test env values skip reset+seed path.
- guardrail rejection for non-test archive paths.
- seed dataset writes expected object counts and valid required fields.
- index rebuild invoked after seeding.

## Acceptance Criteria

- Running `make run-bot-test` always starts with the same baseline test dataset.
- `!status` and `!weekly` show meaningful rows immediately after startup.
- Numbered mutation flows (`!done 1`, `!append 2 ...`, `!fix 3 ...`) are testable without manual setup.
- Startup refuses to reset archives that fail test-safe path checks.
- Normal `make run-bot` behavior is unchanged.

## Rollout

1. Ship as a dedicated PR containing:
   - startup mode wiring,
   - seed helper module,
   - Makefile target,
   - tests,
   - docs updates (`README.md`, `docs/configuration.md`, `docs/deployment.md`).
2. Validate locally with `make run-bot-test` and smoke command checklist.
