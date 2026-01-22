from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

import discord
from dotenv import load_dotenv

from squire_core.config_utils import load_config, load_decision_config, normalize_archive_config
from squire_core.decision_models import DecisionCandidate
from squire_core.decision_flow import apply_decision_to_derived, evaluate_decision
from squire_core.derived_event_store import write_derived_event
from squire_core.id_utils import generate_prefixed_id
from squire_core.indexer import find_candidates, rebuild_index
from squire_core.interpreter import InterpretationValidationError, interpret_text
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.prompts import load_prompt
from squire_core.operation_apply import apply_operations
from squire_core.pending_actions import (
    PendingAction,
    load_pending_action,
    update_pending_action_status,
    write_pending_action,
)
from squire_core.raw_event import RawEvent, Source, write_raw_event
from squire_core.canonical_store import find_object_path, load_frontmatter
from squire_core.surfacing import build_daily_digest
from squire_core.timezone_utils import (
    format_reference_date,
    format_reference_time,
    format_reference_weekday,
    resolve_timezone,
)

_SCHEMA_MAP = {
    "people": Path("config/schemas/derived_event_people_v1.json"),
    "projects": Path("config/schemas/derived_event_projects_v1.json"),
    "ideas": Path("config/schemas/derived_event_ideas_v1.json"),
    "admin": Path("config/schemas/derived_event_admin_v1.json"),
}
_VIEW_TIMEOUT_SECONDS = 3600
_SELECT_OPTION_LIMIT = 25
_SELECT_LABEL_LIMIT = 100
_SELECT_DESCRIPTION_LIMIT = 100


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            return int(trimmed)
    return None


def _parse_daily_digest_time(value: Any) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        value = str(value)
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _next_daily_run(now: datetime, target: time) -> datetime:
    candidate = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def _truncate_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _refresh_index(objects_root: str | Path, index_db: str | Path) -> None:
    try:
        rebuild_index(objects_root, index_db)
        logging.info("index_rebuilt path=%s", index_db)
    except Exception as exc:
        logging.exception("index_rebuild_failed error=%s", exc)


def _merge_candidates(candidates: list[DecisionCandidate], limit: int) -> list[DecisionCandidate]:
    merged: dict[str, DecisionCandidate] = {}
    for candidate in candidates:
        existing = merged.get(candidate.object_id)
        if existing is None or candidate.score > existing.score:
            merged[candidate.object_id] = candidate
    if not merged:
        return []
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]


def _candidate_queries_from_llm(
    *,
    provider: OpenAIProvider,
    model: str,
    prompt: str,
    message: str,
) -> list[str]:
    schema_path = Path("config/schemas/candidate_query_v1.json")
    try:
        result = interpret_text(
            provider=provider,
            text=message,
            model=model,
            system_prompt=prompt,
            schema_path=schema_path,
        )
    except Exception as exc:
        logging.warning("candidate_query_failed error=%s", exc)
        return []
    payload = result.derived if isinstance(result.derived, dict) else {}
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return []
    cleaned = []
    for query in queries:
        if not isinstance(query, str):
            continue
        value = query.strip()
        if value:
            cleaned.append(value)
    return cleaned


