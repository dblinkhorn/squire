# Due-Time Reminder Surfacing Spec (v1)

## Purpose

Define an implementation-ready contract for configurable pre-due reminders on `admin` items with `due_at`.

This feature complements existing surfacing:

- daily digest remains a broad morning overview
- due-time reminders provide focused, time-relative nudges before specific deadlines

This spec is written so a new agent session can implement it without prior project context.

## Product Requirements

1. Support multiple global reminder offsets (for example `120`, `15` minutes).
2. Keep reminder behavior deterministic and local-data driven (no LLM formatting or routing).
3. Avoid duplicate sends across process restarts.
4. Handle out-of-band edits safely (for example manual archive file edits) via periodic reconcile.
5. Keep existing daily digest and weekly review behavior unchanged.

## Non-Goals (v1)

1. Per-item custom reminder offsets.
2. Reminder actions/buttons in Discord messages.
3. Reminder support for non-`admin` object types.
4. Cross-process distributed locking (single process runtime assumed).

## Existing Runtime Invariants to Preserve

1. Timezone handling uses configured `timezone` and existing resolver behavior.
2. `admin` items with `status=done` or `archived=true` are not surfaced as active work.
3. Surfacing output remains deterministic and concise.
4. Feature must be non-breaking when not configured.

## Terminology

1. Reminder offset: minutes before `due_at` when a reminder should fire.
2. Reminder event: derived event `{object_id, due_at, offset_minutes, fire_at}`.
3. Sent ledger: persisted set of already sent reminder keys.
4. Reconcile: full schedule rebuild from canonical files, used as correctness backstop.

## Configuration Contract

Add these keys under `schedule`.

```yaml
schedule:
  daily_digest_time: "09:00"
  weekly_review_day: "SUN"
  weekly_review_time: "10:00"

  # New due-time reminder controls
  due_time_reminder_offsets_minutes: [90, 15]
  due_time_reminder_late_grace_minutes: 10
  due_time_reminder_reconcile_minutes: 60

  # Optional reminder destination override
  # due_time_reminder_channel_id: 123456789012345678
  # due_time_reminder_user_id: 123456789012345678
```

### Defaults

1. `due_time_reminder_offsets_minutes`: `[90, 15]` when the key is omitted.
2. `due_time_reminder_late_grace_minutes`: `10`.
3. `due_time_reminder_reconcile_minutes`: `60`.

### Validation Rules

1. `due_time_reminder_offsets_minutes` must parse to a list of positive integers.
2. Non-integer/invalid entries are ignored; resulting list is deduplicated and sorted descending.
3. If parsed offsets list is empty (for example explicit `[]`), the reminder subsystem is disabled.
4. `due_time_reminder_late_grace_minutes` must be integer `>= 0`; invalid value falls back to default.
5. `due_time_reminder_reconcile_minutes` must be integer `>= 1`; invalid value falls back to default.
6. Destination IDs use existing integer coercion semantics.

### Disable Behavior

Feature is disabled when `due_time_reminder_offsets_minutes` resolves to empty (typically explicit `[]`). When disabled:

1. No reminder queue task is started.
2. No reconcile task is started.
3. Existing daily/weekly scheduling remains unchanged.

## Reminder Eligibility

An item is reminder-eligible only if all are true:

1. Object type is `admin`.
2. `archived` is false.
3. `status` is one of `open` or `blocked`.
4. `due_at` exists and parses to timezone-aware datetime.

`due_date`-only items are excluded from due-time reminder scheduling.

## Derived Reminder Event Model

Each eligible object and each configured offset yields one candidate reminder event:

1. `object_id`: canonical object id.
2. `title`: reminder display title.
3. `due_at`: parsed due datetime in runtime timezone.
4. `offset_minutes`: configured offset.
5. `fire_at = due_at - timedelta(minutes=offset_minutes)`.

### Event Sort Order

Sort ascending by:

1. `fire_at`
2. `due_at`
3. `object_id`
4. `offset_minutes`

This guarantees deterministic ordering.

## Time Horizon and Build Strategy

### Horizon Definition

At build time `now`, include events where:

1. `fire_at >= now - late_grace`
2. `fire_at <= now + 36h`

Where `late_grace = timedelta(minutes=due_time_reminder_late_grace_minutes)`.

### Why 36h

