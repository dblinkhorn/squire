from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import discord
from dotenv import load_dotenv

from squire_core.config_utils import load_config, normalize_archive_config
from squire_core.derived_event_store import write_derived_event
from squire_core.id_utils import generate_prefixed_id
from squire_core.interpreter import InterpretationValidationError, interpret_text
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.prompts import load_prompt
from squire_core.operation_apply import apply_operations
from squire_core.raw_event import RawEvent, Source, write_raw_event
from squire_core.timezone_utils import (
    format_reference_date,
    format_reference_time,
    format_reference_weekday,
    resolve_timezone,
)


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

    model = config.get("llm", {}).get("interpreter_model")
    if not model:
        await _swap_reaction(message, "⏳", "⚠️")
        await _send_response(message, "No interpreter model configured.")
        return

    classify_schema = Path("config/schemas/derived_event_classify_v1.json")
    schema_map = {
        "people": Path("config/schemas/derived_event_people_v1.json"),
        "projects": Path("config/schemas/derived_event_projects_v1.json"),
        "ideas": Path("config/schemas/derived_event_ideas_v1.json"),
        "admin": Path("config/schemas/derived_event_admin_v1.json"),
    }

    provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))

    classify_prompt_path = config.get("llm", {}).get("classify_prompt_path")
    extract_prompt_path = config.get("llm", {}).get("interpreter_prompt_path")
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

    schema_path = schema_map.get(object_type)
    if not schema_path:
        await message.channel.send("Unrecognized category. Please use a prefix.")
        return

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

    objects_root = config.get("paths", {}).get("objects_root", "objects")
    try:
        result = apply_operations(
            interpretation.derived,
            objects_root=objects_root,
            canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
            derived_schema_path=schema_path,
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

    title = interpretation.derived.get("extracted_fields", {}).get("title") or content
    await _swap_reaction(message, "⏳", "✅")
    await _send_response(message, f"Saved \"{title}\" to {object_type.capitalize()}.")


def _generate_raw_id() -> str:
    return generate_prefixed_id("R_")


async def _safe_add_reaction(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except (discord.HTTPException, discord.Forbidden):
        return


async def _swap_reaction(message: discord.Message, remove_emoji: str, add_emoji: str) -> None:
    await _safe_add_reaction(message, add_emoji)
    try:
        bot_user = message.guild.me if message.guild else message._state.user
        await message.remove_reaction(remove_emoji, bot_user)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


async def _send_response(message: discord.Message, content: str) -> None:
    if isinstance(message.channel, discord.Thread):
        await message.channel.send(content)
        return
    try:
        thread = await message.create_thread(
            name=f"squire: {message.author.display_name}",
            auto_archive_duration=1440,
        )
        await thread.send(content)
        return
    except (discord.HTTPException, discord.Forbidden):
        await message.channel.send(content)


class SquireBot(discord.Client):
    def __init__(self, config: dict[str, Any]) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._config = config

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")

    async def on_message(self, message: discord.Message) -> None:
        await _handle_message(message, self._config)


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = Path("config.yaml")
    config = load_config(config_path)
    try:
        config = normalize_archive_config(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is required")

    bot = SquireBot(config=config)
    bot.run(token)


if __name__ == "__main__":
    main()
