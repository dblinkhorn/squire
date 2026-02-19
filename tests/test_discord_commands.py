from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from squire_core import discord_bot


class _Author:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class _Channel:
    def __init__(self, channel_id: int, *, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _Message:
    def __init__(
        self,
        content: str = "",
        *,
        user_id: int = 1,
        channel_id: int = 2,
        parent_id: int | None = None,
        bot: bool = False,
    ) -> None:
        self.author = _Author(user_id, bot=bot)
        self.channel = _Channel(channel_id, parent_id=parent_id)
        self.content = content


def _button_labels(view) -> list[str]:
    labels: list[str] = []
    for child in view.children:
        label = getattr(child, "label", None)
        if isinstance(label, str):
            labels.append(label)
    return labels


def _nl_payload(
    *,
    route: str,
    intent: str,
    risk_tier: str,
    confidence: float,
    read_command: dict[str, object] | None = None,
    mutation_plan: dict[str, object] | None = None,
    clarification: dict[str, object] | None = None,
    ambiguities: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "route": route,
        "intent": intent,
        "risk_tier": risk_tier,
        "confidence": confidence,
        "ambiguities": ambiguities or [],
        "read_command": read_command,
        "mutation_plan": mutation_plan,
        "clarification": clarification,
    }


def test_format_apply_success_message_lists_multiple_titles(monkeypatch) -> None:
    def _fake_load_frontmatter(path):
        name = Path(path).name
        if name == "a.md":
            return {"title": "Call internet provider"}
        if name == "b.md":
            return {"title": "Book annual physical"}
        return {}

    monkeypatch.setattr(discord_bot, "load_frontmatter", _fake_load_frontmatter)
    message = discord_bot._format_apply_success_message(
        written_paths=[Path("/tmp/a.md"), Path("/tmp/b.md")],
    )
    assert message == '✅ Applied updates to 2 notes:\n- "Call internet provider"\n- "Book annual physical"'


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

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        captured["fields"] = fields
        captured["validate_fix"] = validate_fix
        captured["command_name"] = command_name
        captured["row_number"] = row_number
        captured["source_view"] = source_view
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


def test_handle_command_help_sends_help_message(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        captured["reaction"] = (remove_emoji, add_emoji)

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(_Message("!help"), "!help", "R_1", config))

    assert handled is True
    assert captured["reaction"] == ("⏳", "✅")
    assert captured["response"] == discord_bot._HELP_COPY
    assert "\n- `!status` - show daily digest" in str(captured["response"])


def test_handle_command_help_topic_sends_detail(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        captured["reaction"] = (remove_emoji, add_emoji)

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(_Message("!help done"), "!help done", "R_1", config))

    assert handled is True
    assert captured["reaction"] == ("⏳", "✅")
    assert captured["response"] == discord_bot._HELP_DETAILS["done"]
    assert "`!done <id|number>`" in str(captured["response"])
    assert "\n- " not in str(captured["response"])


def test_handle_command_help_unknown_topic_warns(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        captured["reaction"] = (remove_emoji, add_emoji)

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(_Message("!help nope"), "!help nope", "R_1", config))

    assert handled is True
    assert captured["reaction"] == ("⏳", "⚠️")
    assert captured["response"] == "Unknown command `nope`. Run `!help` for a command list."


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
    assert "!done <number>" in str(captured["response"])
    assert "!recent <number>" in str(captured["response"])
    assert "up to 50" in str(captured["response"])


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
    assert "!append <number> <text>" in str(captured["response"])


def test_handle_command_show_does_not_override_digest_id_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    def _fake_build_item_detail(objects_root, object_id, config):
        captured["show_ids_daily_weekly"] = config.get("surfacing", {}).get("output", {}).get("show_ids_daily_weekly")
        return "**Title:** Call dentist\n\n(ID: A_2)"

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
    assert "(ID: A_2)" in str(captured["response"])


def test_handle_command_status_stores_numbered_cursor(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    def _fake_build_daily_digest(objects_root, config):
        return discord_bot.DailyDigest(
            generated_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            sections=[
                discord_bot.DigestSection(
                    title="Admin due today",
                    lines=["Call dentist - due Mon Feb 16 (today)", "Pay rent - due Mon Feb 16 (today)"],
                    object_ids=["A_1", "A_2"],
                ),
                discord_bot.DigestSection(
                    title="Projects needing attention",
                    lines=["Blocked launch - blocked: Waiting on vendor"],
                    object_ids=["P_1"],
                ),
            ],
        )

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_daily_digest", _fake_build_daily_digest)

    message = _Message("!status", user_id=11, channel_id=22)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!status", "R_1", config))

    assert handled is True
    assert "\n1. Call dentist" in str(captured["response"])
    assert "\n   • due Mon Feb 16 (today)" in str(captured["response"])
    assert "\n2. Pay rent" in str(captured["response"])
    assert "\n3. Blocked launch" in str(captured["response"])
    assert "\n   • blocked: Waiting on vendor" in str(captured["response"])
    assert "!done <number>" in str(captured["response"])
    key = discord_bot._cursor_key(message)
    assert discord_bot._RESULT_CURSORS[key].object_ids == ["A_1", "A_2", "P_1"]


def test_handle_command_done_resolves_number_after_status(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        return None

    def _fake_build_daily_digest(objects_root, config):
        return discord_bot.DailyDigest(
            generated_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            sections=[
                discord_bot.DigestSection(
                    title="Admin due today",
                    lines=["Call dentist - due Mon Feb 16 (today)", "Pay rent - due Mon Feb 16 (today)"],
                    object_ids=["A_1", "A_2"],
                )
            ],
        )

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        return True

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_daily_digest", _fake_build_daily_digest)
    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!status", user_id=11, channel_id=22)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}

    handled_status = asyncio.run(discord_bot._handle_command(message, "!status", "R_1", config))
    handled_done = asyncio.run(discord_bot._handle_command(message, "!done 2", "R_2", config))

    assert handled_status is True
    assert handled_done is True
    assert captured["target_id"] == "A_2"
    assert captured["op"] == "update"


def test_handle_command_done_resolves_number_from_parent_cursor_when_in_thread(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        return None

    def _fake_build_daily_digest(objects_root, config):
        return discord_bot.DailyDigest(
            generated_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            sections=[
                discord_bot.DigestSection(
                    title="Admin due today",
                    lines=["Call dermatologist - due Mon Feb 16 (today)"],
                    object_ids=["A_1"],
                )
            ],
        )

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        return True

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_daily_digest", _fake_build_daily_digest)
    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    parent_message = _Message("!status", user_id=11, channel_id=22)
    thread_message = _Message("!done 1", user_id=11, channel_id=999, parent_id=22)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}

    handled_status = asyncio.run(discord_bot._handle_command(parent_message, "!status", "R_1", config))
    handled_done = asyncio.run(discord_bot._handle_command(thread_message, "!done 1", "R_2", config))

    assert handled_status is True
    assert handled_done is True
    assert captured["target_id"] == "A_1"
    assert captured["op"] == "update"


def test_handle_command_weekly_stores_numbered_cursor(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        captured["response"] = content

    def _fake_build_weekly_review(objects_root, config):
        return discord_bot.WeeklyReview(
            generated_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            sections=[
                discord_bot.DigestSection(
                    title="Completed this week",
                    lines=["New unscheduled admin - admin, updated Mon Feb 16 (today)"],
                    object_ids=["A_10"],
                ),
                discord_bot.DigestSection(
                    title="People overdue for contact",
                    lines=["Alex - next contact Sun Feb 15 (yesterday)"],
                    object_ids=["P_20"],
                ),
            ],
        )

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "build_weekly_review", _fake_build_weekly_review)

    message = _Message("!weekly", user_id=11, channel_id=22)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!weekly", "R_1", config))

    assert handled is True
    assert "\n1. New unscheduled admin" in str(captured["response"])
    assert "\n   • admin" in str(captured["response"])
    assert "\n2. Alex" in str(captured["response"])
    assert "\n   • next contact Sun Feb 15 (yesterday)" in str(captured["response"])
    assert "!append <number> <text>" in str(captured["response"])
    key = discord_bot._cursor_key(message)
    assert discord_bot._RESULT_CURSORS[key].object_ids == ["A_10", "P_20"]


def test_handle_command_done_resolves_numbered_target(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        captured["fields"] = fields
        return True

    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!done 2", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_1", "A_2"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!done 2", "R_1", config))

    assert handled is True
    assert captured["target_id"] == "A_2"
    assert captured["op"] == "update"
    assert isinstance(captured["fields"], dict)
    assert captured["fields"]["status"] == "done"
    assert isinstance(captured["fields"]["completed_at"], str)


def test_handle_command_append_resolves_numbered_target(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        captured["fields"] = fields
        captured["command_name"] = command_name
        captured["row_number"] = row_number
        captured["source_view"] = source_view
        return True

    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!append 1 Added note", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_9"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        source_view="status",
    )

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!append 1 Added note", "R_1", config))

    assert handled is True
    assert captured["target_id"] == "A_9"
    assert captured["op"] == "append"
    assert captured["fields"] == {"body": "Added note"}
    assert captured["command_name"] == "append"
    assert captured["row_number"] == 1
    assert captured["source_view"] == "status"


def test_handle_command_fix_resolves_numbered_target(monkeypatch) -> None:
    captured: dict[str, object] = {}
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        captured["fields"] = fields
        captured["validate_fix"] = validate_fix
        captured["command_name"] = command_name
        captured["row_number"] = row_number
        captured["source_view"] = source_view
        return True

    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message('!fix 2 next_action="Call back"', user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_1", "A_2"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        source_view="find",
    )

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, '!fix 2 next_action="Call back"', "R_1", config))

    assert handled is True
    assert captured["target_id"] == "A_2"
    assert captured["op"] == "update"
    assert captured["fields"] == {"next_action": "Call back"}
    assert captured["validate_fix"] is True
    assert captured["command_name"] == "fix"
    assert captured["row_number"] == 2
    assert captured["source_view"] == "find"


def test_handle_command_done_number_without_cursor_shows_guidance(monkeypatch) -> None:
    calls: list[str] = []
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    async def _fake_apply_command_operation(*args, **kwargs):
        raise AssertionError("apply should not run when no numbered cursor exists")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!done 1", user_id=11, channel_id=22)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!done 1", "R_1", config))

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any("No active numbered list for that command." in call for call in calls)