class _CandidateSelect(discord.ui.Select):
    def __init__(self, view: "PendingActionView", options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose a different target (optional)",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not self._view_ref.is_author(interaction):
            await interaction.response.send_message("This selection is not for you.")
            return
        if not self.values:
            await interaction.response.defer()
            return
        self._view_ref.selected_target_id = self.values[0]
        await interaction.response.defer()


class PendingActionView(discord.ui.View):
    def __init__(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        schema_path: Path,
        author_id: int,
        candidates: list[dict[str, Any]],
        default_target_id: str | None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.pending_id = pending_id
        self.pending_root = pending_root
        self.objects_root = objects_root
        self.index_db = index_db
        self.schema_path = schema_path
        self.author_id = author_id
        self.selected_target_id = default_target_id
        self._default_target_id = default_target_id

        if default_target_id and len(candidates) > 1:
            options = []
            for candidate in candidates[:_SELECT_OPTION_LIMIT]:
                candidate_id = candidate.get("id")
                if not isinstance(candidate_id, str):
                    continue
                title = candidate.get("title")
                label = _truncate_text(str(title or candidate_id), _SELECT_LABEL_LIMIT) or candidate_id
                snippet = _truncate_text(str(candidate.get("snippet") or ""), _SELECT_DESCRIPTION_LIMIT)
                options.append(
                    discord.SelectOption(
                        label=label,
                        value=candidate_id,
                        description=snippet if snippet else None,
                        default=candidate_id == default_target_id,
                    )
                )
            if options:
                self.add_item(_CandidateSelect(self, options))

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    async def _apply_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This confirmation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"Pending action {self.pending_id} is {pending.status}.")
            return
        derived = pending.derived
        ops = derived.get("proposed_operations") or []
        if (
            self.selected_target_id
            and self._default_target_id
            and self.selected_target_id != self._default_target_id
            and isinstance(ops, list)
            and len(ops) == 1
            and isinstance(ops[0], dict)
        ):
            updated_op = dict(ops[0])
            updated_op["target_id"] = self.selected_target_id
            derived = dict(derived)
            derived["proposed_operations"] = [updated_op]
        try:
            result = apply_operations(
                derived,
                objects_root=self.objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=self.schema_path,
                last_decision_id=pending.last_decision_id,
            )
        except Exception as exc:
            logging.exception("pending_apply_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed", derived=derived)
            await interaction.response.send_message("Failed to apply pending action. Check logs for details.")
            return
        _refresh_index(self.objects_root, self.index_db)
        _write_pending_with_status(self.pending_root, pending, "confirmed", derived=derived)
        await interaction.response.send_message(
            f"Applied pending action {self.pending_id}. ({len(result.written_paths)} item(s) updated.)"
        )
        await _disable_view(interaction)

    async def _cancel_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This cancellation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"Pending action {self.pending_id} is {pending.status}.")
            return
        _write_pending_with_status(self.pending_root, pending, "cancelled")
        await interaction.response.send_message(f"Cancelled pending action {self.pending_id}.")
        await _disable_view(interaction)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._apply_pending(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._cancel_pending(interaction)


class AutoApplyFeedbackView(discord.ui.View):
    def __init__(self, *, author_id: int, target_id: str) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.target_id = target_id

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    @discord.ui.button(label="Was this incorrect?", style=discord.ButtonStyle.secondary)
    async def incorrect_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This feedback is not for you.")
            return
        await interaction.response.send_message(
            "Sorry about that. Reply with `!fix {id} field=value` or `!append {id} <text>` to correct it.".format(
                id=self.target_id
            )
        )
        await _disable_view(interaction)


async def _handle_message(message: discord.Message, config: dict[str, Any]) -> None:
    if message.author.bot:
        return

    content = (message.content or "").strip()
    if not content:
        return
    await _safe_add_reaction(message, "⏳")

    raw_dir = Path(config.get("paths", {}).get("events_raw", "events/raw"))
    derived_root = Path(config.get("paths", {}).get("events_derived", "events/derived"))
    raw_id = _generate_raw_id()
    raw_event = RawEvent(
        raw_event_id=raw_id,
        source=Source.discord,
        source_message_id=str(message.id),
        timestamp=message.created_at.isoformat(),
        text=content,
    )
    write_raw_event(raw_event, raw_dir)
    logging.info(
        "raw_event_written id=%s source=discord source_message_id=%s",
        raw_id,
        message.id,
    )

    if content.startswith("!"):
        handled = await _handle_command(message, content, raw_id, config)
        if handled:
            return

    model = config.get("llm", {}).get("interpreter_model")
    if not model:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "No interpreter model configured.")
        return

    classify_schema = Path("config/schemas/derived_event_classify_v1.json")

    provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))

    classify_prompt_path = config.get("llm", {}).get("classify_prompt_path")
    extract_prompt_path = config.get("llm", {}).get("interpreter_prompt_path")
    decision_prompt_path = config.get("llm", {}).get("decision_prompt_path")
    candidate_query_prompt_path = config.get("llm", {}).get(
        "candidate_query_prompt_path",
        "config/prompts/candidate_query_v1.txt",
    )
    if not classify_prompt_path or not extract_prompt_path:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Prompt paths are missing. Set llm.classify_prompt_path and llm.interpreter_prompt_path.")
        return

    try:
        classify_prompt = load_prompt(classify_prompt_path)
        extract_prompt = load_prompt(extract_prompt_path)
    except OSError as exc:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Failed to load prompt files: {exc}")
        return
    decision_prompt = None
    candidate_query_prompt = None
    if decision_prompt_path:
        try:
            decision_prompt = load_prompt(decision_prompt_path)
        except OSError as exc:
            logging.warning("decision_prompt_load_failed path=%s error=%s", decision_prompt_path, exc)
    if candidate_query_prompt_path:
        try:
            candidate_query_prompt = load_prompt(candidate_query_prompt_path)
        except OSError as exc:
            logging.warning("candidate_query_prompt_load_failed path=%s error=%s", candidate_query_prompt_path, exc)
    decision_config = load_decision_config(config) if decision_prompt else None
    decision_payload: dict[str, Any] | None = None
    decision_artifact_id: str | None = None

    tz_name = config.get("timezone")
    tz = resolve_timezone(tz_name)
    reference = (
        f"Reference date: {format_reference_date(tz)}. "
        f"Reference weekday: {format_reference_weekday(tz)}. "
        f"Reference time: {format_reference_time(tz)}."
    )
    extract_prompt = f"{extract_prompt} {reference}"

    try:
        classification = interpret_text(
            provider=provider,
            text=content,
            model=model,
            system_prompt=classify_prompt,
            schema_path=classify_schema,
        )
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.warning("classification_invalid id=%s error=%s", raw_id, exc)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "I couldn't parse that reliably. Please rephrase or use a prefix.")
        return
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.exception("classification_failed id=%s", raw_id)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Interpretation failed. Please try again.")
        return

    write_derived_event(
        derived=classification.derived,
        raw_text=classification.raw_text,
        derived_root=derived_root,
        raw_event_id=raw_id,
        label="classify",
    )
    logging.info(
        "classification_ok id=%s object_type=%s confidence=%.2f",
        raw_id,
        classification.derived.get("object_type"),
        classification.derived.get("confidence", 0),
    )

    object_type = classification.derived.get("object_type")
    confidence = classification.derived.get("confidence", 0)
    threshold = config.get("confidence", {}).get("create_threshold", 0.6)

    if not isinstance(object_type, str) or object_type == "unknown" or confidence < threshold:
        logging.info(
            "classification_low_confidence id=%s object_type=%s confidence=%.2f threshold=%.2f",
            raw_id,
            object_type,
            confidence,
            threshold,
        )
        await _swap_reaction(message, "⏳", "❓")
        await _send_response(
            message,
            "I couldn't confidently classify that. Please clarify or use a prefix (admin:, project:, idea:, person:)."
        )
        return

    schema_path = _SCHEMA_MAP.get(object_type)
    if not schema_path:
        await message.channel.send("Unrecognized category. Please use a prefix.")
        return

    if decision_prompt and decision_config:
        index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
        queries = [content]
        if candidate_query_prompt:
            llm_queries = _candidate_queries_from_llm(
                provider=provider,
                model=model,
                prompt=candidate_query_prompt,
                message=content,
            )
            if llm_queries:
                queries = llm_queries
        gathered: list[DecisionCandidate] = []
        for query in queries:
            gathered.extend(
                find_candidates(
                    index_db,
                    query,
                    object_type=object_type,
                    limit=decision_config.candidate_limit,
                    score_threshold=decision_config.candidate_score_threshold,
                )
            )
        candidates = _merge_candidates(gathered, decision_config.candidate_limit)
        decision_input = _build_decision_input(
            raw_event_id=raw_id,
            object_type=object_type,
            message=content,
            candidates=candidates,
        )
        decision_schema = Path("config/schemas/decision_v1.json")
        try:
            decision = interpret_text(
                provider=provider,
                text=decision_input,
                model=model,
                system_prompt=decision_prompt,
                schema_path=decision_schema,
            )
        except InterpretationValidationError as exc:
            write_derived_event(
                derived=exc.payload,
                raw_text=exc.raw_text,
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="decision_invalid",
                error=exc,
            )
            logging.warning("decision_invalid id=%s error=%s", raw_id, exc)
        except Exception as exc:
            write_derived_event(
                derived=None,
                raw_text="",
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="decision_invalid",
                error=exc,
            )
            logging.exception("decision_failed id=%s", raw_id)
        else:
            decision_result = write_derived_event(
                derived=decision.derived,
                raw_text=decision.raw_text,
                derived_root=derived_root,
                raw_event_id=raw_id,
                label="decision",
            )
            decision_payload = decision.derived
            if decision_result.derived_path:
                try:
                    decision_artifact_id = str(decision_result.derived_path.relative_to(Path(derived_root)))
                except ValueError:
                    decision_artifact_id = str(decision_result.derived_path)
            logging.info(
                "decision_ok id=%s object_type=%s confidence=%.2f candidates=%s",
                raw_id,
                object_type,
                decision.derived.get("confidence", 0),
                len(candidates),
            )
    elif decision_prompt:
        logging.warning("decision_config_missing id=%s decision_prompt_path=%s", raw_id, decision_prompt_path)

    try:
        interpretation = interpret_text(
            provider=provider,
            text=content,
            model=model,
            system_prompt=extract_prompt,
            schema_path=schema_path,
        )
        interpretation.derived["raw_event_id"] = raw_id
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.warning("interpretation_invalid id=%s error=%s", raw_id, exc)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "I couldn't parse that reliably. Please rephrase or use a prefix.")
        return
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=raw_id,
            label="invalid",
            error=exc,
        )
        logging.exception("interpretation_failed id=%s", raw_id)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Interpretation failed. Please try again.")
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
    if decision_payload and decision_config:
        decision_routing = evaluate_decision(decision_payload, decision_config)
        effective_derived = apply_decision_to_derived(interpretation.derived, decision_routing)
        if decision_routing.action == "needs_confirmation":
            pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
            pending_id = generate_prefixed_id("PA_")
            now_iso = _now_iso()
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
            candidates = decision_payload.get("candidates") if isinstance(decision_payload, dict) else []
            candidate_list = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
            default_target_id = None
            proposed_ops = effective_derived.get("proposed_operations") or []
            if isinstance(proposed_ops, list) and proposed_ops:
                target_id = proposed_ops[0].get("target_id")
                if isinstance(target_id, str):
                    default_target_id = target_id
            schema_path = _SCHEMA_MAP.get(object_type)
            objects_root = config.get("paths", {}).get("objects_root", "objects")
            view = None
            if schema_path:
                view = PendingActionView(
                    pending_id=pending_id,
                    pending_root=pending_root,
                    objects_root=objects_root,
                    index_db=config.get("paths", {}).get("index_db", "index/sb.sqlite"),
                    schema_path=schema_path,
                    author_id=message.author.id,
                    candidates=candidate_list,
                    default_target_id=default_target_id,
                )
            await _swap_reaction(message, "⏳", "❓")
            await _send_response(
                message,
                _format_pending_message(pending_id, decision_payload),
                view=view,
            )
            return

    objects_root = config.get("paths", {}).get("objects_root", "objects")
    try:
        result = apply_operations(
            effective_derived,
            objects_root=objects_root,
            canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
            derived_schema_path=schema_path,
            last_decision_id=decision_artifact_id,
        )
    except Exception as exc:
        logging.exception("apply_failed id=%s object_type=%s", raw_id, object_type)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Failed to save item. Please try again.")
        return
    logging.info(
        "apply_ok id=%s object_type=%s written=%s",
        raw_id,
        object_type,
        ",".join(str(path) for path in result.written_paths),
    )
    _refresh_index(objects_root, config.get("paths", {}).get("index_db", "index/sb.sqlite"))

    title = effective_derived.get("extracted_fields", {}).get("title") or content
    op = None
    ops = effective_derived.get("proposed_operations") or []
    if ops:
        op = ops[0].get("op")
    await _swap_reaction(message, "⏳", "✅")
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
                feedback_view = AutoApplyFeedbackView(author_id=message.author.id, target_id=target_id)
    response = f"{verb} \"{title}\" in {object_type.capitalize()}."
    if auto_apply_target_id:
        response = f"{response} (Auto-applied.)"
    await _send_response(message, response, thread_title=title, view=feedback_view)


