"""Transport-agnostic contracts for shared command/routing modules.

Stage 0 introduces these contracts without wiring runtime behavior to them.
Adapters can progressively adopt these structures in later stages.
"""

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
