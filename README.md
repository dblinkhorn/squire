![Squire logo](banner.png)

Squire is a self-hosted personal organizer you talk to through Discord.
You send quick notes in a DM, and Squire turns them into structured notes you can review, search, and update later.

## What Squire Does

- Captures notes from Discord DMs
- Organizes notes into four groups: tasks, projects, people, and ideas
- Sends daily and weekly review messages
- Supports configurable pre-due reminders for timed admin items (`due_at`)
- Lets you search and inspect notes with simple commands
- Allows you to make updates and edits to notes using natural language
- Uses conservative matching and confirmation gates to reduce accidental edits
- Stores your data on your own machine

For surfacing details (daily digest, weekly review, due-time reminders), see [`docs/surfacing.md`](docs/surfacing.md).

## How It Works

You send a message to your Squire Discord bot, and Squire saves the original message first.
It then uses AI to extract useful fields (title, status, due date, and similar details), writes the result as readable Markdown files in your archive folder, and updates a local SQLite index so search commands stay fast.
For update/append decisions, Squire ranks likely matches with hybrid lexical + semantic retrieval and applies deterministic safety gates before mutating existing notes.

## Discord Commands

Core commands:

- `!status` - show daily review now
- `!weekly` - show weekly review now
- `!recent [number] [category]` - list recent notes
- `!find <query>` - search notes
- `!show <number>` - open one result from your latest numbered list
- `!help [command]` - show available commands or detailed help for one command
- `!append <id|number> <text>` - add text to a note (`<number>` uses your latest numbered list)
- `!done <id|number>` - mark a task as done (`<number>` uses your latest numbered list)
- `!fix <id|number> <field=value> [field=value ...]` - edit note fields (`<number>` uses your latest numbered list)
- `!confirm <pending_id>` / `!cancel <pending_id>` - approve or cancel a suggested change
- `!clear-archive` then `DELETE` - clear archive data (keeps `.git`)

Optional prefixes when capturing:

- `admin:` (tasks/commitments)
- `project:`
- `person:`
- `idea:`

For complete command semantics, see [`docs/commands.md`](docs/commands.md).

## Run with Docker (Recommended)

### Docker Prerequisites

- Docker and Docker Compose
- A Discord bot token
- `OPENAI_API_KEY`

### Step 1: Create config and env files

```sh
cp config.yaml.example config.yaml
```

### Step 2: Set archive path in `config.yaml`

For containerized runs, use:

```yaml
archive_root: "/data/archive"
```

### Step 3: Add secrets to `.env`

```env
DISCORD_TOKEN=...
OPENAI_API_KEY=...
```

### Step 4: Start Squire

```sh
docker compose up -d --build
```

### Step 5: Check logs

```sh
docker compose logs -f squire-core
```

Data mounts used by compose:

- `./config.yaml` -> `/app/config.yaml` (read-only)
- `./config/` -> `/app/config/` (read-only)
- `./archive/` -> `/data/archive/` (your data)

## Monitoring Squire

Squire exposes a lightweight liveness endpoint:

- Route: `GET /health`
- Default bind: `0.0.0.0` (override with `HEALTH_HOST`)
- Default port: `8080` (override with `HEALTH_PORT`; set `HEALTH_PORT=0` to disable)
- Response: HTTP `200` with `{"status":"ok"}`

Quick check:

```sh
curl http://<host>:<health-port>/health
```

You can point any HTTP-capable monitoring tool at this endpoint.
Typical checks should expect status `200` and treat any non-`200` response (or connection failure) as unhealthy.

Common target examples:

- Repo default (`docker-compose.yml`): `http://squire-core:8080/health`
- Same Docker network: `http://<container-name>:<health-port>/health`
- From another machine/network: `http://<host-ip-or-dns>:<health-port>/health`

For deployment/runtime behavior, see [`docs/deployment.md`](docs/deployment.md).

## Logging Behavior

Runtime logs are split by severity:

- `INFO`/`WARNING` and below are emitted to `stdout`
- `ERROR` and above are emitted to `stderr`

## Run from Source

### Source Prerequisites

- Python 3.11+
- A Discord bot token
- `OPENAI_API_KEY`

### Step 1: Create and activate a virtual environment

```sh
python3 -m venv .venv
source .venv/bin/activate
```

### Step 2: Install Squire

```sh
pip install -e .
```

Install test/dev tools if needed:

```sh
pip install -e ".[dev]"
```

### Step 3: Create config and env files

```sh
cp config.yaml.example config.yaml
```

Set `.env`:

```env
DISCORD_TOKEN=...
OPENAI_API_KEY=...
```

### Step 4: Initialize data folders

```sh
make init
```

### Step 5: Run the bot

```sh
make run-bot
```

For deterministic smoke testing with a fresh seeded dataset on every startup:

```sh
make run-bot-test
```

This mode is destructive for the active test archive. Set `test_archive_root` in `config.yaml` (for example `/tmp/squire-test-archive`) to keep it separate from your normal `archive_root`.

## First-Time Use

Open a DM with your bot and send a simple note like `Call dentist next Tuesday`.
Then run `!status` to see your current daily view, and try `!find dentist` followed by `!show 1` to open a matching result.

## Configuration

Main config file: `config.yaml`

Common settings include AI model/prompt paths, data storage location (`archive_root`), daily and weekly schedule times, and search/result display limits.
Matching defaults also include hybrid lexical + semantic retrieval for safer update/append routing.
To disable semantic scoring and run lexical-only matching, set `matching.semantic_weight: 0`.

For full configuration reference, see [`docs/configuration.md`](docs/configuration.md).

Start with:

- `config.yaml.example`

## Tests

```sh
make test
```

If `pytest` is missing:

```sh
pip install -e ".[dev]"
```

## Docker Releases

This repo publishes Docker images from GitHub Actions when you push a SemVer tag (`vX.Y.Z`).

Example:

```sh
git tag v0.1.2
git push origin v0.1.2
```

On your host, update containers explicitly:

```sh
docker compose pull
docker compose up -d
```

## More Docs! More Docs! More Docs!

- [`docs/commands.md`](docs/commands.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/deployment.md`](docs/deployment.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/data-model.md`](docs/data-model.md)
- [`docs/modules.md`](docs/modules.md)
- [`docs/querying.md`](docs/querying.md)
- [`docs/surfacing.md`](docs/surfacing.md)