def test_handle_command_done_number_expired_cursor_shows_guidance(monkeypatch, caplog) -> None:
    calls: list[str] = []
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    async def _fake_apply_command_operation(*args, **kwargs):
        raise AssertionError("apply should not run when cursor is expired")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!done 1", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_1"],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        source_view="recent",
    )

    caplog.set_level(logging.INFO)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!done 1", "R_1", config))

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any("Your last numbered list expired." in call for call in calls)
    assert any("numbered_mutation_resolution_failed" in rec.message and "reason=expired" in rec.message for rec in caplog.records)


def test_handle_command_done_number_out_of_range_shows_guidance(monkeypatch) -> None:
    calls: list[str] = []
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    async def _fake_apply_command_operation(*args, **kwargs):
        raise AssertionError("apply should not run when numbered target is out of range")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!done 2", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_1"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!done 2", "R_1", config))

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any("That number is out of range for your last list." in call for call in calls)


def test_handle_command_done_number_wrong_type_is_rejected_and_logged(monkeypatch, caplog) -> None:
    calls: list[str] = []
    discord_bot._RESULT_CURSORS.clear()

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    def _fake_find_object_path(objects_root, target_id):
        return Path(f"/tmp/projects/{target_id}.md")

    def _fake_load_frontmatter(path):
        return {"type": "projects", "title": "Website refresh"}

    def _fake_apply_operations(*args, **kwargs):
        raise AssertionError("apply_operations should not run for wrong-type !done")

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "find_object_path", _fake_find_object_path)
    monkeypatch.setattr(discord_bot, "load_frontmatter", _fake_load_frontmatter)
    monkeypatch.setattr(discord_bot, "apply_operations", _fake_apply_operations)

    message = _Message("!done 1", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["PR_1"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        source_view="weekly",
    )

    caplog.set_level(logging.INFO)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!done 1", "R_1", config))

    assert handled is True
    assert "swap:⏳:⚠️" in calls
    assert any("Only admin items can be marked done." in call for call in calls)
    assert any("numbered_mutation_resolution_failed" in rec.message and "reason=wrong_type" in rec.message for rec in caplog.records)


def test_handle_command_done_with_id_keeps_id_path(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_apply_command_operation(
        message,
        raw_id,
        config,
        target_id,
        op,
        fields,
        *,
        validate_fix=False,
        command_name=None,
        row_number=None,
        source_view=None,
    ):
        captured["target_id"] = target_id
        captured["op"] = op
        return True

    monkeypatch.setattr(discord_bot, "_apply_command_operation", _fake_apply_command_operation)

    message = _Message("!done A_1", user_id=11, channel_id=22)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!done A_1", "R_1", config))

    assert handled is True
    assert captured["target_id"] == "A_1"
    assert captured["op"] == "update"


def test_handle_command_append_number_logs_resolved(monkeypatch, caplog) -> None:
    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        return None

    async def _fake_send_response(message, content, thread_title=None, view=None):
        return None

    def _fake_find_object_path(objects_root, target_id):
        return Path(f"/tmp/admin/{target_id}.md")

    def _fake_load_frontmatter(path):
        return {"type": "admin", "title": "Call dermatologist"}

    def _fake_apply_operations(*args, **kwargs):
        return SimpleNamespace(written_paths=[Path("/tmp/admin/A_1.md")])

    async def _fake_refresh_index(objects_root, index_db, *, matching=None):
        return None

    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)
    monkeypatch.setattr(discord_bot, "find_object_path", _fake_find_object_path)
    monkeypatch.setattr(discord_bot, "load_frontmatter", _fake_load_frontmatter)
    monkeypatch.setattr(discord_bot, "apply_operations", _fake_apply_operations)
    monkeypatch.setattr(discord_bot, "_refresh_index_async", _fake_refresh_index)

    message = _Message("!append 1 update", user_id=11, channel_id=22)
    key = discord_bot._cursor_key(message)
    discord_bot._RESULT_CURSORS[key] = discord_bot._ResultCursor(
        object_ids=["A_1"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        source_view="status",
    )

    caplog.set_level(logging.INFO)
    config = {"paths": {"objects_root": "/tmp/objects", "index_db": "/tmp/index.sqlite"}}
    handled = asyncio.run(discord_bot._handle_command(message, "!append 1 update", "R_1", config))

    assert handled is True
    assert any("numbered_mutation_resolved" in rec.message and "command=append" in rec.message for rec in caplog.records)


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


def test_nl_route_executes_read_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="read_command",
                intent="recent",
                risk_tier="read",
                confidence=0.96,
                read_command={
                    "intent": "recent",
                    "args": {"recent_limit": 3},
                },
            ),
            raw_text="{}",
        )

    async def _fake_handle_command(message, content, raw_id, config):
        captured["command"] = content
        return True

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_handle_command", _fake_handle_command)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("show my last 3 notes", user_id=11, channel_id=22),
            content="show my last 3 notes",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert captured["command"] == "!recent 3"


