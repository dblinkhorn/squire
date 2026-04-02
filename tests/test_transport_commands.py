from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from squire_core.transport.commands import handle_command


@dataclass(frozen=True)
class _TargetResolution:
    target_id: str | None
    error: str | None = None
    reason: str | None = None
    row_number: int | None = None
    source_view: str | None = None


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Channel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _Message:
    def __init__(self, *, user_id: int = 1, channel_id: int = 2) -> None:
        self.author = _Author(user_id)
        self.channel = _Channel(channel_id)


def test_handle_command_help_uses_runtime_callbacks() -> None:
    calls: list[str] = []

    class _Runtime:
        schema_map = {}
        help_copy = "HELP_COPY"
        help_details = {"help": "HELP_DETAIL"}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def normalize_help_topic(self, value: str) -> str:
            return value

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            calls.append(f"swap:{remove_emoji}:{add_emoji}")

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            calls.append(f"send:{content}")

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!help",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert calls == ["swap:⏳:✅", "send:HELP_COPY"]


def test_handle_command_clear_archive_calls_confirmation_hook() -> None:
    calls: list[str] = []

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def start_archive_clear_confirmation(self, message) -> None:
            calls.append("start-confirmation")

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            calls.append(f"swap:{remove_emoji}:{add_emoji}")

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            calls.append(f"send:{content}")

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!clear-archive",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert calls[0] == "start-confirmation"
    assert calls[1] == "swap:⏳:❓"
    assert "Reply with `DELETE` within 2 minutes to confirm." in calls[2]


def test_handle_command_done_delegates_to_apply_operation() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def resolve_command_target(self, message, target_token: str) -> _TargetResolution:
            assert target_token == "2"
            return _TargetResolution(
                target_id="A_2",
                row_number=2,
                source_view="status",
            )

        async def apply_command_operation(
            self,
            message,
            raw_id: str,
            config,
            target_id: str,
            op: str,
            fields,
            *,
            validate_fix: bool = False,
            command_name=None,
            row_number=None,
            source_view=None,
        ) -> bool:
            captured["target_id"] = target_id
            captured["op"] = op
            captured["fields"] = fields
            captured["validate_fix"] = validate_fix
            captured["command_name"] = command_name
            captured["row_number"] = row_number
            captured["source_view"] = source_view
            return True

        def now_iso(self) -> str:
            return "2026-02-21T12:00:00+00:00"

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!done 2",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["target_id"] == "A_2"
    assert captured["op"] == "update"
    assert captured["command_name"] == "done"
    assert captured["row_number"] == 2
    assert captured["source_view"] == "status"
    assert captured["validate_fix"] is False
    fields = captured["fields"]
    assert isinstance(fields, dict)
    assert fields == {"status": "done", "done_at": "2026-02-21T12:00:00+00:00"}


def test_handle_command_detail_uses_object_dump_builder() -> None:
    captured: dict[str, object] = {}
    calls: list[str] = []

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def parse_positive_int(self, value: str) -> int | None:
            trimmed = value.strip()
            return int(trimmed) if trimmed.isdigit() else None

        def resolve_result_cursor(self, context, number: int) -> str | None:
            assert number == 1
            return "A_1"

        def build_item_object_dump(self, objects_root, object_id: str) -> str | None:
            captured["object_id"] = object_id
            return "```yaml\nid: A_1\n```"

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            calls.append(f"swap:{remove_emoji}:{add_emoji}")

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            calls.append(f"send:{content}")

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!detail 1",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["object_id"] == "A_1"
    assert calls == ["swap:⏳:✅", "send:```yaml\nid: A_1\n```"]


def test_handle_command_fix_without_updates_shows_fix_guidance() -> None:
    captured: dict[str, object] = {}
    calls: list[str] = []

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def resolve_command_target(self, context, target_token: str) -> _TargetResolution:
            assert target_token == "2"
            return _TargetResolution(
                target_id="A_2",
                row_number=2,
                source_view="recent",
            )

        def build_fix_guidance(self, objects_root, object_id: str, *, target_token: str) -> str | None:
            captured["object_id"] = object_id
            captured["target_token"] = target_token
            return "**Current fields:**\n```yaml\nstatus: open\n```"

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            calls.append(f"swap:{remove_emoji}:{add_emoji}")

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            calls.append(f"send:{content}")

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!fix 2",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["object_id"] == "A_2"
    assert captured["target_token"] == "2"
    assert calls == ["swap:⏳:✅", "send:**Current fields:**\n```yaml\nstatus: open\n```"]


