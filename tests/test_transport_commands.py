from __future__ import annotations

import asyncio
from dataclasses import dataclass

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
            message=_Message(),
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
            message=_Message(),
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
            message=_Message(),
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
    assert fields == {"status": "done", "completed_at": "2026-02-21T12:00:00+00:00"}
