"""Discord-specific interaction views."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import discord

from squire_core.config_utils import MatchingConfig
from squire_core.decision_flow import DecisionRouting, apply_decision_to_derived
from squire_core.operation_apply import apply_operations
from squire_core.pending_actions import PendingAction, load_pending_action, write_pending_action
from squire_core.canonical_store import load_frontmatter

_VIEW_TIMEOUT_SECONDS = 3600
_SELECT_OPTION_LIMIT = 25
_SELECT_LABEL_LIMIT = 100
_SELECT_DESCRIPTION_LIMIT = 100
_PENDING_CONTROLS_INSTRUCTION = (
    "Use the buttons below to confirm which note should be updated, choose to create a new note, or cancel (do nothing):"
)

RefreshIndexAsyncFn = Callable[[str | Path, str | Path], Awaitable[None]]
ExtractTargetIdsFromDerivedFn = Callable[[dict[str, Any]], list[str]]
ExtractIdsFromWrittenPathsFn = Callable[[list[Path]], list[str]]
RecordAffinityTouchesFn = Callable[[tuple[int, int], list[str], MatchingConfig], None]
NowIsoFn = Callable[[], str]


def _truncate_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_title_from_paths(paths: list[Path]) -> str | None:
    for path in paths:
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        title = frontmatter.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def _titles_from_paths(paths: list[Path]) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            frontmatter = load_frontmatter(path)
        except Exception:
            continue
        title = frontmatter.get("title")
        if not isinstance(title, str):
            continue
        value = title.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        titles.append(value)
    return titles


def _format_apply_success_message(*, written_paths: list[Path], fallback_title: str | None = None) -> str:
    titles = _titles_from_paths(written_paths)
    if not titles and fallback_title:
        titles = [fallback_title]
    if len(titles) >= 1:
        if len(titles) == 1:
            header = "✅ Applied update to 1 note:"
        else:
            header = f"✅ Applied updates to {len(titles)} notes:"
        lines = [header]
        for title in titles[:5]:
            lines.append(f'- "{title}"')
        more_count = len(titles) - 5
        if more_count > 0:
            lines.append(f"- and {more_count} more")
        return "\n".join(lines)
    return f"✅ Applied update. ({len(written_paths)} item(s) updated.)"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_pending_with_status(
    root: str | Path,
    pending: PendingAction,
    status: str,
    *,
    derived: dict[str, Any] | None = None,
    now_iso: NowIsoFn | None = None,
) -> PendingAction:
    now_iso_fn = now_iso or _now_iso
    updated = PendingAction(
        schema_version=pending.schema_version,
        pending_action_id=pending.pending_action_id,
        raw_event_id=pending.raw_event_id,
        object_type=pending.object_type,
        status=status,
        created_at=pending.created_at,
        last_updated=now_iso_fn(),
        derived=derived or pending.derived,
        decision=pending.decision,
        decision_confidence=pending.decision_confidence,
        last_decision_id=pending.last_decision_id,
    )
    write_pending_action(updated, root)
    return updated


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


def _force_create_derived(derived: dict[str, Any]) -> dict[str, Any]:
    routing = DecisionRouting(
        action="create",
        confidence=0.0,
        decision_ops=[],
        top_score=0.0,
        second_score=None,
        margin=None,
    )
    return apply_decision_to_derived(derived, routing)


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
        matching: MatchingConfig | None,
        affinity_key: tuple[int, int],
        on_canonical_change: Callable[[], None] | None = None,
        confirm_action: str | None = None,
        selected_target_id: str | None = None,
        refresh_index_async: RefreshIndexAsyncFn | None = None,
        extract_target_ids_from_derived: ExtractTargetIdsFromDerivedFn | None = None,
        extract_ids_from_written_paths: ExtractIdsFromWrittenPathsFn | None = None,
        record_affinity_touches: RecordAffinityTouchesFn | None = None,
        now_iso: NowIsoFn | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
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
        self._on_canonical_change = on_canonical_change
        self._candidates = [candidate for candidate in candidates if isinstance(candidate, dict)]
        self._confirm_action = confirm_action
        self._refresh_index_async = refresh_index_async
        self._extract_target_ids_from_derived = extract_target_ids_from_derived
        self._extract_ids_from_written_paths = extract_ids_from_written_paths
        self._record_affinity_touches = record_affinity_touches
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
            on_canonical_change=self._on_canonical_change,
            confirm_action=confirm_action,
            selected_target_id=self.selected_target_id,
            refresh_index_async=self._refresh_index_async,
            extract_target_ids_from_derived=self._extract_target_ids_from_derived,
            extract_ids_from_written_paths=self._extract_ids_from_written_paths,
            record_affinity_touches=self._record_affinity_touches,
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
            await self._apply_pending(interaction)
            return
        if action == "create_new":
            await self._create_new_pending(interaction)
            return
        await self._cancel_pending(interaction)

    async def _apply_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This confirmation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
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
        except Exception:
            logging.exception("pending_apply_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed", derived=derived, now_iso=self._now_iso)
            await interaction.response.send_message("Failed to apply pending action. Check logs for details.")
            return
        if self._refresh_index_async is not None:
            await self._refresh_index_async(self.objects_root, self.index_db)
        if self._on_canonical_change:
            self._on_canonical_change()
        if (
            self._matching
            and self._extract_target_ids_from_derived is not None
            and self._record_affinity_touches is not None
        ):
            touched_ids = self._extract_target_ids_from_derived(derived)
            self._record_affinity_touches(self._affinity_key, touched_ids, self._matching)
        _write_pending_with_status(self.pending_root, pending, "confirmed", derived=derived, now_iso=self._now_iso)
        fallback_title = _candidate_title(self._candidates, self.selected_target_id)
        await interaction.response.send_message(
            _format_apply_success_message(written_paths=result.written_paths, fallback_title=fallback_title)
        )
        await _disable_view(interaction, clear_pending_instructions=True)

    async def _create_new_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This action is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        derived = _force_create_derived(pending.derived)
        try:
            result = apply_operations(
                derived,
                objects_root=self.objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=self.schema_path,
                last_decision_id=pending.last_decision_id,
            )
        except Exception:
            logging.exception("pending_create_new_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed", derived=derived, now_iso=self._now_iso)
            await interaction.response.send_message("Failed to create a new item. Check logs for details.")
            return
        if self._refresh_index_async is not None:
            await self._refresh_index_async(self.objects_root, self.index_db)
        if self._on_canonical_change:
            self._on_canonical_change()
        if (
            self._matching
            and self._extract_ids_from_written_paths is not None
            and self._record_affinity_touches is not None
        ):
            touched_ids = self._extract_ids_from_written_paths(result.written_paths)
            self._record_affinity_touches(self._affinity_key, touched_ids, self._matching)
        _write_pending_with_status(self.pending_root, pending, "confirmed", derived=derived, now_iso=self._now_iso)
        title = _first_title_from_paths(result.written_paths)
        if title:
            await interaction.response.send_message(f'Created a new note "{title}".')
        else:
            await interaction.response.send_message(f"Created a new note. ({len(result.written_paths)} item(s) updated.)")
        await _disable_view(interaction, clear_pending_instructions=True)

    async def _cancel_pending(self, interaction: discord.Interaction) -> None:
        if not self.is_author(interaction):
            await interaction.response.send_message("This cancellation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        _write_pending_with_status(self.pending_root, pending, "cancelled", now_iso=self._now_iso)
        await interaction.response.send_message("Cancelled. No changes made.")
        await _disable_view(interaction, clear_pending_instructions=True)


class MutationPendingView(discord.ui.View):
    def __init__(
        self,
        *,
        pending_id: str,
        pending_root: str | Path,
        objects_root: str | Path,
        index_db: str | Path,
        author_id: int,
        matching: MatchingConfig | None,
        affinity_key: tuple[int, int],
        on_canonical_change: Callable[[], None] | None = None,
        refresh_index_async: RefreshIndexAsyncFn | None = None,
        extract_target_ids_from_derived: ExtractTargetIdsFromDerivedFn | None = None,
        extract_ids_from_written_paths: ExtractIdsFromWrittenPathsFn | None = None,
        record_affinity_touches: RecordAffinityTouchesFn | None = None,
        now_iso: NowIsoFn | None = None,
        log_confirm_applied: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.pending_id = pending_id
        self.pending_root = pending_root
        self.objects_root = objects_root
        self.index_db = index_db
        self.author_id = author_id
        self._matching = matching
        self._affinity_key = affinity_key
        self._on_canonical_change = on_canonical_change
        self._refresh_index_async = refresh_index_async
        self._extract_target_ids_from_derived = extract_target_ids_from_derived
        self._extract_ids_from_written_paths = extract_ids_from_written_paths
        self._record_affinity_touches = record_affinity_touches
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
        if not self.is_author(interaction):
            await interaction.response.send_message("This confirmation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        try:
            result = apply_operations(
                pending.derived,
                objects_root=self.objects_root,
                canonical_schema_path=Path("config/schemas/canonical_object_v1.json"),
                derived_schema_path=None,
                last_decision_id=pending.last_decision_id,
            )
        except Exception:
            logging.exception("nl_mutation_pending_apply_failed id=%s", self.pending_id)
            _write_pending_with_status(self.pending_root, pending, "failed", now_iso=self._now_iso)
            await interaction.response.send_message("Failed to apply pending action. Check logs for details.")
            return
        if self._refresh_index_async is not None:
            await self._refresh_index_async(self.objects_root, self.index_db)
        if self._on_canonical_change:
            self._on_canonical_change()
        if (
            self._matching
            and self._extract_target_ids_from_derived is not None
            and self._extract_ids_from_written_paths is not None
            and self._record_affinity_touches is not None
        ):
            touched_ids = self._extract_target_ids_from_derived(pending.derived)
            touched_ids.extend(self._extract_ids_from_written_paths(result.written_paths))
            self._record_affinity_touches(self._affinity_key, touched_ids, self._matching)
        _write_pending_with_status(self.pending_root, pending, "confirmed", now_iso=self._now_iso)
        if self._log_confirm_applied is not None:
            self._log_confirm_applied(self.pending_id)
        await interaction.response.send_message(_format_apply_success_message(written_paths=result.written_paths))
        await _disable_view(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if not self.is_author(interaction):
            await interaction.response.send_message("This cancellation is not for you.")
            return
        pending = load_pending_action(self.pending_root, self.pending_id)
        if not pending:
            await interaction.response.send_message("That pending action no longer exists.")
            return
        if pending.status != "pending":
            await interaction.response.send_message(f"This pending action is already {pending.status}.")
            return
        _write_pending_with_status(self.pending_root, pending, "cancelled", now_iso=self._now_iso)
        await interaction.response.send_message("Cancelled. No changes made.")
        await _disable_view(interaction)


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
        if not self.is_author(interaction):
            await interaction.response.send_message("This feedback is not for you.")
            return
        await interaction.response.send_message(
            "Sorry about that. Reply with `!fix {id} field=value` or `!append {id} <text>` to correct it.".format(
                id=self.target_id
            )
        )
        await _disable_view(interaction)