def _generate_raw_id() -> str:
    return generate_prefixed_id("R_")


def _build_decision_input(
    *,
    raw_event_id: str,
    object_type: str,
    message: str,
    candidates: list[Any],
) -> str:
    payload = {
        "raw_event_id": raw_event_id,
        "object_type": object_type,
        "message": message,
        "candidates": [
            {
                "id": candidate.object_id,
                "title": candidate.title,
                "snippet": candidate.snippet,
                "score": candidate.score,
            }
            for candidate in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=True)


def _write_pending_with_status(
    root: str | Path,
    pending: PendingAction,
    status: str,
    *,
    derived: dict[str, Any] | None = None,
) -> PendingAction:
    updated = PendingAction(
        schema_version=pending.schema_version,
        pending_action_id=pending.pending_action_id,
        raw_event_id=pending.raw_event_id,
        object_type=pending.object_type,
        status=status,
        created_at=pending.created_at,
        last_updated=_now_iso(),
        derived=derived or pending.derived,
        decision=pending.decision,
        decision_confidence=pending.decision_confidence,
        last_decision_id=pending.last_decision_id,
    )
    write_pending_action(updated, root)
    return updated


async def _disable_view(interaction: discord.Interaction) -> None:
    try:
        if interaction.message:
            await interaction.message.edit(view=None)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


async def _safe_add_reaction(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except (discord.HTTPException, discord.Forbidden):
        return


async def _swap_reaction(message: discord.Message, remove_emoji: str, add_emoji: str) -> None:
    await _safe_add_reaction(message, add_emoji)
    try:
        bot_user = message.guild.me if message.guild else message._state.user
        if bot_user is None:
            return
        await message.remove_reaction(remove_emoji, cast(discord.abc.Snowflake, bot_user))
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


async def _send_response(
    message: discord.Message,
    content: str,
    thread_title: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    if isinstance(message.channel, discord.Thread):
        try:
            await message.channel.send(content=content, view=view)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("response_send_failed channel=thread error=%s", exc)
        return
    try:
        name = "Squire"
        if thread_title:
            trimmed = thread_title.strip()
            if len(trimmed) > 60:
                trimmed = trimmed[:57].rstrip() + "..."
            name = f"Squire: {trimmed}"
        else:
            name = f"Squire: {message.author.display_name}"
        thread = await message.create_thread(
            name=name,
            auto_archive_duration=1440,
        )
        await thread.send(content=content, view=view)
        logging.info("response_sent thread=%s", thread.id)
        return
    except (discord.HTTPException, discord.Forbidden) as exc:
        logging.warning("thread_create_failed channel=%s error=%s", message.channel.id, exc)
        try:
            await message.channel.send(content=content, view=view)
            logging.info("response_sent channel=%s", message.channel.id)
        except (discord.HTTPException, discord.Forbidden) as send_exc:
            logging.warning("response_send_failed channel=%s error=%s", message.channel.id, send_exc)


async def _handle_command(
    message: discord.Message,
    content: str,
    raw_id: str,
    config: dict[str, Any],
) -> bool:
    parts = content.split()
    command = parts[0].lower()
    if command == "!status":
        objects_root = config.get("paths", {}).get("objects_root", "objects")
        try:
            digest = build_daily_digest(objects_root, config)
        except Exception:
            logging.exception("status_digest_failed id=%s", raw_id)
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Failed to build status digest. Check logs for details.")
            return True
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, digest.render())
        return True
    if command == "!append":
        if len(parts) < 3:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !append <id> <text>")
            return True
        target_id = parts[1]
        text = content.split(None, 2)[2].strip()
        if not text:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !append <id> <text>")
            return True
        return await _apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="append",
            fields={"body": text},
        )
    if command == "!done":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !done <id>")
            return True
        target_id = parts[1]
        return await _apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="update",
            fields={"status": "done", "completed_at": _now_iso()},
        )
    if command == "!fix":
        if len(parts) < 3:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !fix <id> field=value [field=value...]")
            return True
        target_id = parts[1]
        updates: dict[str, Any] = {}
        for token in parts[2:]:
            if "=" not in token:
                await _swap_reaction(message, "⏳", "⚠️")
                await _send_response(message, "Usage: !fix <id> field=value [field=value...]")
                return True
            key, value = token.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in {"id", "type", "created_at"}:
                continue
            updates[key] = value
        if not updates:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "No valid fields provided.")
            return True
        return await _apply_command_operation(
            message,
            raw_id,
            config,
            target_id=target_id,
            op="update",
            fields=updates,
        )
    if command == "!confirm":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !confirm <pending_id>")
            return True
        pending_id = parts[1]
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = load_pending_action(pending_root, pending_id)
        if not pending:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Pending action {pending_id} is {pending.status}.")
            return True
        object_type = pending.object_type
        schema_path = _SCHEMA_MAP.get(object_type)
        if not schema_path:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Pending action has an unsupported object type.")
            return True
        objects_root = config.get("paths", {}).get("objects_root", "objects")
        try:
            result = apply_operations(
                pending.derived,
                objects_root=objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=schema_path,
                last_decision_id=pending.last_decision_id,
            )
        except Exception as exc:
            logging.exception("pending_apply_failed id=%s", pending_id)
            update_pending_action_status(pending_root, pending_id, "failed")
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Failed to apply pending action. Check logs for details.")
            return True
        update_pending_action_status(pending_root, pending_id, "confirmed")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(
            message,
            f"Applied pending action {pending_id}. ({len(result.written_paths)} item(s) updated.)",
        )
        return True
    if command == "!cancel":
        if len(parts) != 2:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, "Usage: !cancel <pending_id>")
            return True
        pending_id = parts[1]
        pending_root = config.get("paths", {}).get("pending_actions", "events/pending")
        pending = load_pending_action(pending_root, pending_id)
        if not pending:
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Unknown pending action: {pending_id}")
            return True
        if pending.status != "pending":
            await _swap_reaction(message, "⏳", "⚠️")
            await _send_response(message, f"Pending action {pending_id} is {pending.status}.")
            return True
        update_pending_action_status(pending_root, pending_id, "cancelled")
        await _swap_reaction(message, "⏳", "✅")
        await _send_response(message, f"Cancelled pending action {pending_id}.")
        return True
    return False


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _format_pending_message(pending_id: str, decision_payload: dict[str, Any]) -> str:
    operations = decision_payload.get("proposed_operations") or []
    candidates = decision_payload.get("candidates") or []
    targets = []
    for op in operations:
        target_id = op.get("target_id")
        if isinstance(target_id, str):
            targets.append(target_id)
    candidate_lookup = {candidate.get("id"): candidate for candidate in candidates if isinstance(candidate, dict)}
    lines = ["I found a possible match and want to confirm before updating."]
    if targets:
        lines.append("Proposed updates:")
        for target_id in targets:
            candidate = candidate_lookup.get(target_id, {})
            title = candidate.get("title") or target_id
            lines.append(f"- {title} ({target_id})")
    lines.append(f"Reply `!confirm {pending_id}` to apply or `!cancel {pending_id}` to skip.")
    return "\n".join(lines)


