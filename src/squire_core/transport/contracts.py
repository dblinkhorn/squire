"""Transport-agnostic contracts for shared command/routing modules.

Stage 0 introduces these contracts without wiring runtime behavior to them.
Adapters can progressively adopt these structures in later stages.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol


@dataclass(frozen=True)
class TransportMessageContext:
    """Normalized inbound message shape used across transport adapters."""

    source: str
    user_id: str
    channel_id: str
    thread_id: str | None
    message_id: str
    content: str
    is_dm: bool
    created_at: datetime


class TransportIO(Protocol):
    """Minimal side-effect surface shared modules can target."""

    async def send_text(self, text: str) -> None:
        ...

    async def send_warning(self, text: str) -> None:
        ...

    async def add_reaction(self, emoji: str) -> None:
        ...

    async def send_pending_controls(self, pending_id: str, payload: dict[str, Any]) -> None:
        ...


SendTextFn = Callable[[str], Awaitable[None]]
AddReactionFn = Callable[[str], Awaitable[None]]
SendPendingControlsFn = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class CommandResult:
    """Transport-neutral command handling result container."""

    handled: bool
    response_texts: list[str] = field(default_factory=list)
    warning_texts: list[str] = field(default_factory=list)
    reactions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteResult:
    """Transport-neutral NL route evaluation result container."""

    handled: bool
    blocked_reason: str | None = None
    read_command: dict[str, Any] | None = None
    mutation_plan: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

