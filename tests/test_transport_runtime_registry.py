from __future__ import annotations

import pytest

from squire_core.transport import runtime_registry


def test_resolve_transport_runtime_main_uses_default_transport(monkeypatch) -> None:
    monkeypatch.delenv("SQUIRE_TRANSPORT", raising=False)
    monkeypatch.setattr(runtime_registry, "TRANSPORT_RUNTIME_TARGETS", {"discord": "pkg.discord_runtime:main"})
    captured: dict[str, str] = {}

    def _fake_import_runtime_main(target: str):
        captured["target"] = target
        return lambda: None

    monkeypatch.setattr(runtime_registry, "_import_runtime_main", _fake_import_runtime_main)

    resolved = runtime_registry.resolve_transport_runtime_main()

    assert callable(resolved)
    assert captured["target"] == "pkg.discord_runtime:main"


def test_resolve_transport_runtime_main_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SQUIRE_TRANSPORT", "DISCORD")
    monkeypatch.setattr(runtime_registry, "TRANSPORT_RUNTIME_TARGETS", {"discord": "pkg.discord_runtime:main"})
    captured: dict[str, str] = {}

    def _fake_import_runtime_main(target: str):
        captured["target"] = target
        return lambda: None

    monkeypatch.setattr(runtime_registry, "_import_runtime_main", _fake_import_runtime_main)

    runtime_registry.resolve_transport_runtime_main()

    assert captured["target"] == "pkg.discord_runtime:main"


def test_resolve_transport_runtime_main_rejects_unknown_transport(monkeypatch) -> None:
    monkeypatch.setattr(runtime_registry, "TRANSPORT_RUNTIME_TARGETS", {"discord": "pkg.discord_runtime:main"})

    with pytest.raises(ValueError) as excinfo:
        runtime_registry.resolve_transport_runtime_main("unknown")

    assert "Unsupported transport" in str(excinfo.value)
    assert "discord" in str(excinfo.value)


def test_run_selected_transport_executes_entrypoint(monkeypatch) -> None:
    captured: list[str] = []

    def _fake_main() -> None:
        captured.append("called")

    monkeypatch.setattr(runtime_registry, "resolve_transport_runtime_main", lambda transport_name=None: _fake_main)

    runtime_registry.run_selected_transport()

    assert captured == ["called"]
