# Deployment

## Target

Squire is designed to be self-hosted and run continuously on a small server or personal machine using Docker Compose.
The repository now includes a `Dockerfile` and a baseline `docker-compose.yml` suitable for common arm64 and x86 hosts.

## Services

The system runs as a single container named `squire-core`.

## Persistence

Durable data should be bind-mounted from the host:

- `./config.yaml` -> `/app/config.yaml` (read-only)
- `./config/` -> `/app/config/` (read-only prompt/schema files)
- `./archive/` -> `/data/archive/` (raw events, derived artifacts, canonical objects, SQLite index)

Inside the container, set `archive_root` to `"/data/archive"` in `config.yaml`.

## Startup

On startup, the bot:

1. Loads `.env` values (including `DISCORD_TOKEN` and `OPENAI_API_KEY`).
2. Initializes OpenTelemetry tracing when an OTLP endpoint is configured via standard `OTEL_*` environment variables. If tracing init/exporter setup fails, startup logs a warning and continues without tracing.
3. Validates and normalizes archive paths from `config.yaml`.
4. Validates required LLM config keys (`llm.provider`, `llm.model`), applies `matching.semantic_provider` (defaults to `llm.provider` when omitted), and validates `matching.semantic_model` when semantic matching is enabled (`matching.semantic_weight > 0`).
5. If `SQUIRE_ENV=test`, validates test-safe archive guardrails, clears archive contents (preserving `.git`), seeds deterministic canonical fixtures, and rebuilds the SQLite index.
   If `test_archive_root` is configured, test mode uses that root instead of `archive_root`.
6. Otherwise, rebuilds the SQLite index if it is missing.
7. If semantic matching is enabled and semantic provider initialization/probe succeeds, runs semantic index sync against the same SQLite database; otherwise semantic matching is auto-disabled and startup continues with lexical-only matching.
8. Starts a lightweight HTTP liveness endpoint at `GET /health` (defaults: `HEALTH_HOST=0.0.0.0`, `HEALTH_PORT=8080`; set port `0` to disable).
9. Connects to Discord and starts message handling and scheduled digest loops.

## Quick Start (Compose)

1. Create `config.yaml` from `config.yaml.example`.
2. Set `archive_root: "/data/archive"` in `config.yaml` and confirm required keys are present:
   - `llm.provider`
   - `llm.model`
   - `matching.semantic_provider` (optional; defaults to `llm.provider`)
   - `matching.semantic_model` (if `matching.semantic_weight > 0`)
3. Ensure `.env` contains:
   - `DISCORD_TOKEN=...`
   - `OPENAI_API_KEY=...`
   - Optional: `HEALTH_PORT=8080` (or your preferred port)
   - Optional tracing:
     - `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://<alloy-host>:4318/v1/traces`
     - `OTEL_SERVICE_NAME=squire-core`
     - `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic ...` or any headers required by your OTLP receiver
4. Start:

```sh
docker compose up -d --build
```

5. Check logs:

```sh
docker compose logs -f squire-core
```

6. Verify health endpoint:

```sh
curl http://<host>:<health-port>/health
```

This should return HTTP `200` with `{"status":"ok"}`.

## Health Monitoring

Squire can be monitored with any HTTP-capable monitoring tool.

Recommended check details:

- URL: `http://<target-host>:<health-port>/health`
- Expected status code: `200`
- Response body: `{"status":"ok"}`

Common target examples:

- Repo default (`docker-compose.yml`): `http://squire-core:8080/health`
- Same Docker network: `http://<container-name>:<health-port>/health`
- From another machine/network: `http://<host-ip-or-dns>:<health-port>/health`

## External Compose Repo Integration

If your Docker Compose files live in another repository, copy the `squire-core` service definition and set:

- `build.context` to the checked-out Squire repo path on the host, or
- use a prebuilt image tag if you publish one.

Keep the same bind mounts and environment variables.

### Using a prebuilt Docker Hub image

If you publish from this repo's `Docker Publish` workflow, use tags from Docker Hub instead of `build`.

Example:

```yaml
services:
  squire-core:
    image: docker.io/<dockerhub-username>/squire:v0.1.0
    container_name: squire-core
    restart: unless-stopped
    env_file:
      - .env
    environment:
      HEALTH_HOST: "0.0.0.0"
      HEALTH_PORT: "${HEALTH_PORT:-8080}"
    ports:
      - "${HEALTH_PORT:-8080}:${HEALTH_PORT:-8080}"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./config:/app/config:ro
      - ./archive:/data/archive
```

Prefer pinned tags (`vX.Y.Z`) for deployment stability. Use `latest` only if you explicitly want automatic upgrades.

## Initialization Helper

`make init` now works via `python -m squire_core.cli_init` and can bootstrap `config.yaml` paths and archive
directories. For Docker deployments, you can run initialization directly on the host before starting Compose:

```sh
python -m squire_core.cli_init --archive-root /absolute/path/to/archive --no-git
```