Covers cross-day offsets and next-day reminders without waiting for next midnight cycle.

### Build Triggers

1. Startup build: run once in `on_ready` before reminder loop sleeps.
2. Midnight rebuild: run at local midnight daily.
3. Event-driven update: run after reminder-relevant canonical mutations.
4. Periodic reconcile: run every `due_time_reminder_reconcile_minutes`.

## Runtime Architecture

Use three async tasks managed by `SquireBot`:

1. Reminder dispatch loop: sleeps until next event or wake signal.
2. Midnight rebuild loop: signals schedule rebuild at local midnight.
3. Reconcile loop: signals schedule rebuild every configured interval.

A shared in-memory scheduler state stores:

1. Min-heap of pending reminder events.
2. Sent ledger set loaded from disk.
3. `asyncio.Event` to wake dispatch loop for schedule changes.
4. Last rebuild timestamp.

## Wake/Sleep Semantics

### Core Rule

Never poll every minute for scans. Sleep until next known event, with event-driven wake.

### Delay Calculation

Use:

```python
delay = max(0.0, (next_fire_at - now).total_seconds())
```

Do not use `timedelta.seconds`.

### Wait Primitive

Use an interruptible wait pattern:

```python
try:
    await asyncio.wait_for(schedule_changed_event.wait(), timeout=delay)
    schedule_changed_event.clear()  # woke due to schedule update
except asyncio.TimeoutError:
    pass  # woke because fire time arrived
```

## Dispatch Rules

At wake time `now`:

1. Pop all events with `fire_at <= now`.
2. For each event, evaluate in this order:
   - stale check
   - dedupe check
   - current eligibility recheck (status/archive/due_at may have changed)
3. Gather sendable events into one batched Discord message.
4. If send succeeds, mark each sent in ledger and persist.
5. If send fails, do not mark sent; they remain retryable while within grace.

### Stale Rule

An event is stale if:

```text
(now - fire_at) > late_grace
```

Stale events are skipped and logged; they are not sent.

### Eligibility Recheck at Dispatch

Before send, reload current object state and ensure:

1. object still exists
2. status still in `{open, blocked}`
3. archived still false
4. `due_at` unchanged for this key

If any fail, skip and log `ineligible`.

## Event-Driven Update Triggers (Required Hook Points)

After successful canonical writes that can affect reminders, call a single notifier helper, for example:

```python
_notify_due_time_reminder_schedule_changed()
```

Required call sites:

1. New object create path (capture flow applying `create`).
2. Update path (`fix`, NL `set_fields`, decision update).
3. Done path (`!done`, NL `mark_done`).
4. Confirm/apply pending action path (`!confirm` and button confirm).
5. Archive-clear flow should clear queue and ledger in-memory state.

Implementation note:

- Prefer one central post-write hook near existing canonical apply plumbing to avoid missing paths.

## Sent Ledger Persistence

### Storage Location

Persist to archive-derived runtime path:

1. Base: `paths.events_derived`
2. File: `runtime/due_time_reminder_sent_ledger_v1.json`

Example absolute path:

- `<archive_root>/events/derived/runtime/due_time_reminder_sent_ledger_v1.json`

### File Schema

```json
{
  "schema_version": 1,
  "updated_at": "2026-02-19T16:00:00+00:00",
  "entries": [
    {
      "key": "A_123|2026-02-20T15:00:00-08:00|120",
      "object_id": "A_123",
      "due_at": "2026-02-20T15:00:00-08:00",
      "offset_minutes": 120,
      "fire_at": "2026-02-20T13:00:00-08:00",
      "sent_at": "2026-02-20T13:00:04-08:00",
      "expires_at": "2026-02-22T13:00:04-08:00"
    }
  ]
}
```

### Dedupe Key

```text
{object_id}|{due_at_iso}|{offset_minutes}
```

Where `due_at_iso` is timezone-aware ISO string.

### Retention

1. Keep entries until `expires_at`.
2. Default retention: 48 hours from `sent_at`.
3. Prune on:
   - startup load
   - every reconcile
   - after successful send batch before flush

### Write Safety

Use atomic write:

1. write temp file in same directory
2. fsync/close
3. replace target file

If ledger read fails, log warning and continue with empty in-memory ledger.

## Destination Resolution

Reminder destination precedence:

