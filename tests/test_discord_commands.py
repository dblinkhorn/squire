from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from squire_core import discord_bot


class _Author:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class _Channel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _Message:
    def __init__(self, content: str = "", *, user_id: int = 1, channel_id: int = 2, bot: bool = False) -> None:
        self.author = _Author(user_id, bot=bot)
        self.channel = _Channel(channel_id)
        self.content = content


def _button_labels(view) -> list[str]:
    labels: list[str] = []
    for child in view.children:
        label = getattr(child, "label", None)
        if isinstance(label, str):
            labels.append(label)
    return labels


def test_pending_action_view_shows_primary_buttons() -> None:
    async def _run() -> None:
        view = discord_bot.PendingActionView(
            pending_id="PA_1",
            pending_root="/tmp/pending",
            objects_root="/tmp/objects",
            index_db="/tmp/index.sqlite",
            schema_path=Path("config/schemas/derived_event_admin_v1.json"),
            author_id=1,
            candidates=[{"id": "A_1", "title": "Call dermatologist", "snippet": "Call dermatologist"}],
            default_target_id="A_1",
            matching=None,
            affinity_key=(1, 2),
        )
        labels = _button_labels(view)
        assert labels == ["Confirm", "Create New", "Cancel"]

    asyncio.run(_run())


def test_pending_action_view_shows_confirmation_buttons() -> None:
    async def _run() -> None:
        view = discord_bot.PendingActionView(
            pending_id="PA_1",
            pending_root="/tmp/pending",
            objects_root="/tmp/objects",
            index_db="/tmp/index.sqlite",
            schema_path=Path("config/schemas/derived_event_admin_v1.json"),
            author_id=1,
            candidates=[{"id": "A_1", "title": "Call dermatologist", "snippet": "Call dermatologist"}],
            default_target_id="A_1",
            matching=None,
            affinity_key=(1, 2),
            confirm_action="cancel",
        )
        labels = _button_labels(view)
        assert labels == ["Yes, cancel (do nothing)", "No, go back"]

    asyncio.run(_run())


def test_handle_command_confirm_refreshes_index(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    pending = discord_bot.PendingAction(
        schema_version=1,
        pending_action_id="PA_1",
        raw_event_id="R_1",
        object_type="admin",
        status="pending",
        created_at="2026-02-08T00:00:00+00:00",
        last_updated="2026-02-08T00:00:00+00:00",
        derived={"object_type": "admin", "proposed_operations": []},
    )

    def _fake_load_pending_action(root, pending_id):
        calls.append(f"load:{root}:{pending_id}")
        return pending

    def _fake_apply_operations(*args, **kwargs):
        calls.append("apply")
        return SimpleNamespace(written_paths=[Path("/tmp/admin/A_1.md")])

    def _fake_update_pending_action_status(root, pending_id, status):
        calls.append(f"status:{status}")
        return pending

    def _fake_refresh_index(objects_root, index_db, *, matching=None):
        calls.append(f"refresh:{objects_root}:{index_db}")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "load_pending_action", _fake_load_pending_action)
    monkeypatch.setattr(discord_bot, "apply_operations", _fake_apply_operations)
    monkeypatch.setattr(discord_bot, "update_pending_action_status", _fake_update_pending_action_status)
    monkeypatch.setattr(discord_bot, "_refresh_index", _fake_refresh_index)

    config = {
        "paths": {
            "objects_root": "/tmp/objects",
            "index_db": "/tmp/index.sqlite",
            "pending_actions": "/tmp/pending",
        }
    }

    handled = asyncio.run(discord_bot._handle_command(_Message("!confirm PA_1"), "!confirm PA_1", "R_1", config))

    assert handled is True
    assert "status:confirmed" in calls
    assert "refresh:/tmp/objects:/tmp/index.sqlite" in calls
    assert any(call.startswith("send:Applied pending action PA_1.") for call in calls)


def test_handle_command_fix_parses_quoted_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_apply_command_operation(message, raw_id, config, target_id, op, fields, *, validate_fix=False):
        captured["target_id"] = target_id
        captured["op"] = op
        captured["fields"] = fields
        captured["validate_fix"] = validate_fix
        return True

    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(
        discord_bot._handle_command(
            object(),
            '!fix A_1 next_action="Call dentist tomorrow at 4pm" priority=high',
            "R_1",
            config,
        )
    )

    assert handled is True
    assert captured["target_id"] == "A_1"
    assert captured["op"] == "update"
    assert captured["validate_fix"] is True
    assert captured["fields"] == {
        "next_action": "Call dentist tomorrow at 4pm",
        "priority": "high",
    }


def test_handle_command_recent_does_not_override_digest_id_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    def _fake_build_recent_list(objects_root, config, limit=None):
        captured["show_ids_daily_weekly"] = config.get("surfacing", {}).get("output", {}).get("show_ids_daily_weekly")
        return SimpleNamespace(lines=["1. Pay rent (A_1) - admin"], object_ids=["A_1"])

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_recent_list", _fake_build_recent_list)

    message = _Message("!recent", user_id=11, channel_id=22)
    config = {
        "surfacing": {"output": {"show_ids_daily_weekly": False}},
        "paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"},
    }
    handled = asyncio.run(discord_bot._handle_command(message, "!recent", "R_1", config))

    assert handled is True
    assert captured["show_ids_daily_weekly"] is False
    assert "Pay rent (A_1)" in str(captured["response"])


