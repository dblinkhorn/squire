"""Transport runtime selection and dynamic runtime loading."""

from __future__ import annotations

import importlib
import os
from typing import Callable

TRANSPORT_RUNTIME_TARGETS: dict[str, str] = {
    "discord": "squire_core.transport.discord.runtime:main",
}

_DEFAULT_TRANSPORT = "discord"


def _normalize_transport_name(value: str | None) -> str:
    if not isinstance(value, str):
        return _DEFAULT_TRANSPORT
    normalized = value.strip().lower()
    if not normalized:
        return _DEFAULT_TRANSPORT
    return normalized


def _import_runtime_main(target: str) -> Callable[[], None]:
    module_path, _, attr_name = target.partition(":")
    if not module_path or not attr_name:
        raise ValueError(f"Invalid transport runtime target: {target!r}")
    module = importlib.import_module(module_path)
    entrypoint = getattr(module, attr_name, None)
    if not callable(entrypoint):
        raise ValueError(f"Transport runtime entrypoint is not callable: {target!r}")
    return entrypoint


def resolve_transport_runtime_main(transport_name: str | None = None) -> Callable[[], None]:
    selected = _normalize_transport_name(transport_name or os.getenv("SQUIRE_TRANSPORT"))
    target = TRANSPORT_RUNTIME_TARGETS.get(selected)
    if target is None:
        supported = ", ".join(sorted(TRANSPORT_RUNTIME_TARGETS.keys()))
        raise ValueError(f"Unsupported transport: {selected!r}. Supported transports: {supported}")
    return _import_runtime_main(target)


def run_selected_transport(transport_name: str | None = None) -> None:
    runtime_main = resolve_transport_runtime_main(transport_name=transport_name)
    runtime_main()