1. `schedule.due_time_reminder_channel_id`
2. `schedule.due_time_reminder_user_id`
3. existing digest destination resolver fallback

If none resolve, skip send and log `due_time_reminder_skipped reason=no_channel`.

## Reminder Message Formatting

### Header

`⏰ **Upcoming due reminders** · <human date>`

### Line format

`• <title> - due <human datetime> (in <relative duration>)`

### Formatting Rules

1. Deterministic formatting only.
2. One message per dispatch cycle, batched lines.
3. Sort lines by `due_at`, then `object_id`, then `offset_minutes`.
4. Keep concise style compatible with digest formatting conventions.

## Logging Contract

Emit structured logs with consistent event names.

1. `due_time_reminder_schedule_built count=<n> horizon_hours=36 source=startup|midnight|event|reconcile`
2. `due_time_reminder_wake reason=timeout|schedule_changed`
3. `due_time_reminder_event_dispatched object_id=<id> due_at=<iso> offset=<m>`
4. `due_time_reminder_event_skipped reason=stale|duplicate|ineligible|missing_object`
5. `due_time_reminder_send_failed error=<message>`
6. `due_time_reminder_reconcile_completed count=<n>`
7. `due_time_reminder_ledger_load_failed error=<message>`
8. `due_time_reminder_ledger_flush_failed error=<message>`

## Failure Semantics

1. Send failure: do not mark sent; retry allowed while not stale.
2. Queue rebuild failure: log error; keep previous queue; retry next trigger.
3. Reconcile failure: log error and continue loops.
4. Unhandled exception in reminder task: log and keep bot running; task should be restarted in controlled manner if practical.

## Integration File Map (Expected)

Primary implementation files:

1. `src/squire_core/runtime.py`
   - schedule config parsing
   - task lifecycle
   - dispatch, rebuild, reconcile loops
   - destination resolution
   - notifier hook from mutation paths
2. `src/squire_core/surfacing.py`
   - reusable candidate extraction and reminder line formatting helpers
3. `tests/test_discord_schedule.py`
   - runtime loop and scheduling behavior
4. `tests/test_surfacing.py`
   - eligibility and rendering behavior
5. `docs/configuration.md`
   - new config keys and defaults
6. `config.yaml.example`
   - example reminder config block
7. `docs/surfacing.md`
   - feature behavior summary

## Required Test Matrix

### Eligibility and Build

1. open + due_at => eligible.
2. blocked + due_at => eligible.
3. done + due_at => ineligible.
4. archived + due_at => ineligible.
5. due_date-only => ineligible.
6. multiple offsets generate multiple events.

### Time Semantics

1. startup build includes events in `[now - grace, now + 36h]`.
2. cross-day case: due `00:30` next day with `120m` offset is included from prior evening startup.
3. stale skip when `now - fire_at > grace`.
4. non-stale send at exact boundary (`== grace`) is allowed.

### Dedupe and Persistence

1. same key not resent after restart.
2. due_at change produces new key and allows new reminder.
3. expired ledger entries are pruned.

### Event-Driven Updates

1. `!fix` due_at change triggers schedule rebuild signal.
2. `!done` triggers rebuild signal and suppresses pending reminders.
3. `!confirm` applied update triggers rebuild signal.

### Reconcile

1. out-of-band file edit to due_at is reflected after reconcile interval.
2. out-of-band archive/status change suppresses future sends after reconcile.

### Destination and Send

1. reminder-specific channel id overrides digest destination.
2. reminder-specific user id used when channel id absent.
3. fallback to digest destination works.
4. no destination logs skipped warning and does not crash.

## Acceptance Criteria

1. Daily digest and weekly review behavior are unchanged.
2. With offsets configured, reminders send at correct local-time offsets.
3. Multiple offsets per item are honored.
4. Duplicate sends are prevented across restarts.
5. Out-of-band edits are corrected by reconcile within configured interval.
6. Feature is fully disabled when offsets list is empty.
7. Implementation does not rely on minute-by-minute full scans.

## Rollout Plan

1. Ship with default offsets `[90, 15]` when the key is omitted; allow explicit `[]` to disable.
2. Update docs + config example in same change.
3. Validate with focused tests first, then full suite.
4. Enable in user config incrementally (for example `[120]`, then `[120, 15]`).

## Open Questions

None for v1. This spec fixes all currently identified behavior ambiguities.
