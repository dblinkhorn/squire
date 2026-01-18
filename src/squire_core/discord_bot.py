from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import discord
from dotenv import load_dotenv

import yaml
from squire_core.interpreter import interpret_text
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.prompts import DEFAULT_CLASSIFY_PROMPT, DEFAULT_EXTRACT_PROMPT
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

    raw_dir = Path(config.get("paths", {}).get("events_raw", "events/raw"))
    raw_id = _generate_raw_id()
    raw_event = RawEvent(
        raw_event_id=raw_id,
        source=Source.discord,
        source_message_id=str(message.id),
        timestamp=message.created_at.isoformat(),
        text=content,
    )
    write_raw_event(raw_event, raw_dir)

    model = config.get("llm", {}).get("interpreter_model")
    if not model:
        await message.channel.send("No interpreter model configured.")
        return

    classify_schema = Path("config/schemas/derived_event_classify_v1.json")
    schema_map = {
        "people": Path("config/schemas/derived_event_people_v1.json"),
        "projects": Path("config/schemas/derived_event_projects_v1.json"),
        "ideas": Path("config/schemas/derived_event_ideas_v1.json"),
        "admin": Path("config/schemas/derived_event_admin_v1.json"),
    }

    provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))

    classify_prompt = config.get("llm", {}).get("classify_prompt") or DEFAULT_CLASSIFY_PROMPT
    extract_prompt = config.get("llm", {}).get("interpreter_prompt") or DEFAULT_EXTRACT_PROMPT

    tz_name = config.get("timezone")
    tz = resolve_timezone(tz_name)
    reference = (
        f"Reference date: {format_reference_date(tz)}. "
        f"Reference weekday: {format_reference_weekday(tz)}. "
        f"Reference time: {format_reference_time(tz)}."
    )
    extract_prompt = f"{extract_prompt} {reference}"

    classification = interpret_text(
        provider=provider,
        text=content,
        model=model,
        system_prompt=classify_prompt,
        schema_path=classify_schema,
    )

    object_type = classification.derived.get("object_type")
    confidence = classification.derived.get("confidence", 0)
    threshold = config.get("confidence", {}).get("create_threshold", 0.6)

    if not isinstance(object_type, str) or object_type == "unknown" or confidence < threshold:
        await message.channel.send(
            "I couldn't confidently classify that. Please clarify or use a prefix (admin:, project:, idea:, person:)."
        )
        return

    schema_path = schema_map.get(object_type)
    if not schema_path:
        await message.channel.send("Unrecognized category. Please use a prefix.")
        return

    interpretation = interpret_text(
        provider=provider,
        text=content,
        model=model,
        system_prompt=extract_prompt,
        schema_path=schema_path,
    )

    objects_root = config.get("paths", {}).get("objects_root", "objects")
    result = apply_operations(
        interpretation.derived,
        objects_root=objects_root,
        canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
        derived_schema_path=schema_path,
    )

    title = interpretation.derived.get("extracted_fields", {}).get("title") or content
    await message.channel.send(f"Saved \"{title}\" to {object_type.capitalize()}.")


def _generate_raw_id() -> str:
    # TODO: replace with ULID generator
    import secrets

    return f"R_{secrets.token_hex(8).upper()}"


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
    config_path = Path("config.yaml")
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is required")

    bot = SquireBot(config=config)
    bot.run(token)


if __name__ == "__main__":
    main()