async def _apply_command_operation(
    message: discord.Message,
    raw_id: str,
    config: dict[str, Any],
    target_id: str,
    op: str,
    fields: dict[str, Any],
) -> bool:
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    target_path = find_object_path(objects_root, target_id)
    if not target_path:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Unknown ID: {target_id}")
        return True
    frontmatter = load_frontmatter(target_path)
    object_type = frontmatter.get("type")
    if not object_type:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, f"Unable to determine object type for {target_id}")
        return True
    if op == "update" and object_type != "admin" and fields.get("status") == "done":
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Only admin items can be marked done.")
        return True
    derived = {
        "object_type": object_type,
        "raw_event_id": raw_id,
        "extracted_fields": {},
        "proposed_operations": [
            {
                "op": op,
                "target_id": target_id,
                "fields": fields,
            }
        ],
    }
    try:
        result = apply_operations(
            derived,
            objects_root=objects_root,
            canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
            derived_schema_path=None,
        )
    except Exception as exc:
        logging.exception("command_apply_failed id=%s op=%s", raw_id, op)
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "Command failed. Check logs for details.")
        return True
    _refresh_index(objects_root, config.get("paths", {}).get("index_db", "index/sb.sqlite"))
    await _swap_reaction(message, "⏳", "✅")
    title = frontmatter.get("title") or target_id
    await _send_response(
        message,
        f"Updated {object_type} \"{title}\".",
        thread_title=title,
    )
    return True


