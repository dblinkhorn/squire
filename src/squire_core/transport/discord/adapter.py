"""Discord transport adapter and IO helpers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, cast

import discord

from squire_core.transport.discord.scheduler import DiscordSchedulerMixin

MessageHandlerFn = Callable[[discord.Message, dict[str, Any]], Awaitable[None]]


async def safe_add_reaction(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except (discord.HTTPException, discord.Forbidden):
        return


async def swap_reaction(message: discord.Message, remove_emoji: str, add_emoji: str) -> None:
    await safe_add_reaction(message, add_emoji)
    try:
        bot_user = message.guild.me if message.guild else message._state.user
        if bot_user is None:
            return
        await message.remove_reaction(remove_emoji, cast(discord.abc.Snowflake, bot_user))
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


async def send_response(
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


class DiscordSquireBot(DiscordSchedulerMixin, discord.Client):
    def __init__(
        self,
        config: dict[str, Any],
        message_handler: MessageHandlerFn | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        if message_handler is None:
            async def _noop_handler(message: discord.Message, config: dict[str, Any]) -> None:
                del message, config
                return
            self._message_handler = _noop_handler
        else:
            self._message_handler = message_handler
        self._init_scheduler_state(config)

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")
        await self._start_scheduler_tasks()

    async def on_message(self, message: discord.Message) -> None:
        if not message.author.bot and isinstance(message.channel, discord.DMChannel):
            self._last_dm_channel_id = message.channel.id
            self._last_dm_user_id = message.author.id
        await self._message_handler(message, self._config)
