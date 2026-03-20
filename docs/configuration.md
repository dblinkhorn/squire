# Configuration

## Environment Variables

Required or optional environment variables include:

- `DISCORD_TOKEN` (required)
- `OPENAI_API_KEY` (required when `llm.provider: openai`)
- `HEALTH_HOST` (optional, default `0.0.0.0`)
- `HEALTH_PORT` (optional, default `8080`; set to `0` to disable the health server)
- `SQUIRE_ENV` (optional; set to `test` to enable startup reset+seed mode for smoke testing)
- `SQUIRE_TRANSPORT` (optional, default `discord`; selects runtime transport via `transport/runtime_registry.py`)
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` (optional; enable OTLP trace export)
- `OTEL_EXPORTER_OTLP_HEADERS` (optional; standard OTLP auth/metadata headers)
- `OTEL_EXPORTER_OTLP_PROTOCOL` (optional; standard OTLP protocol selection)
- `OTEL_SERVICE_NAME` (optional, default `squire-core`)
- `OTEL_RESOURCE_ATTRIBUTES` (optional; comma-separated OTEL resource attributes)
- `OTEL_SDK_DISABLED` (optional; set truthy to disable OTEL tracing even if endpoints are configured)

Tracing notes:

- Squire only enables tracing when an OTLP endpoint is configured and `OTEL_SDK_DISABLED` is not truthy.
- Tracing is best-effort. Exporter/init failures log a warning and runtime startup continues.
- Tracing configuration is environment-only in v1. There is no tracing block in `config.yaml`.

## config.yaml

Configuration specifies LLM behavior, confidence thresholds, daily/weekly digest schedule and destination settings, archive storage paths, and surfacing behavior.

LLM settings in `config.yaml`:

- `llm.provider` (required): single active LLM backend for runtime operations. Current allowed value: `openai`.
- `llm.model` (required): model name used for classify/extract/decision/candidate-query interpretation calls.
- `llm.classify_prompt_path`: classify prompt path.
- `llm.interpreter_prompt_path`: extraction prompt path.
- `llm.decision_prompt_path`: decision prompt path for update/append routing.
- `llm.candidate_query_prompt_path`: candidate-query prompt path for matching retrieval.
- `llm.nl_command_routing_prompt_path`: prompt path for natural-language command intent routing.

OpenAI transport timeout behavior:

- OpenAI HTTP timeout values are runtime defaults (not currently configurable via `config.yaml`).
- Current defaults: `45` seconds for interpret and embedding calls.
- Minimum enforced timeout: `10` seconds.

Confidence settings in `config.yaml`:

- `confidence.create_threshold`: minimum classification confidence required before creating/interpreting a note from a captured message.

Decision thresholds in `config.yaml` (update/append gating):

- decision.auto_apply_threshold: auto-apply when confidence meets or exceeds this threshold.
- decision.confirm_threshold: request confirmation when confidence is below auto-apply but at/above this value.
- decision.candidate_limit: maximum number of candidates to include for decisions.
- decision.candidate_score_threshold: minimum candidate score to include in the list.

Matching settings in `config.yaml` (hybrid lexical/semantic retrieval and deterministic gates):

- matching.lexical_weight / matching.recency_weight / matching.affinity_weight / matching.semantic_weight: component weights for fused candidate scoring.
  `matching.semantic_weight` ships at a conservative default of `0.15`.

- `matching.semantic_provider`: optional provider for semantic embeddings. When omitted, it defaults to `llm.provider`. Current implemented provider support is `openai`.
- `matching.semantic_model`: embedding model for semantic retrieval. This key is required when `matching.semantic_weight > 0`. Startup validates semantic provider initialization and probes embedding support for the selected semantic provider/model; if either step fails, semantic matching is auto-disabled with a warning and runtime falls back to lexical-only matching.
- matching.candidate_multiplier / matching.max_candidate_pool / matching.candidate_limit: pre-fusion recall depth and post-fusion shortlist size.
- matching.affinity_recent_ids_per_thread / matching.affinity_ttl_days / matching.affinity_max_boost: conversation-affinity memory window and max additive contribution.
- matching.auto_min_score / matching.auto_min_margin: deterministic auto-apply score and margin gates (in addition to decision confidence thresholds).
- matching.semantic_text_schema_version: embedding text composition version; changing this triggers a full semantic reindex.

Natural-language command routing settings in `config.yaml`:

- `nl_command_routing.enabled`: enable pre-capture NL routing for command-like DMs.
- `nl_command_routing.clarify_on_ambiguous`: ask a clarification question on medium-confidence ambiguous command intents.
- `nl_command_routing.allow_nl_mutations`: allow NL mutation intents (`done`/`append`/`fix`) with confirmation-first behavior.
- `nl_command_routing.plan_trace_enabled`: write normalized mutation trace artifacts under `events_derived`.
- `nl_command_routing.read_auto_min_confidence`: minimum confidence to auto-execute NL read intents.
- `nl_command_routing.mutation_confirm_min_confidence`: minimum confidence to open NL mutation confirmation flow.
- `nl_command_routing.max_recent_limit`: max `N` used when NL routing maps to `!recent N`.

## Prompt Files

Prompt files are stored under `config/prompts/` and referenced by path in `config.yaml`:

```yaml
llm:
  provider: "openai"
  model: "gpt-5-mini"
  classify_prompt_path: "config/prompts/classify_v1.txt"
  interpreter_prompt_path: "config/prompts/extract_v1.txt"
  decision_prompt_path: "config/prompts/decision_v1.txt"
  candidate_query_prompt_path: "config/prompts/candidate_query_v1.txt"
