# NL Routing Implementation Plan (Operation Plan Model)

Purpose:

- Provide an execution-ready plan for a new agent session with no prior context.
- Implement NL mutation handling via structured operation plans (not command-string conversion).

## 1) Starting Context

Assume current runtime:

- NL routing exists in `src/squire_core/discord_bot.py`.
- Read intents route to command handlers.
- Mutation intents currently convert to command-like structures and then validate.
- Explicit command flows (`!done`, `!append`, `!fix`, `!confirm`, `!cancel`, `!clear-archive`) are implemented and must remain stable.

Do not change:

- Explicit command syntax/behavior.
- Archive clear `!clear-archive` + `DELETE` safety.
- Pending-action core model (`events/pending` JSON shape) unless explicitly versioned.

## 2) Deliverables

Required deliverables:

1. New schemas:
   - `config/schemas/nl_route_intent_v2.json`
   - `config/schemas/nl_mutation_plan_v1.json`
   - `config/schemas/nl_mutation_normalized_v1.json`
2. New/updated prompt:
   - `config/prompts/nl_command_routing_v2.txt`
3. Runtime implementation in:
   - `src/squire_core/discord_bot.py`
   - optional helper extraction into `src/squire_core/nl_planner.py` (recommended)
4. Config plumbing:
   - `src/squire_core/config_utils.py`
   - `config.yaml.example`
   - `docs/configuration.md`
5. Test coverage:
   - route behavior tests
   - normalization tests
   - clarification/blocking tests
   - regression tests for explicit commands unchanged
6. Docs updates:
   - `docs/nl-command-routing-spec.md` (already updated)
   - `docs/commands.md`

## 3) High-Level Architecture

Implement two-stage NL handling:

1. Route stage:
   - decides `read_command | mutation_plan | clarify | capture_fallthrough | blocked_explicit_only`
2. Mutation-plan stage:
   - normalize + validate typed plan
   - create pending action with normalized derived operation
   - show confirm/cancel

Read intents should remain direct command execution.

## 4) Detailed Work Breakdown

### Step A: Config + Feature Flags

Files:

- `src/squire_core/config_utils.py`
- `config.yaml.example`
- `docs/configuration.md`

Tasks:

- Add config keys under `nl_command_routing`:
  - `mutation_plan_enabled` (default true)
  - `plan_auto_aliasing` (default true)
  - `plan_trace_enabled` (default true)
- Keep existing keys and defaults.

Acceptance check:

- New config keys load with defaults when missing.
- Invalid types clamp/fallback safely.

### Step B: Schema and Prompt Upgrade

Files:

- `config/schemas/nl_route_intent_v2.json`
- `config/schemas/nl_mutation_plan_v1.json`
- `config/schemas/nl_mutation_normalized_v1.json`
- `config/prompts/nl_command_routing_v2.txt`

Tasks:

- Create strict schemas with `additionalProperties=false`.
- Route schema includes nullable `read_command`, `mutation_plan`, `clarification`.
- Mutation plan schema includes action type, target ref, field updates, append text, confidence, ambiguity flags.
- Prompt must instruct:
  - do not output command strings for mutation operations
  - prefer semantic field intent (`due`, `priority`, `status`) over raw command args
  - output `clarify` when uncertain

Acceptance check:

- Schema validation catches malformed model output.
- Prompt examples include ambiguous and synonym-heavy cases.

### Step C: Route Interpreter Refactor

Files:

- `src/squire_core/discord_bot.py` (or `src/squire_core/nl_planner.py`)

Tasks:

- Replace/extend route parsing to consume `nl_route_intent_v2`.
- Keep explicit-only blocking for:
  - `clear_archive`
  - `confirm_pending`
  - `cancel_pending`
- Preserve read command behavior.

Acceptance check:

- Existing read route tests still pass.
- Explicit-only intents remain blocked from NL.

### Step D: Mutation Plan Normalization Layer

Files:

