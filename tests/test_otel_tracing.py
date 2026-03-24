from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from squire_core import telemetry
from squire_core.pending_actions import PendingAction, write_pending_action
from squire_core.transport import inbound, routing
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.discord import message_entry
from squire_core.transport.mutations import extract_ids_from_written_paths, extract_target_ids_from_derived
from squire_core.transport.discord.views import MutationPendingView, PendingActionView
from squire_core.transport.state import RuntimeStateStore


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    telemetry.reset_tracing_for_tests()
    yield
    telemetry.reset_tracing_for_tests()


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    telemetry.initialize_tracing(span_processors=[SimpleSpanProcessor(exporter)])
    return exporter


def _span_by_name(exporter: InMemorySpanExporter, name: str):
    for span in exporter.get_finished_spans():
        if span.name == name:
            return span
    raise AssertionError(f"missing span {name}")


def _span_names(exporter: InMemorySpanExporter) -> list[str]:
    return [span.name for span in exporter.get_finished_spans()]


def _context(*, content: str = "test") -> TransportMessageContext:
    return TransportMessageContext(
        source="discord",
        user_id="1",
        channel_id="2",
        thread_id=None,
        message_id="3",
        content=content,
        is_dm=True,
        created_at=datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
    )


class _Author:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class _Channel:
    def __init__(self, channel_id: int, *, parent_id: int | None = None) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _DiscordMessage:
    def __init__(self, content: str, *, user_id: int = 1, channel_id: int = 2) -> None:
        self.author = _Author(user_id)
        self.channel = _Channel(channel_id)
        self.content = content
        self.id = 999
        self.created_at = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)


class _InteractionResponse:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.edits: list[dict[str, Any]] = []
        self.deferred = False

    async def send_message(self, content: str) -> None:
        self.messages.append(content)

    async def edit_message(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)

    async def defer(self) -> None:
        self.deferred = True


class _InteractionMessage:
    def __init__(self, *, content: str = "Confirm action or cancel to take no action.") -> None:
        self.content = content
        self.edits: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)


class _Interaction:
    def __init__(self, *, user_id: int = 1, content: str = "Confirm action or cancel to take no action.") -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = _InteractionResponse()
        self.message = _InteractionMessage(content=content)


class _PendingRuntime:
    def __init__(self, *, written_paths: list[Path]) -> None:
        self.written_paths = written_paths
        self.refreshed: list[tuple[str | Path, str | Path, object | None]] = []

    def load_pending_action(self, root: str | Path, pending_id: str) -> PendingAction | None:
        from squire_core.pending_actions import load_pending_action

        return load_pending_action(root, pending_id)

    def write_pending_action(self, pending: PendingAction, root: str | Path) -> Path:
        from squire_core.pending_actions import write_pending_action

        return write_pending_action(pending, root)

    def apply_operations(
        self,
        derived: dict[str, Any],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ) -> Any:
        del derived, objects_root, canonical_schema_path, derived_schema_path, last_decision_id
        return SimpleNamespace(written_paths=self.written_paths)

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching: object | None = None,
    ) -> None:
        self.refreshed.append((objects_root, index_db, matching))

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        del clear_state

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        return extract_target_ids_from_derived(derived)

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        return extract_ids_from_written_paths(paths, load_frontmatter_fn=self.load_frontmatter)

    def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: object) -> None:
        del key, object_ids, matching

    def load_frontmatter(self, path: str | Path) -> dict[str, Any]:
        target = Path(path)
        if target.name == "A_1.md":
            return {"id": "A_1", "title": "Call dentist"}
        return {}


def test_initialize_tracing_is_noop_without_otlp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)

    assert telemetry.initialize_tracing() is False
    assert telemetry.tracing_enabled() is False


