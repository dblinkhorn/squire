from __future__ import annotations

from ulid import ULID


def generate_ulid() -> str:
    return str(ULID())


def generate_prefixed_id(prefix: str) -> str:
    return f"{prefix}{generate_ulid()}"
