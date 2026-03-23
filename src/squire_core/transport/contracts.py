"""Transport-agnostic contracts shared across transport runtimes."""

from dataclasses import dataclass
from datetime import datetime


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
