# Squire

![Squire logo](hero_logo.png)

Squire is a self-hosted, modular, AI-assisted personal organization and reminder system. It captures items frictionlessly (via a Discord bot DM), interprets them into structured entities, stores them durably in a git-backed archive, indexes them locally for fast queries, and proactively or interactively surfaces relevant information.

## Purpose

The system prioritizes durability, trust, low cognitive overhead, composability, and open-source deployability. Git is the durable archive and source of truth, while SQLite is a rebuildable derived index. AI output is never authoritative without auditability. Surfacing is the primary product rather than storage, and all automation is explainable and repairable.

Squire organizes information into four buckets: people, projects, ideas, and admin. The goal is to feel like jotting a quick note, while still producing durable, structured records you can query and maintain over time.

## Canonical Objects

Canonical objects are the current truthy records used for querying, surfacing, and maintenance. They are the only mutable artifacts; everything else is immutable and versioned.

## How It Works (High-Level)

When you send a message to the Squire bot, it is stored as an immutable raw event. The LLM interprets the message and proposes structured changes. The store validates those proposals and updates canonical objects (markdown files with YAML frontmatter). A derived SQLite index is rebuilt from the canonical files for fast search, and surfacing uses that index to send digests and respond to commands.

LLM output is never authoritative. If confidence is low or a payload is malformed, Squire asks for clarification instead of guessing.

## Buckets At A Glance

People track relationships and follow-ups. Projects track ongoing work with state and next actions. Ideas capture insights with a one-line summary and optional next steps. Admin items are tasks and commitments you want to complete, including calendarable items.

## Development

Python dependencies are declared in `pyproject.toml`. Install them with your preferred tool (e.g., `pip install -e .`).

Prompt templates live in `config/prompts/`. To override them, create your own prompt files and update the paths in `config.yaml`.

## Initialize archive storage

Run the init helper to set up a durable archive folder and update `config.yaml` paths:

```sh
make init
```

Defaults to `~/squire-archive`. Override with `--archive-root` or disable git initialization with `--no-git` (or set `archive_git_enabled: false` in `config.yaml`).

## Docker Compose

This repository includes a baseline `Dockerfile` and `docker-compose.yml` for self-hosted deployment.

Quick start:

1. Copy `config.yaml.example` to `config.yaml`.
2. Set `archive_root: "/data/archive"` in `config.yaml`.
3. Add required secrets to `.env` (`DISCORD_TOKEN`, `OPENAI_API_KEY`).
4. Run:

```sh
docker compose up -d --build
```

See `docs/deployment.md` for homelab integration details.
