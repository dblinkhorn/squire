# Configuration

## Environment Variables

Required or optional environment variables include `DISCORD_TOKEN`, `OPENAI_API_KEY`, and `GOOGLE_CALENDAR_CREDENTIALS` (optional).

## config.yaml

Configuration specifies enabled modules, confidence thresholds, schedules, reminder defaults, repo paths, and index paths. Configuration drives composition and sets the confidence gates for automatic actions and query execution.

Query thresholds in `config.yaml`:

- querying.execute_threshold: execute queries when confidence is high.
- querying.confirm_threshold: execute but ask for confirmation when confidence is moderate.

## config.yaml.example

Use `config.yaml.example` as a reference. For local development, copy it to `config.yaml` and edit as needed.

## Timezone

Squire uses the system local timezone by default. To override it (for example, when running in a container or on a remote host), set an IANA timezone name in `config.yaml`:

```
timezone: "America/Los_Angeles"
```
