"""Transport-agnostic runtime entrypoint."""

from __future__ import annotations

from squire_core.transport.runtime_registry import run_selected_transport


def main() -> None:
    run_selected_transport()


if __name__ == "__main__":
    main()
