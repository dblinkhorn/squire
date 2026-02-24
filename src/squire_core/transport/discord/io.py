"""Discord-specific IO wrappers."""

from __future__ import annotations

import discord

from squire_core.transport.discord.adapter import (
    safe_add_reaction as _discord_safe_add_reaction,
    send_response as _discord_send_response,
    swap_reaction as _discord_swap_reaction,
)


async def safe_add_reaction(message: discord.Message, emoji: str) -> None:
    await _discord_safe_add_reaction(message, emoji)


async def swap_reaction(message: discord.Message, remove_emoji: str, add_emoji: str) -> None:
    await _discord_swap_reaction(message, remove_emoji, add_emoji)


async def send_response(
    message: discord.Message,
    content: str,
    thread_title: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    await _discord_send_response(
        message,
        content,
        thread_title=thread_title,
        view=view,
    )
