from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ALLOWED_STATUSES = {"pending", "confirmed", "cancelled", "failed"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pending_path(root: str | Path, pending_action_id: str) -> Path:
    return Path(root) / f"{pending_action_id}.json"


@dataclass(frozen=True)
class PendingAction:
    schema_version: int
    pending_action_id: str
    raw_event_id: str
    object_type: str
    status: str
    created_at: str
    last_updated: str
    derived: dict[str, Any]
    decision: dict[str, Any] | None = None
    decision_confidence: float | None = None
    last_decision_id: str | None = None

    def with_status(self, status: str) -> "PendingAction":
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"Unsupported pending action status: {status}")
        return PendingAction(
            schema_version=self.schema_version,
            pending_action_id=self.pending_action_id,
            raw_event_id=self.raw_event_id,
            object_type=self.object_type,
            status=status,
            created_at=self.created_at,
            last_updated=_now_iso(),
            derived=self.derived,
            decision=self.decision,
            decision_confidence=self.decision_confidence,
            last_decision_id=self.last_decision_id,
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "pending_action_id": self.pending_action_id,
            "raw_event_id": self.raw_event_id,
            "object_type": self.object_type,
            "status": self.status,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "derived": self.derived,
        }
        if self.decision is not None:
            payload["decision"] = self.decision
        if self.decision_confidence is not None:
            payload["decision_confidence"] = self.decision_confidence
        if self.last_decision_id is not None:
            payload["last_decision_id"] = self.last_decision_id
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PendingAction":
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            pending_action_id=str(payload.get("pending_action_id")),
            raw_event_id=str(payload.get("raw_event_id")),
            object_type=str(payload.get("object_type")),
            status=str(payload.get("status")),
            created_at=str(payload.get("created_at")),
            last_updated=str(payload.get("last_updated")),
            derived=payload.get("derived") or {},
            decision=payload.get("decision"),
            decision_confidence=payload.get("decision_confidence"),
            last_decision_id=payload.get("last_decision_id"),
        )


def write_pending_action(pending: PendingAction, root: str | Path) -> Path:
    if pending.status not in _ALLOWED_STATUSES:
        raise ValueError(f"Unsupported pending action status: {pending.status}")
    path = _pending_path(root, pending.pending_action_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_pending_action(root: str | Path, pending_action_id: str) -> PendingAction | None:
    path = _pending_path(root, pending_action_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PendingAction.from_dict(payload)


def update_pending_action_status(
    root: str | Path,
    pending_action_id: str,
    status: str,
) -> PendingAction | None:
    pending = load_pending_action(root, pending_action_id)
    if pending is None:
        return None
    updated = pending.with_status(status)
    write_pending_action(updated, root)
    return updated
