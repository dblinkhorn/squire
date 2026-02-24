from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Source(str, Enum):
    discord = "discord"
    webhook = "webhook"


@dataclass(frozen=True)
class RawEvent:
    raw_event_id: str
    source: Source
    source_message_id: str
    timestamp: str
    text: str


def _format_frontmatter(event: RawEvent) -> str:
    lines = [
        "---",
        f"id: {event.raw_event_id}",
        f"source: {event.source.value}",
        f"source_message_id: {event.source_message_id}",
        f"timestamp: {event.timestamp}",
        "---",
    ]
    return "\n".join(lines)


def write_raw_event(event: RawEvent, raw_dir: str | Path) -> Path:
    raw_dir_path = Path(raw_dir)
    raw_dir_path.mkdir(parents=True, exist_ok=True)
    output_path = raw_dir_path / f"{event.raw_event_id}.md"

    content = f"{_format_frontmatter(event)}\n\n{event.text.strip()}\n"
    output_path.write_text(content, encoding="utf-8")
    return output_path