def test_initialize_tracing_uses_otlp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Exporter:
        def export(self, spans):  # pragma: no cover - exporter behavior is irrelevant here
            del spans
            return None

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            del timeout_millis
            return True

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "squire-test")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=dev")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda: _Exporter())

    assert telemetry.initialize_tracing() is True
    assert telemetry.tracing_enabled() is True
    assert telemetry._provider is not None
    assert telemetry._provider.resource.attributes["service.name"] == "squire-test"
    assert telemetry._provider.resource.attributes["deployment.environment"] == "dev"


def test_initialize_tracing_logs_warning_when_exporter_init_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    with caplog.at_level(logging.WARNING):
        assert telemetry.initialize_tracing() is False

    assert "tracing_init_failed" in caplog.text
    assert telemetry.tracing_enabled() is False


def test_discord_command_message_flow_emits_root_and_child_spans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_state: RuntimeStateStore,
    span_exporter: InMemorySpanExporter,
) -> None:
    class _Runtime:
        schema_map = {}
        help_copy = ""
        help_details = {}
        numbered_command_tip = "tip"
        numbered_command_tip_with_recent_limit = "tip+recent"

        def __init__(self) -> None:
            self.sent: list[str] = []

        def load_matching_config(self, config: dict[str, Any]) -> object:
            del config
            return object()

        def build_daily_digest(self, objects_root: str | Path, config: dict[str, Any]) -> object:
            del objects_root, config
            return object()

        def render_numbered_daily_digest_for_command(self, digest: object) -> tuple[str, list[str]]:
            del digest
            return ("Status digest", ["A_1"])

        def store_result_cursor(
            self,
            context: TransportMessageContext,
            config: dict[str, Any],
            object_ids: list[str],
            *,
            source_view: str = "unknown",
        ) -> None:
            del context, config, object_ids, source_view

        async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
            del context, remove_emoji, add_emoji

        async def send_response(
            self,
            context: TransportMessageContext,
            content: str,
            *,
            thread_title: str | None = None,
            view: Any = None,
        ) -> None:
            del context, thread_title, view
            self.sent.append(content)

    runtime = _Runtime()

    async def _fake_safe_add_reaction(message: Any, emoji: str) -> None:
        del message, emoji

    monkeypatch.setattr(message_entry, "_safe_add_reaction", _fake_safe_add_reaction)
    monkeypatch.setattr(message_entry, "_build_command_runtime", lambda *args, **kwargs: runtime)

    asyncio.run(
        message_entry.handle_message(
            _DiscordMessage("!status"),
            {"paths": {"events_raw": str(tmp_path / "events" / "raw")}},
            runtime_state=runtime_state,
        )
    )

    root = _span_by_name(span_exporter, "discord.message.command")
    assert root.attributes["squire.flow"] == "command"
    assert root.attributes["squire.outcome"] == "status_sent"
    assert root.attributes["squire.transport"] == "discord"
    assert root.attributes["squire.raw_id"].startswith("R_")
    assert "event.raw.write" in _span_names(span_exporter)
    assert "command.dispatch" in _span_names(span_exporter)
    assert "digest.build.status" in _span_names(span_exporter)
    assert "response.send" in _span_names(span_exporter)


