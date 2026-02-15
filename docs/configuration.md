# Configuration

## Environment Variables

Required or optional environment variables include:

- `DISCORD_TOKEN` (required)
- `OPENAI_API_KEY` (required)
- `HEALTH_HOST` (optional, default `0.0.0.0`)
- `HEALTH_PORT` (optional, default `8080`; set to `0` to disable the health server)

## config.yaml

Configuration specifies LLM behavior, confidence thresholds, daily/weekly digest schedule and destination settings, archive storage paths, and surfacing behavior.

LLM settings in `config.yaml`:

- `llm.interpreter_model`: model name used for classify/extract/decision/candidate-query interpretation calls.
- `llm.classify_prompt_path`: classify prompt path.
- `llm.interpreter_prompt_path`: extraction prompt path.
- `llm.decision_prompt_path`: decision prompt path for update/append routing.
- `llm.candidate_query_prompt_path`: candidate-query prompt path for matching retrieval.

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
- matching.semantic_provider / matching.semantic_model: embedding provider/model for semantic retrieval (OpenAI-first rollout).
- matching.candidate_multiplier / matching.max_candidate_pool / matching.candidate_limit: pre-fusion recall depth and post-fusion shortlist size.
- matching.affinity_recent_ids_per_thread / matching.affinity_ttl_days / matching.affinity_max_boost: conversation-affinity memory window and max additive contribution.
- matching.auto_min_score / matching.auto_min_margin: deterministic auto-apply score and margin gates (in addition to decision confidence thresholds).
- matching.semantic_text_schema_version: embedding text composition version; changing this triggers a full semantic reindex.

## Prompt Files

Prompt files are stored under `config/prompts/` and referenced by path in `config.yaml`:

```yaml
llm:
  classify_prompt_path: "config/prompts/classify_v1.txt"
  interpreter_prompt_path: "config/prompts/extract_v1.txt"
  decision_prompt_path: "config/prompts/decision_v1.txt"
  candidate_query_prompt_path: "config/prompts/candidate_query_v1.txt"
```

To customize behavior, copy the default prompt files, edit them, and point `config.yaml` at your versions.

## Archive Storage

The archive root controls where durable artifacts are stored and is required. By default, `squire init` creates `~/squire-archive` and writes the derived paths into `config.yaml`. `archive_root` must be an absolute path (use `~/...` for home). You can disable git initialization with `archive_git_enabled: false` or `squire init --no-git`.

For Docker Compose deployments, set `archive_root` to the in-container mount path (for example, `"/data/archive"`).

Archive paths include:
- events_raw
- events_derived
- pending_actions
- objects_root
- index_db

Relative archive paths (including `index_db`) are resolved under `archive_root` and validated to stay inside it. The
SQLite index is derived and will be rebuilt automatically if missing on startup.

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

Both schedules use one shared destination and one of the following optional destination keys:

- `schedule.daily_digest_channel_id`: Discord channel ID to post the digest.
- `schedule.daily_digest_user_id`: Discord user ID to DM the digest.

If neither destination is configured, the bot falls back to the last DM channel it received and logs a warning if no
channel is available.

## Surfacing Settings

Surfacing behavior is configured under `surfacing` in `config.yaml`.

- `surfacing.output.show_ids_daily_weekly`: include canonical IDs in daily/weekly digest output (default false). Manual pull commands (`!recent`, `!find`, `!show`) always include IDs.
- `surfacing.admin.due_soon_days`: include admin items due within this many days in the due-soon section.
- `surfacing.projects.stale_days`: threshold for stale project surfacing.
- `surfacing.projects.blocked_limit`: maximum blocked/stale projects shown in digest.
- `surfacing.ideas.weekly_review`: include (or omit) the weekly “Ideas updated recently” section.
- `surfacing.people.next_contact_days`: include people with `next_contact` due within this window.
- `surfacing.pull.default_recent_limit`: default row count for `!recent`.
- `surfacing.pull.default_find_limit`: default row count for `!find`.
- `surfacing.pull.cursor_ttl_minutes`: how long numbered result selections remain available for `!show <number>`.