def test_nl_route_overrides_show_my_notes_to_recent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="read_command",
                intent="show",
                risk_tier="read",
                confidence=0.95,
                read_command={
                    "intent": "show",
                    "args": {},
                },
            ),
            raw_text="{}",
        )

    async def _fake_handle_command(message, content, raw_id, config):
        captured["command"] = content
        return True

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_handle_command", _fake_handle_command)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("show me my notes", user_id=11, channel_id=22),
            content="show me my notes",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert captured["command"] == "!recent"


def test_nl_route_clarifies_ambiguous_read_intent(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="clarify",
                intent="find",
                risk_tier="read",
                confidence=0.72,
                clarification={
                    "question": "Did you mean search your notes?",
                    "options": ["Run `!find dentist`", "Show `!recent`", "Save as a note"],
                },
            ),
            raw_text="{}",
        )

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("show my dentist note", user_id=11, channel_id=22),
            content="show my dentist note",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert "swap:⏳:❓" in calls
    assert any("Did you mean search your notes?" in call for call in calls)
    assert any("Run `!find dentist`" in call for call in calls)


def test_nl_route_falls_through_on_low_confidence(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="read_command",
                intent="status",
                risk_tier="read",
                confidence=0.2,
                read_command={"intent": "status", "args": {}},
            ),
            raw_text="{}",
        )

    async def _fake_handle_command(message, content, raw_id, config):
        calls.append("handle")
        return True

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_handle_command", _fake_handle_command)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("status maybe", user_id=11, channel_id=22),
            content="status maybe",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is False
    assert calls == []


