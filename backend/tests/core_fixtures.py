"""Fixtures do núcleo que registram fakes de tempo real (plugin pytest).

Dependem de `app`: são registradas DEPOIS do startup do tempo real, que de outra forma
substituiria os fakes pelas implementações reais. Para usá-las, declare no módulo de teste:
    pytest_plugins = ("tests.core_fixtures",)
O conftest restaura NullLockStore / bus None ao fim de cada teste.
"""

from __future__ import annotations

import pytest

from app.realtime import events, registry
from tests.core_support import FakeLockStore, RecordingBus


@pytest.fixture
async def bus(app) -> RecordingBus:
    recording = RecordingBus()
    events.set_event_bus(recording)
    return recording


@pytest.fixture
async def lock_store(app) -> FakeLockStore:
    store = FakeLockStore()
    registry.set_lock_store(store)
    return store
