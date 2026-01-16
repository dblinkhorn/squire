# Configuration

## Environment Variables

Required or optional environment variables include `DISCORD_TOKEN`, `LLM_API_KEY`, and `GOOGLE_CALENDAR_CREDENTIALS` (optional).

## config.yaml

Configuration specifies enabled modules, confidence thresholds, schedules, reminder defaults, repo paths, and index paths. Configuration drives composition and sets the confidence gates for automatic actions and query execution.

Query thresholds in `config.yaml`:

- querying.execute_threshold: execute queries when confidence is high.
- querying.confirm_threshold: execute but ask for confirmation when confidence is moderate.
