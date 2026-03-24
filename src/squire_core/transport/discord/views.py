"""Discord-specific interaction views."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

import discord
from opentelemetry.trace import SpanKind

from squire_core import telemetry
from squire_core.config_utils import MatchingConfig
from squire_core.transport.pending_interactions import (
    LogConfirmAppliedFn,
    NowIsoFn,
    PendingInteractionRuntime,
    cancel_pending_action,
    confirm_capture_pending_create_new,
    confirm_capture_pending_update,
    confirm_nl_pending,
)

_VIEW_TIMEOUT_SECONDS = 3600
_SELECT_OPTION_LIMIT = 25
_SELECT_LABEL_LIMIT = 100
_SELECT_DESCRIPTION_LIMIT = 100
_PENDING_CONTROLS_INSTRUCTION = (
    "Use the buttons below to confirm which note should be updated, choose to create a new note, or cancel (do nothing):"
)


def _truncate_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
def _candidate_title(candidates: list[dict[str, Any]], candidate_id: str | None) -> str | None:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("id") != candidate_id:
            continue
        title = candidate.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def _candidate_display_title(candidate: dict[str, Any]) -> str:
    title = candidate.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Untitled note"
def _strip_pending_controls_from_message(content: str) -> str:
    lines = content.split("\n")
    cleaned = [
        line
        for line in lines
        if line != _PENDING_CONTROLS_INSTRUCTION and line != "\u200b"
    ]
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


async def _disable_view(
    interaction: discord.Interaction,
    *,
    clear_pending_instructions: bool = False,
) -> None:
    try:
        if interaction.message:
            edit_kwargs: dict[str, Any] = {"view": None}
            if clear_pending_instructions:
                message_content = getattr(interaction.message, "content", None)
                if isinstance(message_content, str):
                    edit_kwargs["content"] = _strip_pending_controls_from_message(message_content)
            await interaction.message.edit(**edit_kwargs)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return


def _interaction_attributes(
    interaction: discord.Interaction,
    *,
    pending_id: str | None = None,
    target_id: str | None = None,
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "squire.transport": "discord",
    }
    user = getattr(interaction, "user", None)
    if user is not None:
        user_id = getattr(user, "id", None)
        if user_id is not None:
            attributes["discord.user_id"] = str(user_id)
    if pending_id:
        attributes["squire.pending_action_id"] = pending_id
    if target_id:
        attributes["squire.target_id"] = target_id
    return attributes
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
        runtime: PendingInteractionRuntime,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        schema_path: Path,
        author_id: int,
        candidates: list[dict[str, Any]],
        default_target_id: str | None,
        matching: MatchingConfig | None,
        affinity_key: tuple[int, int],
        confirm_action: str | None = None,
        selected_target_id: str | None = None,
        now_iso: NowIsoFn | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self._runtime = runtime
        self.pending_id = pending_id
        self.pending_root = pending_root
        self.objects_root = objects_root
        self.index_db = index_db
        self.schema_path = schema_path
        self.author_id = author_id
        self.selected_target_id = selected_target_id if selected_target_id else default_target_id
        self._default_target_id = default_target_id
        self._matching = matching
        self._affinity_key = affinity_key
        self._candidates = [candidate for candidate in candidates if isinstance(candidate, dict)]
        self._confirm_action = confirm_action
        self._now_iso = now_iso

        if confirm_action:
            self._render_confirmation()
        else:
            self._render_primary()

    def _render_primary(self) -> None:
        self.clear_items()
        if self._default_target_id and len(self._candidates) > 1:
            options = []
            selected = self.selected_target_id or self._default_target_id
            for candidate in self._candidates[:_SELECT_OPTION_LIMIT]:
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
                        default=candidate_id == selected,
                    )
                )
            if options:
                self.add_item(_CandidateSelect(self, options))
        self._add_button("Confirm", discord.ButtonStyle.green, self._begin_confirm_update, row=1)
        self._add_button("Create New", discord.ButtonStyle.primary, self._begin_confirm_create_new, row=1)
        self._add_button("Cancel", discord.ButtonStyle.gray, self._begin_confirm_cancel, row=1)

    def _render_confirmation(self) -> None:
        self.clear_items()
        action = self._confirm_action or ""
        if action == "confirm":
            label = "Yes, apply update"
            style = discord.ButtonStyle.green
        elif action == "create_new":
            label = "Yes, create new"
            style = discord.ButtonStyle.primary
        else:
            label = "Yes, cancel (do nothing)"
            style = discord.ButtonStyle.gray
        self._add_button(label, style, self._confirm_selected_action, row=1)
        self._add_button("No, go back", discord.ButtonStyle.secondary, self._restore_primary_actions, row=1)

    def _add_button(
        self,
        label: str,
        style: discord.ButtonStyle,
        handler: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int | None = None,
    ) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)

        async def _callback(interaction: discord.Interaction) -> None:
            await handler(interaction)

        button.callback = _callback  # type: ignore[assignment]
        self.add_item(button)

    def _spawn_view(self, *, confirm_action: str | None) -> "PendingActionView":
        return PendingActionView(
            runtime=self._runtime,
            pending_id=self.pending_id,
            pending_root=self.pending_root,
            objects_root=self.objects_root,
            index_db=self.index_db,
            schema_path=self.schema_path,
            author_id=self.author_id,
            candidates=self._candidates,
            default_target_id=self._default_target_id,
            matching=self._matching,
            affinity_key=self._affinity_key,
            confirm_action=confirm_action,
            selected_target_id=self.selected_target_id,
            now_iso=self._now_iso,
        )

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    async def _begin_confirm_update(self, interaction: discord.Interaction) -> None:
        await self._show_confirmation(interaction, "confirm")

    async def _begin_confirm_create_new(self, interaction: discord.Interaction) -> None:
        await self._show_confirmation(interaction, "create_new")

    async def _begin_confirm_cancel(self, interaction: discord.Interaction) -> None:
        await self._show_confirmation(interaction, "cancel")

    async def _show_confirmation(self, interaction: discord.Interaction, action: str) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This action is not for you.")
            return
        confirm_view = self._spawn_view(confirm_action=action)
        await interaction.response.edit_message(view=confirm_view)

    async def _restore_primary_actions(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This action is not for you.")
            return
        original_view = self._spawn_view(confirm_action=None)
        await interaction.response.edit_message(view=original_view)

    async def _confirm_selected_action(self, interaction: discord.Interaction) -> None:
        action = self._confirm_action
        if action == "confirm":
            await self._confirm_capture_update(interaction)
            return
        if action == "create_new":
            await self._confirm_capture_create_new(interaction)
            return
        await self._confirm_capture_cancel(interaction)

    async def _confirm_capture_update(self, interaction: discord.Interaction) -> None:
        with telemetry.start_span(
            "discord.interaction.pending.confirm",
            kind=SpanKind.CONSUMER,
            attributes=_interaction_attributes(
                interaction,
                pending_id=self.pending_id,
                target_id=self.selected_target_id,
            ),
        ) as root_span:
            if not self.is_author(interaction):
                telemetry.set_span_attribute("squire.outcome", "unauthorized", span=root_span)
                with telemetry.start_span("response.send"):
                    await interaction.response.send_message("This confirmation is not for you.")
                return
            result = await confirm_capture_pending_update(
                runtime=self._runtime,
                pending_id=self.pending_id,
                pending_root=self.pending_root,
                objects_root=self.objects_root,
                index_db=self.index_db,
                derived_schema_path=self.schema_path,
                selected_target_id=self.selected_target_id,
                default_target_id=self._default_target_id,
                fallback_title=_candidate_title(self._candidates, self.selected_target_id),
                matching=self._matching,
                affinity_key=self._affinity_key,
                now_iso=self._now_iso,
            )
            telemetry.set_span_attribute("squire.outcome", result.outcome, span=root_span)
            with telemetry.start_span("response.send"):
                await interaction.response.send_message(result.response_text)
            if result.outcome in {"confirmed", "created_new", "cancelled"}:
                await _disable_view(
                    interaction,
                    clear_pending_instructions=result.clear_pending_instructions,
                )

    async def _confirm_capture_create_new(self, interaction: discord.Interaction) -> None:
        with telemetry.start_span(
            "discord.interaction.pending.create_new",
            kind=SpanKind.CONSUMER,
            attributes=_interaction_attributes(interaction, pending_id=self.pending_id),
        ) as root_span:
            if not self.is_author(interaction):
                telemetry.set_span_attribute("squire.outcome", "unauthorized", span=root_span)
                with telemetry.start_span("response.send"):
                    await interaction.response.send_message("This action is not for you.")
                return
            result = await confirm_capture_pending_create_new(
                runtime=self._runtime,
                pending_id=self.pending_id,
                pending_root=self.pending_root,
                objects_root=self.objects_root,
                index_db=self.index_db,
                derived_schema_path=self.schema_path,
                matching=self._matching,
                affinity_key=self._affinity_key,
                now_iso=self._now_iso,
            )
            telemetry.set_span_attribute("squire.outcome", result.outcome, span=root_span)
            with telemetry.start_span("response.send"):
                await interaction.response.send_message(result.response_text)
            if result.outcome in {"confirmed", "created_new", "cancelled"}:
                await _disable_view(
                    interaction,
                    clear_pending_instructions=result.clear_pending_instructions,
                )

    async def _confirm_capture_cancel(self, interaction: discord.Interaction) -> None:
        with telemetry.start_span(
            "discord.interaction.pending.cancel",
            kind=SpanKind.CONSUMER,
            attributes=_interaction_attributes(interaction, pending_id=self.pending_id),
        ) as root_span:
            if not self.is_author(interaction):
                telemetry.set_span_attribute("squire.outcome", "unauthorized", span=root_span)
                with telemetry.start_span("response.send"):
                    await interaction.response.send_message("This cancellation is not for you.")
                return
            result = await cancel_pending_action(
                runtime=self._runtime,
                pending_id=self.pending_id,
                pending_root=self.pending_root,
                clear_pending_instructions=True,
                now_iso=self._now_iso,
            )
            telemetry.set_span_attribute("squire.outcome", result.outcome, span=root_span)
            with telemetry.start_span("response.send"):
                await interaction.response.send_message(result.response_text)
            if result.outcome in {"confirmed", "created_new", "cancelled"}:
                await _disable_view(
                    interaction,
                    clear_pending_instructions=result.clear_pending_instructions,
                )


class MutationPendingView(discord.ui.View):
    def __init__(
        self,
        *,
        runtime: PendingInteractionRuntime,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        author_id: int,
        matching: MatchingConfig | None,
        affinity_key: tuple[int, int],
        now_iso: NowIsoFn | None = None,
        log_confirm_applied: LogConfirmAppliedFn | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self._runtime = runtime
        self.pending_id = pending_id
        self.pending_root = pending_root
        self.objects_root = objects_root
        self.index_db = index_db
        self.author_id = author_id
        self._matching = matching
        self._affinity_key = affinity_key
        self._now_iso = now_iso
        self._log_confirm_applied = log_confirm_applied

    def is_author(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return bool(user and user.id == self.author_id)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        with telemetry.start_span(
            "discord.interaction.nl_pending.confirm",
            kind=SpanKind.CONSUMER,
            attributes=_interaction_attributes(interaction, pending_id=self.pending_id),
        ) as root_span:
            if not self.is_author(interaction):
                telemetry.set_span_attribute("squire.outcome", "unauthorized", span=root_span)
                with telemetry.start_span("response.send"):
                    await interaction.response.send_message("This confirmation is not for you.")
                return
            result = await confirm_nl_pending(
                runtime=self._runtime,
                pending_id=self.pending_id,
                pending_root=self.pending_root,
                objects_root=self.objects_root,
                index_db=self.index_db,
                matching=self._matching,
                affinity_key=self._affinity_key,
                now_iso=self._now_iso,
                log_confirm_applied=self._log_confirm_applied,
            )
            telemetry.set_span_attribute("squire.outcome", result.outcome, span=root_span)
            with telemetry.start_span("response.send"):
                await interaction.response.send_message(result.response_text)
            if result.outcome in {"confirmed", "created_new", "cancelled"}:
                await _disable_view(
                    interaction,
                    clear_pending_instructions=result.clear_pending_instructions,
                )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        with telemetry.start_span(
            "discord.interaction.nl_pending.cancel",
            kind=SpanKind.CONSUMER,
            attributes=_interaction_attributes(interaction, pending_id=self.pending_id),
        ) as root_span:
            if not self.is_author(interaction):
                telemetry.set_span_attribute("squire.outcome", "unauthorized", span=root_span)
                with telemetry.start_span("response.send"):
                    await interaction.response.send_message("This cancellation is not for you.")
                return
            result = await cancel_pending_action(
                runtime=self._runtime,
                pending_id=self.pending_id,
                pending_root=self.pending_root,
                now_iso=self._now_iso,
            )
            telemetry.set_span_attribute("squire.outcome", result.outcome, span=root_span)
            with telemetry.start_span("response.send"):
                await interaction.response.send_message(result.response_text)
            if result.outcome in {"confirmed", "created_new", "cancelled"}:
                await _disable_view(
                    interaction,
                    clear_pending_instructions=result.clear_pending_instructions,
                )


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
        del button
        with telemetry.start_span(
            "discord.interaction.auto_apply_feedback",
            kind=SpanKind.CONSUMER,
            attributes=_interaction_attributes(interaction, target_id=self.target_id),
        ) as root_span:
            if not self.is_author(interaction):
                telemetry.set_span_attribute("squire.outcome", "unauthorized", span=root_span)
                with telemetry.start_span("response.send"):
                    await interaction.response.send_message("This feedback is not for you.")
                return
            telemetry.set_span_attribute("squire.outcome", "feedback_sent", span=root_span)
            with telemetry.start_span("response.send"):
                await interaction.response.send_message(
                    "Sorry about that. Reply with `!fix {id} field=value` or `!append {id} <text>` to correct it.".format(
                        id=self.target_id
                    )
                )
            await _disable_view(interaction)
