# Configuration

## Environment Variables

Required or optional environment variables include `DISCORD_TOKEN`, `OPENAI_API_KEY`, and `GOOGLE_CALENDAR_CREDENTIALS` (optional).

## config.yaml

Configuration specifies enabled modules, confidence thresholds, schedules, reminder defaults, repo paths, index paths, and prompt file locations. Configuration drives composition and sets the confidence gates for automatic actions and query execution.

Query thresholds in `config.yaml`:

- querying.execute_threshold: execute queries when confidence is high.
- querying.confirm_threshold: execute but ask for confirmation when confidence is moderate.

## Prompt Files

Prompt files are stored under `config/prompts/` and referenced by path in `config.yaml`:

```yaml
llm:
  classify_prompt_path: "config/prompts/classify_v1.txt"
  interpreter_prompt_path: "config/prompts/extract_v1.txt"
```

To customize behavior, copy the default prompt files, edit them, and point `config.yaml` at your versions.

## Archive Storage

The archive root controls where durable artifacts are stored and is required. By default, `squire init` creates `~/squire-archive` and writes the derived paths into `config.yaml`. `archive_root` must be an absolute path (use `~/...` for home). You can disable git initialization with `archive_git_enabled: false` or `squire init --no-git`.

## config.yaml.example

Use `config.yaml.example` as a reference. For local development, copy it to `config.yaml` and edit as needed.

## Timezone

Squire uses the system local timezone by default. To override it (for example, when running in a container or on a remote host), set an IANA timezone name in `config.yaml`:

```yaml
timezone: "America/Los_Angeles"
```
