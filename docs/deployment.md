# Deployment

## Target

Squire is designed to be self-hosted and run continuously on a small server or personal machine using Docker Compose.
The repository now includes a `Dockerfile` and a baseline `docker-compose.yml` suitable for Raspberry Pi 4
(arm64) and x86 hosts.

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
2. Validates and normalizes archive paths from `config.yaml`.
3. Rebuilds the SQLite index if it is missing.
4. Connects to Discord and starts message handling and scheduled digest loops.

## Quick Start (Compose)

1. Create `config.yaml` from `config.yaml.example`.
2. Set `archive_root: "/data/archive"` in `config.yaml`.
3. Ensure `.env` contains:
   - `DISCORD_TOKEN=...`
   - `OPENAI_API_KEY=...`
4. Start:

```sh
docker compose up -d --build
```

5. Check logs:

```sh
docker compose logs -f squire-core
```

## Homelab Repo Integration

If your homelab Compose files live in another repository, copy the `squire-core` service definition and set:

- `build.context` to the checked-out Squire repo path on the host, or
- use a prebuilt image tag if you publish one.

Keep the same bind mounts and environment variables.

## Initialization Helper

`make init` now works via `python -m squire_core.cli_init` and can bootstrap `config.yaml` paths and archive
directories. For Docker deployments, you can run initialization directly on the host before starting Compose:

```sh
python -m squire_core.cli_init --archive-root /absolute/path/to/archive --no-git
```

## Archive Backup (Planned)

GitHub backup for the archive repo is planned but not implemented yet. The intent is to optionally create a private GitHub repo and push the local archive as a remote backup.