def test_nl_route_blocks_explicit_only_intent(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="blocked_explicit_only",
                intent="clear_archive",
                risk_tier="destructive",
                confidence=0.93,
            ),
            raw_text="{}",
        )

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("delete everything", user_id=11, channel_id=22),
            content="delete everything",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert "swap:⏳:❓" in calls
    assert any("!clear-archive" in call for call in calls)
    assert any("only be done explicitly" in call for call in calls)


def test_nl_route_queues_mutation_confirmation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="mutation_plan",
                intent="done",
                risk_tier="mutation",
                confidence=0.91,
                mutation_plan={
                    "schema_version": 1,
                    "operations": [
                        {
                            "operation_id": "op_1",
                            "action_type": "mark_done",
                            "target_refs": [{"kind": "row_number", "value": 2}],
                            "field_updates": [],
                            "append_text": None,
                            "raw_user_phrases": {},
                            "confidence": 0.91,
                            "requires_clarification": False,
                            "clarification_reason": None,
                        }
                    ],
                    "raw_user_phrases": {},
                    "confidence": 0.91,
                    "object_type_hint": None,
                    "requires_clarification": False,
                    "clarification_reason": None,
                },
            ),
            raw_text="{}",
        )

    async def _fake_queue_nl_mutation_confirmation(*, message, raw_id, config, plan_input, confidence, routing, source_view=None):
        operations = plan_input["operations"]
        captured["action_type"] = operations[0]["action_type"]
        captured["target_token"] = operations[0]["target_refs"][0]["target_token"]
        captured["confidence"] = confidence
        return True

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_queue_nl_mutation_confirmation", _fake_queue_nl_mutation_confirmation)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("mark item 2 done", user_id=11, channel_id=22),
            content="mark item 2 done",
            raw_id="R_1",
            config={},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert captured["action_type"] == "mark_done"
    assert captured["target_token"] == "2"
    assert captured["confidence"] == 0.91