def test_capture_flow_emits_expected_spans_for_create_path(
    tmp_path: Path,
    span_exporter: InMemorySpanExporter,
) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.triage_calls = 0
            self.interpret_calls = 0
            self.responses: list[str] = []

        async def triage_message(self, *, context, content, raw_id, config, provider, model) -> Any:
            del context
            del content, raw_id, config, provider, model
            self.triage_calls += 1
            with telemetry.start_span("llm.triage"):
                return SimpleNamespace(
                    handled=False,
                    triage=SimpleNamespace(
                        route="capture_fallthrough",
                        intent="none",
                        risk_tier="none",
                        confidence=0.92,
                        ambiguities=[],
                        read_command=None,
                        mutation_plan=None,
                        clarification=None,
                        capture=SimpleNamespace(object_type="admin", confidence=0.92),
                    ),
                )

        async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
            del context, remove_emoji, add_emoji

        async def send_response(
            self,
            context: TransportMessageContext,
            content: str,
            *,
            thread_title: str | None = None,
            view: Any = None,
        ) -> None:
            del context, thread_title, view
            self.responses.append(content)

        async def send_unrecognized_category(self, context: TransportMessageContext) -> None:
            del context
            raise AssertionError("unexpected")

        def load_prompt(self, path: str) -> str:
            return f"prompt:{path}"

        async def interpret_text_async(self, **kwargs: Any) -> Any:
            del kwargs
            self.interpret_calls += 1
            return SimpleNamespace(
                derived={
                    "object_type": "admin",
                    "confidence": 0.92,
                    "extracted_fields": {"title": "Call dentist"},
                    "proposed_operations": [{"op": "create", "fields": {"title": "Call dentist"}}],
                },
                raw_text="{}",
            )

        def now_iso(self) -> str:
            return "2026-03-19T12:00:00+00:00"

        def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
            del context
            return (1, 2)

        def load_affinity_scores(self, key: tuple[int, int], *, matching: Any) -> dict[str, float]:
            del key, matching
            return {}

        def write_matching_trace(self, *, derived_root: str | Path, raw_event_id: str, trace_payload: dict[str, Any]) -> None:
            del derived_root, raw_event_id, trace_payload
            raise AssertionError("unexpected")

        def apply_operations(self, derived: dict[str, Any], **kwargs: Any) -> Any:
            del derived, kwargs
            return SimpleNamespace(written_paths=[tmp_path / "objects" / "admin" / "A_1.md"])

        async def refresh_index_async(
            self,
            objects_root: str | Path,
            index_db: str | Path,
            *,
            matching: Any = None,
        ) -> None:
            del objects_root, index_db, matching

        def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
            del clear_state

        def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
            del derived
            return []

        def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
            del paths
            return ["A_1"]

        def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
            del key, object_ids, matching

        def author_id(self, context: TransportMessageContext) -> int:
            del context
            return 1

        def create_pending_action_view(self, **kwargs: Any) -> Any:
            del kwargs
            return None

        def format_pending_message(self, pending_id: str, decision_payload: dict[str, Any]) -> str:
            del pending_id, decision_payload
            return ""

        def create_auto_apply_feedback_view(self, *, author_id: int, target_id: str) -> Any:
            del author_id, target_id
            return None

    runtime = _Runtime()
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "message_triage_prompt_path": "config/prompts/message_triage_v1.txt",
            "interpreter_prompt_path": "config/prompts/extract_v1.txt",
        },
        "matching": {"semantic_weight": 0},
        "paths": {
            "events_derived": str(tmp_path / "events" / "derived"),
            "objects_root": str(tmp_path / "objects"),
            "index_db": str(tmp_path / "index.sqlite"),
        },
        "confidence": {"create_threshold": 0.6},
    }

    with telemetry.start_span("discord.message.capture", attributes={"squire.transport": "discord"}) as root_span:
        asyncio.run(
            inbound.handle_non_command_message(
                runtime=runtime,
                context=_context(content="Call dentist tomorrow"),
                content="Call dentist tomorrow",
                raw_id="R_CREATE",
                config=config,
                provider=object(),
                model="gpt-5-mini",
                schema_map={"admin": Path("config/schemas/derived_event_admin_v1.json")},
            )
        )

    assert root_span.is_recording() is False
    root = _span_by_name(span_exporter, "discord.message.capture")
    assert root.attributes["squire.outcome"] == "saved"
    assert root.attributes["squire.object_type"] == "admin"
    assert root.attributes["squire.capture_confidence"] == 0.92
    assert "prompt.load" in _span_names(span_exporter)
    assert "llm.triage" in _span_names(span_exporter)
    assert "llm.extract" in _span_names(span_exporter)
    assert "canonical.apply" in _span_names(span_exporter)
    assert "index.refresh" in _span_names(span_exporter)
    assert "response.send" in _span_names(span_exporter)


