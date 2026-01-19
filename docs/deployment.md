# Deployment

## Target

Squire is designed to be self-hosted and run continuously on a small server or personal machine using Docker Compose.

## Services

The system runs as a single container named squire-core.

## Persistence

The git repo and the SQLite database use bind mounts for persistence.

## Startup

On startup, the system rebuilds the index if missing, validates configuration, and connects to the Discord gateway.

## Archive Backup (Planned)

GitHub backup for the archive repo is planned but not implemented yet. The intent is to optionally create a private GitHub repo and push the local archive as a remote backup.