def test_handle_command_find_does_not_override_digest_id_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    def _fake_build_find_list(objects_root, index_db, config, query):
        captured["show_ids_daily_weekly"] = config.get("surfacing", {}).get("output", {}).get("show_ids_daily_weekly")
        return SimpleNamespace(lines=["1. Call dentist (A_2) - admin"], object_ids=["A_2"])

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_find_list", _fake_build_find_list)

    message = _Message("!find dentist", user_id=11, channel_id=22)
    config = {
        "surfacing": {"output": {"show_ids_daily_weekly": False}},
        "paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"},
    }
    handled = asyncio.run(discord_bot._handle_command(message, "!find dentist", "R_1", config))

    assert handled is True
    assert captured["show_ids_daily_weekly"] is False
    assert "Call dentist (A_2)" in str(captured["response"])


def test_handle_command_show_does_not_override_digest_id_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    def _fake_build_item_detail(objects_root, object_id, config):
        captured["show_ids_daily_weekly"] = config.get("surfacing", {}).get("output", {}).get("show_ids_daily_weekly")
        return "Title: Call dentist\nID: A_2"

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_item_detail", _fake_build_item_detail)

    message = _Message("!show 1", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_2"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    config = {
        "surfacing": {"output": {"show_ids_daily_weekly": False}},
        "paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"},
    }
    handled = asyncio.run(discord_bot._handle_command(message, "!show 1", "R_1", config))

    assert handled is True
    assert captured["show_ids_daily_weekly"] is False
    assert "ID: A_2" in str(captured["response"])


def test_apply_command_operation_rejects_disallowed_fix_field(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    def _fake_find_object_path(objects_root, target_id):
        return Path("/tmp/admin/A_1.md")

    def _fake_load_frontmatter(path):
        return {"type": "admin", "title": "Pay rent"}

    def _fake_apply_operations(*args, **kwargs):
        raise AssertionError("apply_operations should not be called for invalid !fix fields")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "find_object_path", _fake_find_object_path)
    monkeypatch.setattr(discord_bot, "load_frontmatter", _fake_load_frontmatter)
    monkeypatch.setattr(discord_bot, "apply_operations", _fake_apply_operations)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(
        discord_bot._apply_command_operation(
            object(),
            "R_1",
            config,
            target_id="A_1",
            op="update",
            fields={"foo": "bar"},
            validate_fix=True,
        )
    )

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any("Field `foo` is not allowed for admin." in call for call in calls)


def test_apply_command_operation_rejects_invalid_fix_enum(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    def _fake_find_object_path(objects_root, target_id):
        return Path("/tmp/admin/A_1.md")

    def _fake_load_frontmatter(path):
        return {"type": "admin", "title": "Pay rent"}

    def _fake_apply_operations(*args, **kwargs):
        raise AssertionError("apply_operations should not be called for invalid !fix values")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "find_object_path", _fake_find_object_path)
    monkeypatch.setattr(discord_bot, "load_frontmatter", _fake_load_frontmatter)
    monkeypatch.setattr(discord_bot, "apply_operations", _fake_apply_operations)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(
        discord_bot._apply_command_operation(
            object(),
            "R_1",
            config,
            target_id="A_1",
            op="update",
            fields={"priority": "urgent"},
            validate_fix=True,
        )
    )

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any("Invalid value for `priority`." in call for call in calls)


def test_handle_command_clear_archive_starts_confirmation(monkeypatch) -> None:
    calls: list[str] = []
    discord_bot._ARCHIVE_CLEAR_CONFIRMATIONS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    message = _Message("!clear-archive", user_id=100, channel_id=200)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!clear-archive", "R_1", config))

    assert handled is True
    assert discord_bot._archive_clear_key(message) in discord_bot._ARCHIVE_CLEAR_CONFIRMATIONS
    assert "swap:⏳:❓" in calls
    assert any("Reply with `DELETE`" in call for call in calls)


def test_handle_message_delete_clears_archive_when_pending(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    discord_bot._ARCHIVE_CLEAR_CONFIRMATIONS.clear()

    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / ".git").mkdir()
    (archive_root / "events").mkdir()
    (archive_root / "state.db").write_text("x", encoding="utf-8")

    async def _fake_safe_add_reaction(message, emoji):
        calls.append(f"react:{emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(discord_bot, "_safe_add_reaction", _fake_safe_add_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    message = _Message("DELETE", user_id=100, channel_id=200)
    discord_bot._start_archive_clear_confirmation(message)

    handled_config = {"archive_root": str(archive_root)}
    asyncio.run(discord_bot._handle_message(message, handled_config))

    assert (archive_root / ".git").exists()
    assert not (archive_root / "events").exists()
    assert not (archive_root / "state.db").exists()
    assert "react:✅" in calls
    assert any("Archive cleared. Removed 2 top-level entries" in call for call in calls)
    assert discord_bot._archive_clear_key(message) not in discord_bot._ARCHIVE_CLEAR_CONFIRMATIONS


def test_handle_message_delete_without_pending_shows_warning(monkeypatch) -> None:
    calls: list[str] = []
    discord_bot._ARCHIVE_CLEAR_CONFIRMATIONS.clear()

    async def _fake_safe_add_reaction(message, emoji):
        calls.append(f"react:{emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(discord_bot, "_safe_add_reaction", _fake_safe_add_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    message = _Message("DELETE", user_id=100, channel_id=200)
    asyncio.run(discord_bot._handle_message(message, {"archive_root": "/tmp/archive"}))

    assert "react:⚠️" in calls
    assert any("No pending archive clear request." in call for call in calls)