class SquireBot(discord.Client):
    def __init__(self, config: dict[str, Any]) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._config = config
        schedule = config.get("schedule", {}) if isinstance(config.get("schedule"), dict) else {}
        self._digest_time = _parse_daily_digest_time(schedule.get("daily_digest_time"))
        self._digest_channel_id = _coerce_int(schedule.get("daily_digest_channel_id"))
        self._digest_user_id = _coerce_int(schedule.get("daily_digest_user_id"))
        self._last_dm_channel_id: int | None = None
        self._last_dm_user_id: int | None = None
        self._timezone = resolve_timezone(config.get("timezone"))
        self._digest_task: asyncio.Task | None = None

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")
        if self._digest_time and self._digest_task is None:
            self._digest_task = asyncio.create_task(self._daily_digest_loop())

    async def on_message(self, message: discord.Message) -> None:
        if not message.author.bot and isinstance(message.channel, discord.DMChannel):
            self._last_dm_channel_id = message.channel.id
            self._last_dm_user_id = message.author.id
        await _handle_message(message, self._config)

    async def _resolve_digest_channel(self) -> discord.abc.Messageable | None:
        if self._digest_channel_id:
            channel = self.get_channel(self._digest_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(self._digest_channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("daily_digest_channel_unavailable id=%s", self._digest_channel_id)
                return None
        if self._digest_user_id:
            user = self.get_user(self._digest_user_id)
            if not user:
                try:
                    user = await self.fetch_user(self._digest_user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logging.warning("daily_digest_user_unavailable id=%s", self._digest_user_id)
                    user = None
            if user:
                if user.dm_channel:
                    return user.dm_channel
                try:
                    return await user.create_dm()
                except (discord.HTTPException, discord.Forbidden):
                    logging.warning("daily_digest_dm_create_failed user=%s", self._digest_user_id)
                    return None
        if self._last_dm_channel_id:
            channel = self.get_channel(self._last_dm_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                return channel
            try:
                fetched = await self.fetch_channel(self._last_dm_channel_id)
                if isinstance(fetched, discord.abc.Messageable):
                    return fetched
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("daily_digest_last_dm_unavailable id=%s", self._last_dm_channel_id)
                return None
        return None

    async def _send_daily_digest(self) -> None:
        channel = await self._resolve_digest_channel()
        if not channel:
            logging.warning("daily_digest_skipped reason=no_channel")
            return
        objects_root = self._config.get("paths", {}).get("objects_root", "objects")
        digest = build_daily_digest(objects_root, self._config)
        try:
            await channel.send(content=digest.render())
        except (discord.HTTPException, discord.Forbidden) as exc:
            logging.warning("daily_digest_send_failed error=%s", exc)

    async def _daily_digest_loop(self) -> None:
        if not self._digest_time:
            return
        while not self.is_closed():
            now = datetime.now(self._timezone)
            target = _next_daily_run(now, self._digest_time)
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self._send_daily_digest()
            except Exception:
                logging.exception("daily_digest_failed")


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = Path("config.yaml")
    config = load_config(config_path)
    try:
        config = normalize_archive_config(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")
    index_path = Path(index_db)
    if not index_path.exists():
        logging.info("index_missing rebuilding index at %s", index_db)
        try:
            rebuild_index(objects_root, index_db)
        except Exception as exc:
            logging.exception("index_rebuild_failed error=%s", exc)
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is required")

    bot = SquireBot(config=config)
    bot.run(token)


if __name__ == "__main__":
    main()