def test_nl_route_blocks_mutation_when_disabled(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="mutation_plan",
                intent="append",
                risk_tier="mutation",
                confidence=0.9,
                mutation_plan={
                    "schema_version": 1,
                    "operations": [
                        {
                            "operation_id": "op_1",
                            "action_type": "append_body",
                            "target_refs": [{"kind": "object_id", "value": "A_1"}],
                            "field_updates": [],
                            "append_text": "Call back tomorrow",
                            "raw_user_phrases": {},
                            "confidence": 0.9,
                            "requires_clarification": False,
                            "clarification_reason": None,
                        }
                    ],
                    "raw_user_phrases": {},
                    "confidence": 0.9,
                    "object_type_hint": None,
                    "requires_clarification": False,
                    "clarification_reason": None,
                },
            ),
            raw_text="{}",
        )

    async def _fake_swap_reaction(message, remove_emoji, add_emoji):
        calls.append(f"swap:{remove_emoji}:{add_emoji}")

    async def _fake_send_response(message, content, thread_title=None, view=None):
        calls.append(f"send:{content}")

    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)
    monkeypatch.setattr(discord_bot, "_swap_reaction", _fake_swap_reaction)
    monkeypatch.setattr(discord_bot, "_send_response", _fake_send_response)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("append this to A_1", user_id=11, channel_id=22),
            content="append this to A_1",
            raw_id="R_1",
            config={"nl_command_routing": {"allow_nl_mutations": False}},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is True
    assert "swap:⏳:❓" in calls
    assert any("mutations are disabled" in call.lower() for call in calls)


def test_nl_route_uses_configured_v1_prompt_path(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_load_prompt(path):
        captured["prompt_path"] = path
        return "prompt"

    async def _fake_interpret_text_async(*args, **kwargs):
        return SimpleNamespace(
            derived=_nl_payload(
                route="capture_fallthrough",
                intent="none",
                risk_tier="none",
                confidence=0.1,
            ),
            raw_text="{}",
        )

    monkeypatch.setattr(discord_bot, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(discord_bot, "interpret_text_async", _fake_interpret_text_async)

    handled = asyncio.run(
        discord_bot._maybe_route_nl_command(
            message=_Message("noop", user_id=11, channel_id=22),
            content="noop",
            raw_id="R_1",
            config={"llm": {"nl_command_routing_prompt_path": "config/prompts/nl_command_routing_v1.txt"}},
            provider=object(),
            model="gpt-5-mini",
        )
    )

    assert handled is False
    assert captured["prompt_path"] == "config/prompts/nl_command_routing_v1.txt"
