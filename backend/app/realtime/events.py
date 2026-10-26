"""Tipos de evento do WebSocket e função publish() usada pelos serviços após o COMMIT.

Envelope (JSON) em ambas as direções:
    {"type": "<EventType>", "data": {...}, "ts": "<ISO UTC>", "request_id": "<opcional>"}
"""

from __future__ import annotations

import enum
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

log = logging.getLogger(__name__)


class ClientMessage(enum.StrEnum):
    LOCK_SLOT = "LOCK_SLOT"  # {room_id, date, start, end, booking_id?}  (+ request_id)
    UNLOCK_SLOT = "UNLOCK_SLOT"  # {lock_id}
    REFRESH_LOCK = "REFRESH_LOCK"  # {lock_id}
    PING = "PING"  # {}


class EventType(enum.StrEnum):
    # respostas diretas ao remetente
    HELLO = "HELLO"  # {user, instance_id, server_time, locks: [LockOut], online: [OnlineUser]}
    LOCK_ACQUIRED = "LOCK_ACQUIRED"  # {lock: LockOut}
    LOCK_DENIED = "LOCK_DENIED"  # {reason, message, conflicts: [LockOut]}
    LOCK_REFRESHED = "LOCK_REFRESHED"  # {lock: LockOut}
    PONG = "PONG"  # {}
    ERROR = "ERROR"  # {code, message}
    # broadcast para todas as conexões de todas as instâncias
    SLOT_LOCKED = "SLOT_LOCKED"  # {lock: LockOut}
    SLOT_UNLOCKED = "SLOT_UNLOCKED"  # {lock: LockOut, reason: released|saved|expired|disconnected}
    BOOKING_CREATED = "BOOKING_CREATED"  # {booking: BookingOut}
    BOOKING_UPDATED = "BOOKING_UPDATED"  # {booking: BookingOut, previous: {room_id, date, start, end}}
    BOOKING_CANCELLED = "BOOKING_CANCELLED"  # {booking: BookingOut}
    BOOKINGS_IMPORTED = "BOOKINGS_IMPORTED"  # {dates: ["YYYY-MM-DD", ...], count}
    ROOM_CREATED = "ROOM_CREATED"  # {room: RoomOut}
    ROOM_UPDATED = "ROOM_UPDATED"  # {room: RoomOut}
    PRESENCE = "PRESENCE"  # {online: [OnlineUser]}


def make_envelope(event_type: str, data: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {
        "type": str(event_type),
        "data": data,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if request_id:
        env["request_id"] = request_id
    return env


class EventBus(Protocol):
    async def publish(self, envelope: dict[str, Any]) -> None:
        """Entrega o envelope a TODAS as conexões de TODAS as instâncias (inclusive esta)."""
        ...


_bus: EventBus | None = None


def set_event_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus


def get_event_bus() -> EventBus | None:
    return _bus


async def publish(event_type: EventType | str, data: dict[str, Any]) -> None:
    """Chamado pelos serviços DEPOIS do commit. Nunca levanta exceção (falha só é logada)."""
    bus = _bus
    if bus is None:
        return
    try:
        await bus.publish(make_envelope(event_type, data))
    except Exception:  # pragma: no cover - não pode derrubar a requisição REST
        log.exception("Falha ao publicar evento %s", event_type)
