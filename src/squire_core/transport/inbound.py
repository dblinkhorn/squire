"""Shared inbound non-command capture/interpret/apply orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from squire_core import telemetry
from squire_core.config_utils import load_decision_config, load_matching_config
from squire_core.decision_flow import apply_decision_to_derived, evaluate_decision
from squire_core.derived_event_store import write_derived_event
from squire_core.id_utils import generate_prefixed_id
from squire_core.interpreter import InterpretationValidationError
from squire_core.llm.provider import AsyncLLMProvider, LLMProvider
from squire_core.pending_actions import PendingAction, write_pending_action
from squire_core.timezone_utils import (
    format_reference_date,
    format_reference_time,
    format_reference_weekday,
    resolve_timezone,
)
from squire_core.transport import matching_pipeline
from squire_core.transport.contracts import TransportMessageContext
from squire_core.transport.routing import MessageTriageOutcome


class InboundRuntime(Protocol):
    async def triage_message(
        self,
        *,
        context: TransportMessageContext,
        content: str,
        raw_id: str,
        config: dict[str, Any],
        provider: LLMProvider | AsyncLLMProvider,
        model: str,
    ) -> MessageTriageOutcome:
        ...

    async def swap_reaction(self, context: TransportMessageContext, remove_emoji: str, add_emoji: str) -> None:
        ...

    async def send_response(
        self,
        context: TransportMessageContext,
        content: str,
        *,
        thread_title: str | None = None,
        view: Any = None,
    ) -> None:
        ...

    async def send_unrecognized_category(self, context: TransportMessageContext) -> None:
        ...

    def load_prompt(self, path: str) -> str:
        ...

    async def interpret_text_async(
        self,
        *,
        provider: LLMProvider | AsyncLLMProvider,
        text: str,
        model: str,
        system_prompt: str,
        schema_path: Path,
    ) -> Any:
        ...

    def now_iso(self) -> str:
        ...

    def cursor_key(self, context: TransportMessageContext) -> tuple[int, int]:
        ...

    def load_affinity_scores(self, key: tuple[int, int], *, matching: Any) -> dict[str, float]:
        ...

    def write_matching_trace(self, *, derived_root: str | Path, raw_event_id: str, trace_payload: dict[str, Any]) -> None:
        ...

    def apply_operations(
        self,
        derived: dict[str, Any],
        *,
        objects_root: str | Path,
        canonical_schema_path: Path,
        derived_schema_path: Path | None,
        last_decision_id: str | None = None,
    ) -> Any:
        ...

    async def refresh_index_async(
        self,
        objects_root: str | Path,
        index_db: str | Path,
        *,
        matching: Any = None,
    ) -> None:
        ...

    def notify_due_time_reminder_schedule_changed(self, *, clear_state: bool = False) -> None:
        ...

    def extract_target_ids_from_derived(self, derived: dict[str, Any]) -> list[str]:
        ...

    def extract_ids_from_written_paths(self, paths: list[Path]) -> list[str]:
        ...

    def record_affinity_touches(self, key: tuple[int, int], object_ids: list[str], *, matching: Any) -> None:
        ...

    def author_id(self, context: TransportMessageContext) -> int:
        ...

    def create_pending_action_view(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        schema_path: Path | None,
        author_id: int,
        candidates: list[dict[str, Any]],
        default_target_id: str | None,
        matching: Any,
        affinity_key: tuple[int, int],
        config: dict[str, Any],
    ) -> Any:
        ...

    def format_pending_message(self, pending_id: str, decision_payload: dict[str, Any]) -> str:
        ...

    def create_auto_apply_feedback_view(self, *, author_id: int, target_id: str) -> Any:
        ...


async def _send_traced_response(
    runtime: InboundRuntime,
    context: TransportMessageContext,
    content: str,
    *,
    thread_title: str | None = None,
    view: Any = None,
) -> None:
    with telemetry.start_span("response.send"):
        await runtime.send_response(
            context,
            content,
            thread_title=thread_title,
            view=view,
        )


async def handle_non_command_message(
    *,
    runtime: InboundRuntime,
    context: TransportMessageContext,
    content: str,
    raw_id: str,
    config: dict[str, Any],
    provider: LLMProvider | AsyncLLMProvider,
    model: str,
    schema_map: dict[str, Path],
    embedding_provider: LLMProvider | AsyncLLMProvider | None = None,
) -> None:
    root_span = telemetry.current_span()
    telemetry.set_span_attribute("squire.raw_id", raw_id, span=root_span)
    try:
        triage_outcome = await runtime.triage_message(
            context=context,
            content=content,
            raw_id=raw_id,
            config=config,
            provider=provider,
            model=model,
        )
    except OSError as exc:
        telemetry.record_exception(exc, span=root_span)
        telemetry.set_span_attribute("squire.outcome", "prompt_load_failed", span=root_span)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await _send_traced_response(runtime, context, f"Failed to load prompt files: {exc}")
        return
    except InterpretationValidationError as exc:
        telemetry.record_exception(exc, span=root_span)
        telemetry.set_span_attribute("squire.outcome", "triage_invalid", span=root_span)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await _send_traced_response(runtime, context, "I couldn't parse that reliably. Please rephrase or use a prefix.")
        return
    except Exception as exc:
        telemetry.record_exception(exc, span=root_span)
        telemetry.set_span_attribute("squire.outcome", "triage_failed", span=root_span)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await _send_traced_response(runtime, context, "Interpretation failed. Please try again.")
        return

    if triage_outcome.handled:
        telemetry.set_span_attribute("squire.outcome", "routed", span=root_span)
        return
    triage = triage_outcome.triage

    derived_root = Path(config.get("paths", {}).get("events_derived", "events/derived"))

    extract_prompt_path = config.get("llm", {}).get("interpreter_prompt_path")
    decision_prompt_path = config.get("llm", {}).get("decision_prompt_path")
    if not extract_prompt_path:
        telemetry.set_span_attribute("squire.outcome", "prompt_missing", span=root_span)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await _send_traced_response(
            runtime,
            context,
            "Prompt paths are missing. Set llm.interpreter_prompt_path.",
        )
        return

    try:
        with telemetry.start_span("prompt.load"):
            extract_prompt = runtime.load_prompt(extract_prompt_path)
            decision_prompt = None
            if decision_prompt_path:
                try:
                    decision_prompt = runtime.load_prompt(decision_prompt_path)
                except OSError as exc:
                    logging.warning("decision_prompt_load_failed path=%s error=%s", decision_prompt_path, exc)
    except OSError as exc:
        telemetry.record_exception(exc, span=root_span)
        telemetry.set_span_attribute("squire.outcome", "prompt_load_failed", span=root_span)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await _send_traced_response(runtime, context, f"Failed to load prompt files: {exc}")
        return

    decision_config = load_decision_config(config) if decision_prompt else None
    matching_config = load_matching_config(config)
    tz_name = config.get("timezone")
    tz = resolve_timezone(tz_name)
    reference = (
        f"Reference date: {format_reference_date(tz)}. "
        f"Reference weekday: {format_reference_weekday(tz)}. "
        f"Reference time: {format_reference_time(tz)}."
    )
    base_capture_prompt = f"{extract_prompt} {reference}"

    object_type = triage.capture.object_type
    confidence = triage.capture.confidence
    telemetry.set_span_attributes(
        {
            "squire.object_type": object_type,
            "squire.capture_confidence": confidence,
        },
        span=root_span,
    )
    threshold = config.get("confidence", {}).get("create_threshold", 0.6)

    if not isinstance(object_type, str) or object_type == "unknown" or confidence < threshold:
        logging.info(
            "capture_low_confidence id=%s object_type=%s confidence=%.2f threshold=%.2f",
            raw_id,
            object_type,
            confidence,
            threshold,
        )
        telemetry.set_span_attribute("squire.outcome", "capture_low_confidence", span=root_span)
        await runtime.swap_reaction(context, "⏳", "❓")
        await _send_traced_response(
            runtime,
            context,
            "I couldn't confidently classify that. Please clarify or use a prefix (admin:, project:, idea:, person:).",
        )
        return

    schema_path = schema_map.get(object_type)
    if not schema_path:
        telemetry.set_span_attribute("squire.outcome", "unrecognized_category", span=root_span)
        with telemetry.start_span("response.send"):
            await runtime.send_unrecognized_category(context)
        return

    decision_payload: dict[str, Any] | None = None
    decision_artifact_id: str | None = None
    matching_trace: dict[str, Any] | None = None
    matching_candidates: list[Any] = []

    capture_prompt = base_capture_prompt
    capture_input = content
    fallback_to_plain_extract = False

    if decision_prompt and decision_config:
        affinity_key = runtime.cursor_key(context)
        affinity_scores = runtime.load_affinity_scores(affinity_key, matching=matching_config)
        with telemetry.start_span("matching.retrieval"):
            matching_context = await matching_pipeline.build_matching_context(
                embedding_provider=embedding_provider or provider,
                raw_event_id=raw_id,
                object_type=object_type,
                message=content,
                config=config,
                decision_config=decision_config,
                matching_config=matching_config,
                affinity_scores=affinity_scores,
                now_iso=runtime.now_iso(),
            )
        matching_candidates = matching_context.candidates
        matching_trace = matching_context.matching_trace
        capture_prompt = matching_pipeline.build_capture_prompt(
            extract_prompt=base_capture_prompt,
            decision_prompt=decision_prompt,
        )
        capture_input = matching_pipeline.build_capture_input(
            raw_event_id=raw_id,
            object_type=object_type,
            message=content,
            candidates=matching_candidates,
        )
        fallback_to_plain_extract = True
    elif decision_prompt:
        logging.warning("decision_config_missing id=%s decision_prompt_path=%s", raw_id, decision_prompt_path)

    fallback_capture_prompt = base_capture_prompt
    fallback_capture_input = content
    try:
        with telemetry.start_span("llm.extract") as extract_span:
            interpretation = await runtime.interpret_text_async(
                provider=provider,
                text=capture_input,
                model=model,
                system_prompt=capture_prompt,
                schema_path=schema_path,
            )
            interpretation.derived["raw_event_id"] = raw_id
            telemetry.set_span_attribute("squire.object_type", interpretation.derived.get("object_type"), span=extract_span)
    except InterpretationValidationError as exc:
        if fallback_to_plain_extract:
            logging.warning("capture_interpretation_invalid_fallback id=%s error=%s", raw_id, exc)
            write_derived_event(
                derived=exc.payload,
                raw_text=exc.raw_text,
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="decision_invalid",
                error=exc,
            )
            try:
                with telemetry.start_span("llm.extract_fallback") as extract_span:
                    interpretation = await runtime.interpret_text_async(
                        provider=provider,
                        text=fallback_capture_input,
                        model=model,
                        system_prompt=fallback_capture_prompt,
                        schema_path=schema_path,
                    )
                    interpretation.derived["raw_event_id"] = raw_id
                    telemetry.set_span_attribute(
                        "squire.object_type",
                        interpretation.derived.get("object_type"),
                        span=extract_span,
                    )
                matching_candidates = []
                decision_payload = None
                decision_artifact_id = None
                if matching_trace:
                    gate = matching_trace.get("gate")
                    if not isinstance(gate, dict):
                        gate = {}
                        matching_trace["gate"] = gate
                    gate["outcome"] = "create"
            except InterpretationValidationError as fallback_exc:
                telemetry.record_exception(fallback_exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "extract_invalid", span=root_span)
                write_derived_event(
                    derived=fallback_exc.payload,
                    raw_text=fallback_exc.raw_text,
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="invalid",
                    error=fallback_exc,
                )
                logging.warning("interpretation_invalid id=%s error=%s", raw_id, fallback_exc)
                await runtime.swap_reaction(context, "⏳", "⚠️")
                await _send_traced_response(runtime, context, "I couldn't parse that reliably. Please rephrase or use a prefix.")
                return
            except Exception as fallback_exc:
                telemetry.record_exception(fallback_exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "extract_failed", span=root_span)
                write_derived_event(
                    derived=None,
                    raw_text="",
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="invalid",
                    error=fallback_exc,
                )
                logging.exception("interpretation_failed id=%s", raw_id)
                await runtime.swap_reaction(context, "⏳", "⚠️")
                await _send_traced_response(runtime, context, "Interpretation failed. Please try again.")
                return
        else:
            telemetry.record_exception(exc, span=root_span)
            telemetry.set_span_attribute("squire.outcome", "extract_invalid", span=root_span)
            write_derived_event(
                derived=exc.payload,
                raw_text=exc.raw_text,
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="invalid",
                error=exc,
            )
            logging.warning("interpretation_invalid id=%s error=%s", raw_id, exc)
            await runtime.swap_reaction(context, "⏳", "⚠️")
            await _send_traced_response(runtime, context, "I couldn't parse that reliably. Please rephrase or use a prefix.")
            return
    except Exception as exc:
        if fallback_to_plain_extract:
            logging.exception("capture_interpretation_failed_fallback id=%s", raw_id)
            write_derived_event(
                derived=None,
                raw_text="",
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="decision_invalid",
                error=exc,
            )
            try:
                with telemetry.start_span("llm.extract_fallback") as extract_span:
                    interpretation = await runtime.interpret_text_async(
                        provider=provider,
                        text=fallback_capture_input,
                        model=model,
                        system_prompt=fallback_capture_prompt,
                        schema_path=schema_path,
                    )
                    interpretation.derived["raw_event_id"] = raw_id
                    telemetry.set_span_attribute(
                        "squire.object_type",
                        interpretation.derived.get("object_type"),
                        span=extract_span,
                    )
                matching_candidates = []
                decision_payload = None
                decision_artifact_id = None
                if matching_trace:
                    gate = matching_trace.get("gate")
                    if not isinstance(gate, dict):
                        gate = {}
                        matching_trace["gate"] = gate
                    gate["outcome"] = "create"
            except InterpretationValidationError as fallback_exc:
                telemetry.record_exception(fallback_exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "extract_invalid", span=root_span)
                write_derived_event(
                    derived=fallback_exc.payload,
                    raw_text=fallback_exc.raw_text,
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="invalid",
                    error=fallback_exc,
                )
                logging.warning("interpretation_invalid id=%s error=%s", raw_id, fallback_exc)
                await runtime.swap_reaction(context, "⏳", "⚠️")
                await _send_traced_response(runtime, context, "I couldn't parse that reliably. Please rephrase or use a prefix.")
                return
            except Exception as fallback_exc:
                telemetry.record_exception(fallback_exc, span=root_span)
                telemetry.set_span_attribute("squire.outcome", "extract_failed", span=root_span)
                write_derived_event(
                    derived=None,
                    raw_text="",
                    derived_root=derived_root,
                    raw_event_id=raw_id,
                    label="invalid",
                    error=fallback_exc,
                )
                logging.exception("interpretation_failed id=%s", raw_id)
                await runtime.swap_reaction(context, "⏳", "⚠️")
                await _send_traced_response(runtime, context, "Interpretation failed. Please try again.")
                return
        else:
            telemetry.record_exception(exc, span=root_span)
            telemetry.set_span_attribute("squire.outcome", "extract_failed", span=root_span)
            write_derived_event(
                derived=None,
                raw_text="",
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="invalid",
                error=exc,
            )
            logging.exception("interpretation_failed id=%s", raw_id)
            await runtime.swap_reaction(context, "⏳", "⚠️")
            await _send_traced_response(runtime, context, "Interpretation failed. Please try again.")
            return

    write_derived_event(
        derived=interpretation.derived,
        raw_text=interpretation.raw_text,
        derived_root=derived_root,
        raw_event_id=raw_id,
        label="derived",
    )
    logging.info(
        "interpretation_ok id=%s object_type=%s confidence=%.2f",
        raw_id,
        interpretation.derived.get("object_type"),
        interpretation.derived.get("confidence", 0),
    )

    effective_derived = interpretation.derived
    decision_routing = None
    trace_top_score = None
    trace_second_score = None
    if isinstance(matching_trace, dict):
        ranking = matching_trace.get("ranking")
        if isinstance(ranking, dict):
            top_value = ranking.get("top_score")
            second_value = ranking.get("second_score")
            if isinstance(top_value, (int, float)):
                trace_top_score = float(top_value)
            if isinstance(second_value, (int, float)):
                trace_second_score = float(second_value)
    if decision_prompt and decision_config:
        decision_payload = matching_pipeline.build_decision_payload_from_capture(
            raw_event_id=raw_id,
            object_type=object_type,
            derived=interpretation.derived,
            candidates=matching_candidates,
        )
        decision_result = write_derived_event(
            derived=decision_payload,
            raw_text=interpretation.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="decision",
        )
        if decision_result.derived_path:
            try:
                decision_artifact_id = str(decision_result.derived_path.relative_to(Path(derived_root)))
            except ValueError:
                decision_artifact_id = str(decision_result.derived_path)
    if decision_payload and decision_config:
        with telemetry.start_span("decision.evaluate"):
            decision_routing = evaluate_decision(
                decision_payload,
                decision_config,
                top_score=trace_top_score,
                second_score=trace_second_score,
            )
        effective_derived = apply_decision_to_derived(interpretation.derived, decision_routing)
        telemetry.set_span_attribute("squire.decision_action", decision_routing.action, span=root_span)
    if matching_trace:
        gate = matching_trace.get("gate")
        if not isinstance(gate, dict):
            gate = {}
            matching_trace["gate"] = gate
        gate["decision_confidence"] = decision_routing.confidence if decision_routing else 0.0
        gate["auto_min_score"] = decision_config.auto_min_score if decision_config else matching_config.auto_min_score
        gate["auto_min_margin"] = decision_config.auto_min_margin if decision_config else matching_config.auto_min_margin
        gate["outcome"] = decision_routing.action if decision_routing else "create"
        ranking = matching_trace.get("ranking")
        if isinstance(ranking, dict):
            ranking["top_score"] = decision_routing.top_score if decision_routing else ranking.get("top_score", 0.0)
            ranking["second_score"] = decision_routing.second_score if decision_routing else ranking.get("second_score")
            ranking["margin"] = decision_routing.margin if decision_routing else ranking.get("margin")
        with telemetry.start_span("matching.trace.write"):
            runtime.write_matching_trace(derived_root=derived_root, raw_event_id=raw_id, trace_payload=matching_trace)

    if decision_routing and decision_routing.action == "needs_confirmation":
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending_id = generate_prefixed_id("PA_")
        now_iso = runtime.now_iso()
        pending = PendingAction(
            schema_version=1,
            pending_action_id=pending_id,
            raw_event_id=raw_id,
            object_type=object_type,
            status="pending",
            created_at=now_iso,
            last_updated=now_iso,
            derived=effective_derived,
            decision=decision_payload,
            decision_confidence=decision_routing.confidence,
            last_decision_id=decision_artifact_id,
        )
        write_pending_action(pending, pending_root)
        telemetry.set_span_attributes(
            {
                "squire.pending_action_id": pending_id,
                "squire.outcome": "pending_confirmation",
            },
            span=root_span,
        )
        candidates = decision_payload.get("candidates") if isinstance(decision_payload, dict) else []
        candidate_list = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
        default_target_id = None
        proposed_ops = effective_derived.get("proposed_operations") or []
        if isinstance(proposed_ops, list) and proposed_ops:
            target_id = proposed_ops[0].get("target_id")
            if isinstance(target_id, str):
                default_target_id = target_id
        objects_root = config.get("paths", {}).get("objects_root", "objects")
        view = runtime.create_pending_action_view(
            pending_id=pending_id,
            pending_root=pending_root,
            objects_root=objects_root,
            index_db=config.get("paths", {}).get("index_db", "index/sb.sqlite"),
            schema_path=schema_map.get(object_type),
            author_id=runtime.author_id(context),
            candidates=candidate_list,
            default_target_id=default_target_id,
            matching=matching_config,
            affinity_key=runtime.cursor_key(context),
            config=config,
        )
        await runtime.swap_reaction(context, "⏳", "❓")
        await _send_traced_response(
            runtime,
            context,
            runtime.format_pending_message(pending_id, decision_payload),
            view=view,
        )
        return

    objects_root = config.get("paths", {}).get("objects_root", "objects")
    try:
        with telemetry.start_span("canonical.apply"):
            result = runtime.apply_operations(
                effective_derived,
                objects_root=objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=schema_path,
                last_decision_id=decision_artifact_id,
            )
    except Exception:
        logging.exception("apply_failed id=%s object_type=%s", raw_id, object_type)
        telemetry.set_span_attribute("squire.outcome", "apply_failed", span=root_span)
        await runtime.swap_reaction(context, "⏳", "⚠️")
        await _send_traced_response(runtime, context, "Failed to save item. Please try again.")
        return
    logging.info(
        "apply_ok id=%s object_type=%s written=%s",
        raw_id,
        object_type,
        ",".join(str(path) for path in result.written_paths),
    )
    with telemetry.start_span("index.refresh"):
        await runtime.refresh_index_async(
            objects_root,
            config.get("paths", {}).get("index_db", "index/sb.sqlite"),
            matching=matching_config,
        )
    runtime.notify_due_time_reminder_schedule_changed()
    touched_ids = runtime.extract_target_ids_from_derived(effective_derived)
    touched_ids.extend(runtime.extract_ids_from_written_paths(result.written_paths))
    runtime.record_affinity_touches(runtime.cursor_key(context), touched_ids, matching=matching_config)

    title = effective_derived.get("extracted_fields", {}).get("title") or content
    op = None
    ops = effective_derived.get("proposed_operations") or []
    if ops:
        op = ops[0].get("op")
    await runtime.swap_reaction(context, "⏳", "✅")
    verb = "Saved"
    if op in {"update", "append"}:
        verb = "Updated"
    feedback_view = None
    auto_apply_target_id = None
    if decision_routing and decision_routing.action == "auto_apply":
        decision_ops = decision_routing.decision_ops
        if decision_ops and isinstance(decision_ops[0], dict):
            target_id = decision_ops[0].get("target_id")
            if isinstance(target_id, str):
                auto_apply_target_id = target_id
                feedback_view = runtime.create_auto_apply_feedback_view(
                    author_id=runtime.author_id(context),
                    target_id=target_id,
                )
    response = f"{verb} \"{title}\" in {object_type.capitalize()}."
    if auto_apply_target_id:
        response = f"{response} (Auto-applied.)"
    telemetry.set_span_attribute("squire.outcome", "saved", span=root_span)
    await _send_traced_response(runtime, context, response, thread_title=title, view=feedback_view)