def test_capture_flow_low_confidence_stops_before_extract(
    tmp_path: Path,
    span_exporter: InMemorySpanExporter,
) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.triage_calls = 0
            self.interpret_calls = 0
            self.responses: list[str] = []

        async def triage_message(self, *, context, content, raw_id, config, provider, model) -> Any:
            del context
            del content, raw_id, config, provider, model
            self.triage_calls += 1
            with telemetry.start_span("llm.triage"):
                return SimpleNamespace(
                    handled=False,
                    triage=SimpleNamespace(
                        route="capture_fallthrough",
                        intent="none",
                        risk_tier="none",
                        confidence=0.1,
                        ambiguities=[],
                        read_command=None,
                        mutation_plan=None,
                        clarification=None,
                        capture=SimpleNamespace(object_type="unknown", confidence=0.1),
                    ),
                )

        async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
            del context, remove_emoji, add_emoji

        async def send_response(
            self,
            context: TransportMessageContext,
            content: str,
            *,
            thread_title: str | None = None,
            view: Any = None,
        ) -> None:
            del context, thread_title, view
            self.responses.append(content)

        async def send_unrecognized_category(self, context: TransportMessageContext) -> None:
            del context
            raise AssertionError("unexpected")

        def load_prompt(self, path: str) -> str:
            return f"prompt:{path}"

        async def interpret_text_async(self, **kwargs: Any) -> Any:
            del kwargs
            self.interpret_calls += 1
            raise AssertionError("unexpected")

        def now_iso(self) -> str:
            return "2026-03-19T12:00:00+00:00"

        def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
            del context
            return (1, 2)

        def load_affinity_scores(self, key: tuple[int, int], *, matching: Any) -> dict[str, float]:
            del key, matching
            return {}

        def write_matching_trace(self, *, derived_root: str | Path, raw_event_id: str, trace_payload: dict[str, Any]) -> None:
            del derived_root, raw_event_id, trace_payload
            raise AssertionError("unexpected")

        def apply_operations(self, **kwargs: Any) -> Any:
            raise AssertionError("unexpected")

        async def refresh_index_async(
            self,
            objects_root: str | Path,
            index_db: str | Path,
            *,
            matching: Any = None,
        ) -> None:
            del objects_root, index_db, matching
            raise AssertionError("unexpected")

        def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
            del clear_state
            raise AssertionError("unexpected")

        def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
            del derived
            return []

        def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
            del paths
            return []

        def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
            del key, object_ids, matching
            raise AssertionError("unexpected")

        def author_id(self, context: TransportMessageContext) -> int:
            del context
            return 1

        def create_pending_action_view(self, **kwargs: Any) -> Any:
            del kwargs
            return None

        def format_pending_message(self, pending_id: str, decision_payload: dict[str, Any]) -> str:
            del pending_id, decision_payload
            return ""

        def create_auto_apply_feedback_view(self, *, author_id: int, target_id: str) -> Any:
            del author_id, target_id
            return None

    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "message_triage_prompt_path": "config/prompts/message_triage_v1.txt",
            "interpreter_prompt_path": "config/prompts/extract_v1.txt",
        },
        "matching": {"semantic_weight": 0},
        "paths": {"events_derived": str(tmp_path / "events" / "derived")},
        "confidence": {"create_threshold": 0.6},
    }

    with telemetry.start_span("discord.message.capture", attributes={"squire.transport": "discord"}):
        asyncio.run(
            inbound.handle_non_command_message(
                runtime=_Runtime(),
                context=_context(content="unclear"),
                content="unclear",
                raw_id="R_LOW",
                config=config,
                provider=object(),
                model="gpt-5-mini",
                schema_map={"admin": Path("config/schemas/derived_event_admin_v1.json")},
            )
        )

    root = _span_by_name(span_exporter, "discord.message.capture")
    assert root.attributes["squire.outcome"] == "capture_low_confidence"
    assert "llm.triage" in _span_names(span_exporter)
    assert "llm.extract" not in _span_names(span_exporter)