def test_handle_command_recent_accepts_limit_and_category() -> None:
    captured: dict[str, object] = {}
    calls: list[str] = []

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def parse_positive_int(self, value: str) -> int | None:
            trimmed = value.strip()
            return int(trimmed) if trimmed.isdigit() else None

        def build_recent_list(self, objects_root, config, *, limit=None, object_type=None):
            captured["limit"] = limit
            captured["object_type"] = object_type
            return SimpleNamespace(lines=["1. Pay rent"], object_ids=["A_1"])

        def store_result_cursor(self, context, config, object_ids, *, source_view="unknown") -> None:
            captured["cursor_ids"] = list(object_ids)
            captured["source_view"] = source_view

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            calls.append(f"swap:{remove_emoji}:{add_emoji}")

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            calls.append(f"send:{content}")

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!recent 5 admin",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["limit"] == 5
    assert captured["object_type"] == "admin"
    assert captured["cursor_ids"] == ["A_1"]
    assert captured["source_view"] == "recent"
    assert calls == ["swap:⏳:✅", "send:Recent admin notes:\n1. Pay rent\n\ntip+recent"]


def test_handle_command_recent_accepts_category_only() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def parse_positive_int(self, value: str) -> int | None:
            trimmed = value.strip()
            return int(trimmed) if trimmed.isdigit() else None

        def build_recent_list(self, objects_root, config, *, limit=None, object_type=None):
            captured["limit"] = limit
            captured["object_type"] = object_type
            return SimpleNamespace(lines=["1. Person note"], object_ids=["P_1"])

        def store_result_cursor(self, context, config, object_ids, *, source_view="unknown") -> None:
            return None

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            return None

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            captured["response"] = content

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!recent person",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["limit"] is None
    assert captured["object_type"] == "people"
    assert str(captured["response"]).startswith("Recent people notes:\n1. Person note")


def test_handle_command_active_accepts_category_only() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def parse_positive_int(self, value: str) -> int | None:
            trimmed = value.strip()
            return int(trimmed) if trimmed.isdigit() else None

        def build_active_list(self, objects_root, config, *, limit=None, object_type=None):
            captured["limit"] = limit
            captured["object_type"] = object_type
            return SimpleNamespace(lines=["🧱 **Active projects**", "───────────────", "1. Repaint house"], object_ids=["PR_1"])

        def store_result_cursor(self, context, config, object_ids, *, source_view="unknown") -> None:
            captured["cursor_ids"] = list(object_ids)
            captured["source_view"] = source_view

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            return None

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            captured["response"] = content

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!active project",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["limit"] is None
    assert captured["object_type"] == "projects"
    assert captured["cursor_ids"] == ["PR_1"]
    assert captured["source_view"] == "active"
    assert str(captured["response"]).startswith("Active project notes:\n🧱 **Active projects**")


def test_handle_command_active_rejects_unknown_category() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def parse_positive_int(self, value: str) -> int | None:
            trimmed = value.strip()
            return int(trimmed) if trimmed.isdigit() else None

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            captured["reaction"] = (remove_emoji, add_emoji)

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            captured["response"] = content

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!active chores",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["reaction"] == ("⏳", "⚠️")
    assert captured["response"] == "Usage: !active [number] [category]"


def test_handle_command_recent_rejects_unknown_category() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        def parse_positive_int(self, value: str) -> int | None:
            trimmed = value.strip()
            return int(trimmed) if trimmed.isdigit() else None

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            captured["reaction"] = (remove_emoji, add_emoji)

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            captured["response"] = content

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!recent chores",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["reaction"] == ("⏳", "⚠️")
    assert captured["response"] == "Usage: !recent [number] [category]"


def test_handle_command_unknown_command_suggests_closest_match() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {
            "help": "",
            "status": "",
            "weekly": "",
            "recent": "",
            "find": "",
            "show": "",
            "append": "",
            "done": "",
            "fix": "",
            "confirm": "",
            "cancel": "",
            "clear-archive": "",
        }
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            captured["reaction"] = (remove_emoji, add_emoji)

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            captured["response"] = content

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!stats",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["reaction"] == ("⏳", "⚠️")
    assert captured["response"] == "Unknown command: !stats. Did you mean !status?\n\nRun !help for a list of commands."


def test_handle_command_unknown_command_without_close_match_shows_plain_error() -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {
            "help": "",
            "status": "",
            "weekly": "",
            "recent": "",
            "find": "",
            "show": "",
            "append": "",
            "done": "",
            "fix": "",
            "confirm": "",
            "cancel": "",
            "clear-archive": "",
        }
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def load_matching_config(self, config):
            return object()

        async def swap_reaction(self, message, remove_emoji: str, add_emoji: str) -> None:
            captured["reaction"] = (remove_emoji, add_emoji)

        async def send_response(self, message, content: str, *, thread_title=None, view=None) -> None:
            captured["response"] = content

    handled = asyncio.run(
        handle_command(
            runtime=_Runtime(),
            context=_Message(),
            content="!admin",
            raw_id="R_1",
            config={},
        )
    )

    assert handled is True
    assert captured["reaction"] == ("⏳", "⚠️")
    assert captured["response"] == "Unknown command: !admin.\n\nRun !help for a list of commands."
