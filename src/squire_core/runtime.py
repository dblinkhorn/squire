"""Transport-agnostic runtime entrypoint.

Current production transport wiring delegates to the Discord adapter runtime flow.
"""

from __future__ import annotations

from squire_core.transport.discord.flow import main as _run_discord_runtime


def main() -> None:
    _run_discord_runtime()


if __name__ == "__main__":
    main()
