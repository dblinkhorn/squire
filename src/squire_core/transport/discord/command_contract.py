"""Discord command contract constants and small formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_MAP = {
    "people": Path("config/schemas/derived_event_people_v1.json"),
    "projects": Path("config/schemas/derived_event_projects_v1.json"),
    "ideas": Path("config/schemas/derived_event_ideas_v1.json"),
    "admin": Path("config/schemas/derived_event_admin_v1.json"),
}

PENDING_CONTROLS_INSTRUCTION = (
    "Use the buttons below to confirm which note should be updated, choose to create a new note, or cancel (do nothing):"
)
NUMBERED_COMMAND_TIP = (
    "Tip: `!show <number>` · `!done <number>` · `!append <number> <text>` · `!fix <number> field=value`"
)
NUMBERED_COMMAND_TIP_WITH_RECENT_LIMIT = (
    "Tip: `!show <number>` · `!done <number>` · `!append <number> <text>` · `!fix <number> field=value` · `!recent <number>` (up to 50)"
)
NUMBERED_LIST_ACTION_HELP_COPY = (
    "After this command shows a numbered list, you can use those numbers to take action on items (for example: "
    "`!show 2`, `!append 2 <text>`, `!fix 2 field=value`, or `!done 2` for admin items)."
)
HELP_COPY = (
    "Available commands:\n"
    "- `!help [command]` - show this list or command details\n"
    "- `!status` - show daily digest\n"
    "- `!weekly` - show weekly review\n"
    "- `!recent [number]` - list recent notes\n"
    "- `!find <query>` - search notes\n"
    "- `!show <number>` - open one result\n"
    "- `!append <id|number> <text>` - append note text\n"
    "- `!done <id|number>` - mark admin done\n"
    "- `!fix <id|number> <field=value> [field=value ...]` - edit note fields\n"
    "- `!confirm <pending_id>` - apply pending change\n"
    "- `!cancel <pending_id>` - cancel pending change\n"
    "- `!clear-archive` then `DELETE` - clear archive data\n"
    "Tip: run `!help <command>` for more details."
)
HELP_DETAILS = {
    "help": (
        "`!help [command]`\n"
        "Shows the command list. Add a command name for a more detailed description of that command."
    ),
    "status": (
        "`!status`\n"
        "Shows your daily digest.\n"
        + NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "weekly": (
        "`!weekly`\n"
        "Shows your weekly review.\n"
        + NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "recent": (
        "`!recent [number]`\n"
        "Lists your recent notes. Use `!recent [number]` to show your most recent notes (up to 50).\n"
        + NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "find": (
        "`!find <query>`\n"
        "Searches your notes by title and body.\n"
        + NUMBERED_LIST_ACTION_HELP_COPY
    ),
    "show": (
        "`!show <number>`\n"
        "Opens details for one item from your latest numbered list (for example, after `!recent`, `!find`, "
        "`!status`, or `!weekly`)."
    ),
    "append": (
        "`!append <id|number> <text>`\n"
        "Appends text to an existing note body. The target can be an ID or a row number from your latest numbered "
        "list (for example, after `!recent`, `!find`, `!status`, or `!weekly`)."
    ),
    "done": (
        "`!done <id|number>`\n"
        "Marks an admin item as done. This sets `status=done` and records `completed_at`. The target can be an ID "
        "or a row number from your latest numbered list (for example, after `!recent`, `!find`, `!status`, or "
        "`!weekly`)."
    ),
    "fix": (
        "`!fix <id|number> <field=value> [field=value ...]`\n"
        "Updates allowed fields on an existing note. Quote values containing spaces (for example "
        "`next_action=\"Call dentist\"`). The target can be an ID or a row number from your latest numbered list "
        "(for example, after `!recent`, `!find`, `!status`, or `!weekly`)."
    ),
    "confirm": (
        "`!confirm <pending_id>`\n"
        "Confirms and applies a pending note update."
    ),
    "cancel": (
        "`!cancel <pending_id>`\n"
        "Cancels a pending note update without applying changes."
    ),
    "clear-archive": (
        "`!clear-archive`\n"
        "Starts the destructive archive reset flow. You will lose all notes. Confirm with 'DELETE'."
    ),
}
NL_OUT_OF_SCOPE_CLARIFICATION_COPY = (
    "Before I can proceed with any other actions, I need clarification on the unresolved parts of the previous request. "
    "You may cancel your last action if you'd like to take a new action now."
)


def parse_positive_int(value: str) -> int | None:
    trimmed = value.strip()
    if not trimmed.isdigit():
        return None
    parsed = int(trimmed)
    if parsed <= 0:
        return None
    return parsed


def normalize_help_topic(value: str) -> str:
    topic = value.strip().lower()
    if topic.startswith("!"):
        topic = topic[1:]
    if topic == "clear_archive":
        return "clear-archive"
    return topic


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_display_title(candidate: dict[str, Any]) -> str:
    title = candidate.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Untitled note"


def format_pending_message(_pending_id: str, decision_payload: dict[str, Any]) -> str:
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
        lines.append("")
        lines.append("Proposed updates:")
        for target_id in targets:
            candidate = candidate_lookup.get(target_id, {})
            lines.append(f"- {_candidate_display_title(candidate)}")
    candidate_ids = [
        candidate.get("id")
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("id"), str)
    ]
    alternates = [candidate_id for candidate_id in candidate_ids if isinstance(candidate_id, str) and candidate_id not in targets]
    if alternates:
        lines.append("")
        lines.append("Other close matches:")
        for candidate_id in alternates:
            candidate = candidate_lookup.get(candidate_id, {})
            lines.append(f"- {_candidate_display_title(candidate)}")
    lines.append("")
    lines.append(PENDING_CONTROLS_INSTRUCTION)
    lines.append("\u200b")
    return "\n".join(lines)
