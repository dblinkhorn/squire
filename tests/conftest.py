from __future__ import annotations

import pytest

from squire_core.transport.state import RuntimeStateStore


@pytest.fixture
def runtime_state() -> RuntimeStateStore:
    return RuntimeStateStore()
