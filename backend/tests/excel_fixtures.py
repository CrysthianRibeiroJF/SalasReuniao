"""Fixtures do módulo Excel (plugin pytest). Para usar, declare no módulo de teste:
pytest_plugins = ("tests.excel_fixtures",)
"""

from __future__ import annotations

import pytest

from app.models import Room
from app.realtime import events
from tests.core_support import RecordingBus
from tests.excel_builders import ROOMS


@pytest.fixture
async def rooms(make_room) -> dict[str, Room]:
    """Salas padrão (2 de vídeo, 2 normais) por nome."""
    result = {}
    for order, (name, room_type, capacity) in enumerate(ROOMS, start=1):
        result[name] = await make_room(name, room_type, capacity=capacity, display_order=order)
    return result


@pytest.fixture
def recording_bus() -> RecordingBus:
    """EventBus que grava os eventos (sem subir a aplicação). O conftest o remove ao final."""
    bus = RecordingBus()
    events.set_event_bus(bus)
    return bus