```

To customize behavior, copy the default prompt files, edit them, and point `config.yaml` at your versions.

## Archive Storage

The archive root controls where durable artifacts are stored and is required. By default, `squire init` creates `~/squire-archive` and writes the derived paths into `config.yaml`. `archive_root` must be an absolute path (use `~/...` for home). You can disable git initialization with `archive_git_enabled: false` or `squire init --no-git`.

For Docker Compose deployments, set `archive_root` to the in-container mount path (for example, `"/data/archive"`).

Optional test-mode override:

- `test_archive_root`: when `SQUIRE_ENV=test`, startup uses this root instead of `archive_root` before path normalization. Use this to keep a separate throwaway test archive.

Archive paths include:

- events_raw
- events_derived
- pending_actions
- objects_root
- index_db

Relative archive paths (including `index_db`) are resolved under `archive_root` and validated to stay inside it. The
SQLite index is derived and will be rebuilt automatically if missing on startup.

When `SQUIRE_ENV=test`, startup performs a destructive reset+seed cycle before normal startup indexing/sync. In this mode,
startup refuses to reset unless the active archive root is test-safe (under `/tmp` or containing a `squire-test` path segment).

## config.yaml.example

Use `config.yaml.example` as a reference. For local development, copy it to `config.yaml` and edit as needed.

## Timezone

Squire uses the system local timezone by default. To override it (for example, when running in a container or on a remote host), set an IANA timezone name in `config.yaml`:

```yaml
timezone: "America/Los_Angeles"
```

## Schedule Destinations

Daily digests and weekly reviews are sent to a Discord channel or user when configured.
The scheduler supports:

- `schedule.daily_digest_time`: local-time daily digest send time (`HH:MM`).
- `schedule.weekly_review_day`: weekly review day (`MON`..`SUN`).
- `schedule.weekly_review_time`: local-time weekly review send time (`HH:MM`).
- `schedule.due_time_reminder_offsets_minutes`: list of reminder offsets (minutes before `admin.due_at`), e.g. `[120, 15]`. If omitted, defaults to `[90, 15]`; set `[]` to disable due-time reminders.
- `schedule.due_time_reminder_late_grace_minutes`: skip stale reminders older than this many minutes past their fire time (default `10`).
- `schedule.due_time_reminder_reconcile_minutes`: full reminder schedule reconcile interval in minutes (default `60`).

Both schedules use one shared destination and one of the following optional destination keys:

- `schedule.daily_digest_channel_id`: Discord channel ID to post the digest.
- `schedule.daily_digest_user_id`: Discord user ID to DM the digest.
- `schedule.due_time_reminder_channel_id`: optional channel override for due-time reminders.
- `schedule.due_time_reminder_user_id`: optional user DM override for due-time reminders.

If neither destination is configured, the bot falls back to the last DM channel it received and logs a warning if no
channel is available.

## Surfacing Settings

Surfacing behavior is configured under `surfacing` in `config.yaml`.

- `surfacing.output.show_ids_daily_weekly`: include canonical IDs in daily/weekly digest output (default false).
- `surfacing.admin.due_soon_days`: include admin items due within this many days in the due-soon section.
- `surfacing.projects.stale_days`: threshold for stale project surfacing.
- `surfacing.projects.blocked_limit`: maximum blocked/stale projects shown in digest.
- `surfacing.ideas.weekly_review`: include (or omit) the weekly “Ideas updated recently” section.
- `surfacing.people.next_contact_days`: include people with `next_contact` due within this window.
- `surfacing.pull.default_recent_limit`: default row count for `!recent`.
- `surfacing.pull.default_find_limit`: default row count for `!find`.
- `surfacing.pull.cursor_ttl_minutes`: how long numbered result selections remain available for `!show <number>`.