def test_nl_read_route_emits_dispatch_spans(span_exporter: InMemorySpanExporter) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.executed_command: str | None = None

        def load_prompt(self, path: str) -> str:
            assert path == "config/prompts/message_triage_v1.txt"
            return "prompt"

        async def interpret_text_async(self, **kwargs: Any) -> Any:
            del kwargs
            return SimpleNamespace(
                derived={
                    "schema_version": 1,
                    "route": "read_command",
                    "intent": "recent",
                    "risk_tier": "read",
                    "confidence": 0.96,
                    "ambiguities": [],
                    "read_command": {
                        "intent": "recent",
                        "args": {"recent_limit": 3},
                    },
                    "mutation_plan": None,
                    "clarification": None,
                    "capture": {"object_type": "unknown", "confidence": 0.1},
                },
                raw_text="{}",
            )

        async def handle_command(
            self,
            context: TransportMessageContext,
            content: str,
            raw_id: str,
            config: dict[str, Any],
        ) -> bool:
            del context, raw_id, config
            self.executed_command = content
            return True

        async def queue_nl_mutation_confirmation(self, **kwargs: Any) -> bool:
            del kwargs
            raise AssertionError("unexpected")

        def load_nl_clarification_context(self, context: TransportMessageContext) -> Any:
            del context
            return None

        def clear_nl_clarification_context(self, context: TransportMessageContext) -> None:
            del context

        def store_nl_clarification_context(self, **kwargs: Any) -> None:
            del kwargs

        async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
            del context, remove_emoji, add_emoji

        async def send_response(
            self,
            context: TransportMessageContext,
            content: str,
            *,
            thread_title: str | None = None,
            view: Any = None,
        ) -> None:
            del context, content, thread_title, view

    runtime = _Runtime()
    with telemetry.start_span("discord.message.capture", attributes={"squire.transport": "discord"}):
        outcome = asyncio.run(
            routing.triage_message(
                runtime=runtime,
                context=_context(content="show my last 3 notes"),
                content="show my last 3 notes",
                raw_id="R_ROUTE_READ",
                config={},
                provider=object(),
                model="gpt-5-mini",
            )
        )

    assert outcome.handled is True
    assert runtime.executed_command == "!recent 3"
    root = _span_by_name(span_exporter, "discord.message.capture")
    assert root.attributes["squire.nl_route"] == "read_command"
    assert root.attributes["squire.intent"] == "recent"
    assert root.attributes["squire.risk_tier"] == "read"
    assert "llm.triage" in _span_names(span_exporter)
    assert "nl.clarification.load" in _span_names(span_exporter)
    assert "nl.read.dispatch" in _span_names(span_exporter)
    assert "nl.command.delegate" in _span_names(span_exporter)