- `src/squire_core/discord_bot.py` or new helper module

Tasks:

- Implement deterministic normalizer:
  1. resolve target ID from `row_number|object_id`
  2. load target object type/frontmatter
  3. apply field alias map by object type
  4. normalize values:
     - date -> ISO date
     - datetime -> ISO datetime (+timezone when required)
     - enum -> canonical enum token
  5. validate against existing `_validate_fix_updates` and command-type rules
- On failure, return structured clarification reason.
- If `plan_trace_enabled`, write derived normalization artifact (`nl_mutation_normalized_v1`).

Minimum alias map v1:

- Admin:
  - `date`, `deadline`, `due` -> `due_date` (unless explicit time present)
  - `time` with date intent -> `due_at`
  - `name`, `task` -> `title`
- Projects:
  - `deadline`, `date` -> `due`

Acceptance check:

- `change number 2 date to feb 18` normalizes to `due_date=YYYY-MM-DD` for admin target.
- Unknown fields clarify instead of hard-failing with raw field name errors.

### Step E: Pending Action Construction from Normalized Plan

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- Build derived payload from normalized plan:
  - `proposed_operations` with canonical fields
- Write pending action.
- Reuse confirmation UI (`Confirm` / `Cancel`) with improved copy.

Acceptance check:

- No write occurs until confirm.
- Confirm applies normalized operation through existing apply + refresh path.

### Step F: Clarification Responses

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- Map normalization failure reasons to user-friendly clarification prompts.
- Include concrete options when possible.
- Keep concise, avoid internal terminology.

Reason mapping examples:

- `target_missing` -> ask for row number or rerun list
- `field_ambiguous` -> ask whether date vs datetime
- `value_parse_failed` -> ask for explicit date format

### Step G: Telemetry

Files:

- `src/squire_core/discord_bot.py`

Tasks:

- Emit:
  - `nl_plan_generated`
  - `nl_plan_normalized`
  - `nl_plan_clarified`
  - `nl_plan_blocked`
  - `nl_plan_pending_created`
  - `nl_plan_confirm_applied`
- Include reason codes and route outcome.

## 5) Test Plan (Required)

Add/extend tests in:

- `tests/test_discord_commands.py`
- `tests/test_nl_command_routing_config.py`
- new: `tests/test_nl_mutation_normalization.py`

Must-cover scenarios:

1. Read route unchanged:
   - `show me my notes` -> recent flow behavior.
2. Mutation plan success:
   - `mark 1 done` -> pending action created.
3. Alias normalization:
   - `change number 2 date to feb 18` -> due field normalized.
4. Value normalization:
   - date and datetime parsing paths.
5. Clarification path:
   - ambiguous field target.
6. Explicit-only blocking:
   - archive/confirm/cancel from NL.
7. Regression:
   - explicit `!fix` validation unchanged.

## 6) Validation Commands

Run at minimum:

1. `.venv/bin/python -m pytest -q tests/test_discord_commands.py tests/test_nl_command_routing_config.py`
2. `.venv/bin/python -m pytest -q tests/test_surfacing.py tests/test_discord_schedule.py`
3. `.venv/bin/python -m py_compile src/squire_core/discord_bot.py src/squire_core/config_utils.py`

If any tests fail, fix before handoff.

## 7) Rollout Strategy

Phase gate recommendations:

1. Ship read-route behavior unchanged.
2. Ship mutation plan engine behind `mutation_plan_enabled=true` default.
3. Keep temporary fallback path for one release window (optional).
4. Remove fallback after telemetry shows stable normalization/clarification rates.

## 8) Handoff Checklist

Before handoff, ensure:

- schemas and prompt committed
- config docs and example updated
- telemetry logs verified in local run
- tests passing for touched areas
- `.agent/context.md` updated with summary + test results

## 9) Out-of-Scope Follow-ups

- Auto-apply NL mutations without confirmation.
- Multi-target NL mutation in one utterance.
- Undo/revert transaction system.