def test_nl_mutation_pending_flow_emits_pending_spans(
    tmp_path: Path,
    span_exporter: InMemorySpanExporter,
) -> None:
    class _TargetResolution:
        def __init__(self) -> None:
            self.target_id = "A_2"
            self.error = None
            self.reason = None
            self.row_number = 2
            self.source_view = "recent"

    class _Runtime:
        def __init__(self) -> None:
            self.responses: list[str] = []
            self.pending_ids: list[str] = []

        def load_prompt(self, path: str) -> str:
            assert path == "config/prompts/message_triage_v1.txt"
            return "prompt"

        async def interpret_text_async(self, **kwargs: Any) -> Any:
            del kwargs
            return SimpleNamespace(
                derived={
                    "schema_version": 1,
                    "route": "mutation_plan",
                    "intent": "done",
                    "risk_tier": "mutation",
                    "confidence": 0.95,
                    "ambiguities": [],
                    "read_command": None,
                    "mutation_plan": {
                        "schema_version": 1,
                        "operations": [
                            {
                                "operation_id": "op_1",
                                "action_type": "mark_done",
                                "target_refs": [{"kind": "row_number", "value": 2}],
                                "field_updates": [],
                                "append_text": None,
                                "raw_user_phrases": {},
                                "confidence": 0.95,
                                "requires_clarification": False,
                                "clarification_reason": None,
                            }
                        ],
                        "raw_user_phrases": {},
                        "confidence": 0.95,
                        "object_type_hint": None,
                        "requires_clarification": False,
                        "clarification_reason": None,
                    },
                    "clarification": None,
                    "capture": {"object_type": "unknown", "confidence": 0.1},
                },
                raw_text="{}",
            )

        async def handle_command(
            self,
            context: TransportMessageContext,
            content: str,
            raw_id: str,
            config: dict[str, Any],
        ) -> bool:
            del context, content, raw_id, config
            raise AssertionError("unexpected")

        async def queue_nl_mutation_confirmation(self, **kwargs: Any) -> bool:
            return await routing.queue_nl_mutation_confirmation(runtime=self, **kwargs)

        def load_nl_clarification_context(self, context: TransportMessageContext) -> Any:
            del context
            return None

        def clear_nl_clarification_context(self, context: TransportMessageContext) -> None:
            del context

        def store_nl_clarification_context(self, **kwargs: Any) -> None:
            del kwargs

        async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
            del context, remove_emoji, add_emoji

        async def send_response(
            self,
            context: TransportMessageContext,
            content: str,
            *,
            thread_title: str | None = None,
            view: Any = None,
        ) -> None:
            del context, thread_title, view
            self.responses.append(content)

        def resolve_command_target(self, context: TransportMessageContext, target_token: str) -> _TargetResolution:
            del context
            assert target_token == "2"
            return _TargetResolution()

        def map_target_resolution_reason_to_plan_reason(self, reason: str | None) -> str:
            del reason
            return "target_missing"

        def log_numbered_mutation_resolution_failed(self, **kwargs: Any) -> None:
            del kwargs

        def log_numbered_mutation_resolved(self, **kwargs: Any) -> None:
            del kwargs

        def find_object_path(self, objects_root: str | Path, target_id: str) -> Path | None:
            del objects_root
            assert target_id == "A_2"
            return tmp_path / "objects" / "admin" / "A_2.md"

        def load_frontmatter(self, path: str | Path) -> dict[str, Any]:
            del path
            return {"type": "admin", "title": "Pay rent"}

        def write_nl_mutation_normalized_trace(self, **kwargs: Any) -> None:
            del kwargs

        def create_mutation_pending_view(self, **kwargs: Any) -> Any:
            pending_id = kwargs.get("pending_id")
            if isinstance(pending_id, str):
                self.pending_ids.append(pending_id)
            return object()

        def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
            del context
            return (1, 2)

        def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
            del clear_state

        def now_iso(self) -> str:
            return "2026-03-19T12:00:00+00:00"

    runtime = _Runtime()
    config = {
        "llm": {"provider": "openai", "model": "gpt-5-mini"},
        "paths": {
            "objects_root": str(tmp_path / "objects"),
            "index_db": str(tmp_path / "index.sqlite"),
            "pending_actions": str(tmp_path / "events" / "pending"),
        },
        "matching": {"semantic_weight": 0},
        "nl_command_routing": {"allow_nl_mutations": True, "plan_trace_enabled": False},
    }
    with telemetry.start_span("discord.message.capture", attributes={"squire.transport": "discord"}):
        outcome = asyncio.run(
            routing.triage_message(
                runtime=runtime,
                context=_context(content="mark 2 done"),
                content="mark 2 done",
                raw_id="R_ROUTE_MUTATION",
                config=config,
                provider=object(),
                model="gpt-5-mini",
            )
        )

    assert outcome.handled is True
    root = _span_by_name(span_exporter, "discord.message.capture")
    assert root.attributes["squire.outcome"] == "nl_pending_created"
    assert root.attributes["squire.pending_action_id"].startswith("PA_")
    assert "llm.triage" in _span_names(span_exporter)
    assert "nl.mutation.plan" in _span_names(span_exporter)
    assert "nl.pending.write" in _span_names(span_exporter)
    assert "response.send" in _span_names(span_exporter)


def test_pending_action_confirm_emits_interaction_spans(
    tmp_path: Path,
    span_exporter: InMemorySpanExporter,
) -> None:
    pending_root = tmp_path / "pending"
    objects_root = tmp_path / "objects"
    index_db = tmp_path / "index.sqlite"
    runtime = _PendingRuntime(written_paths=[objects_root / "admin" / "A_1.md"])
    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_CONFIRM",
        raw_event_id="R_1",
        object_type="admin",
        status="pending",
        created_at="2026-03-19T12:00:00+00:00",
        last_updated="2026-03-19T12:00:00+00:00",
        derived={"object_type": "admin", "proposed_operations": [{"op": "update", "target_id": "A_1", "fields": {"status": "done"}}]},
        decision={"confidence": 0.8},
        decision_confidence=0.8,
        last_decision_id="R_1/decision.json",
    )
    write_pending_action(pending, pending_root)

    async def _run() -> None:
        view = PendingActionView(
            runtime=runtime,
            pending_id="PA_CONFIRM",
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=index_db,
            schema_path=Path("config/schemas/derived_event_admin_v1.json"),
            author_id=1,
            candidates=[{"id": "A_1", "title": "Call dentist", "snippet": "Call dentist"}],
            default_target_id="A_1",
            matching=None,
            affinity_key=(1, 2),
            confirm_action="confirm",
        )
        confirm_button = next(child for child in view.children if getattr(child, "label", "") == "Yes, apply update")
        await confirm_button.callback(_Interaction(user_id=1))

    asyncio.run(_run())

    root = _span_by_name(span_exporter, "discord.interaction.pending.confirm")
    assert root.attributes["squire.pending_action_id"] == "PA_CONFIRM"
    assert root.attributes["squire.target_id"] == "A_1"
    assert root.attributes["squire.outcome"] == "confirmed"
    assert "pending.load" in _span_names(span_exporter)
    assert "canonical.apply" in _span_names(span_exporter)
    assert "index.refresh" in _span_names(span_exporter)
    assert "response.send" in _span_names(span_exporter)
    assert runtime.refreshed == [(objects_root, index_db, None)]


def test_mutation_pending_cancel_emits_interaction_spans(
    tmp_path: Path,
    span_exporter: InMemorySpanExporter,
) -> None:
    pending_root = tmp_path / "pending"
    pending = PendingAction(
        schema_version=1,
        pending_action_id="PA_CANCEL",
        raw_event_id="R_2",
        object_type="admin",
        status="pending",
        created_at="2026-03-19T12:00:00+00:00",
        last_updated="2026-03-19T12:00:00+00:00",
        derived={"object_type": "admin", "proposed_operations": []},
    )
    write_pending_action(pending, pending_root)

    async def _run() -> None:
        view = MutationPendingView(
            runtime=_PendingRuntime(written_paths=[]),
            pending_id="PA_CANCEL",
            pending_root=pending_root,
            objects_root=tmp_path / "objects",
            index_db=tmp_path / "index.sqlite",
            author_id=1,
            matching=None,
            affinity_key=(1, 2),
        )
        cancel_button = next(child for child in view.children if getattr(child, "label", "") == "Cancel")
        await cancel_button.callback(_Interaction(user_id=1))

    asyncio.run(_run())

    root = _span_by_name(span_exporter, "discord.interaction.nl_pending.cancel")
    assert root.attributes["squire.pending_action_id"] == "PA_CANCEL"
    assert root.attributes["squire.outcome"] == "cancelled"
    assert "pending.load" in _span_names(span_exporter)
    assert "response.send" in _span_names(span_exporter)
