"""Apoio aos testes do núcleo (serviços REST): fakes de tempo real e atalhos de requisição.

As fixtures que registram os fakes ficam em tests/core_fixtures.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx

from app.models import User
from app.realtime.registry import LockInfo, slot_key
from app.timeutil import local_dt, slot_starts

D = date(2026, 10, 26)  # segunda-feira


def booking_payload(room, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "room_id": room.id,
        "date": D.isoformat(),
        "start": "14:00",
        "end": "15:00",
        "ticket_number": "48211",
        "requester_name": "Fulano de Tal",
        "title": "Reunião de alinhamento",
    }
    payload.update(overrides)
    return payload


async def create_booking(
    client: httpx.AsyncClient, headers: dict[str, str], room, **overrides: Any
) -> dict[str, Any]:
    """POST /api/bookings que precisa dar certo; devolve o BookingOut."""
    resp = await client.post(
        "/api/bookings", json=booking_payload(room, **overrides), headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["booking"]


def error_code(resp: httpx.Response) -> str:
    return resp.json()["detail"]["code"]


class RecordingBus:
    """EventBus que guarda os envelopes publicados (opcionalmente chama um callback)."""

    def __init__(self) -> None:
        self.envelopes: list[dict[str, Any]] = []
        self.on_publish: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def publish(self, envelope: dict[str, Any]) -> None:
        if self.on_publish is not None:
            await self.on_publish(envelope)
        self.envelopes.append(envelope)

    @property
    def types(self) -> list[str]:
        return [e["type"] for e in self.envelopes]

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e["data"] for e in self.envelopes if e["type"] == event_type]

    def clear(self) -> None:
        self.envelopes.clear()


class FakeLockStore:
    """LockStore em memória mínimo (implementa o Protocol inteiro)."""

    def __init__(self) -> None:
        self.locks: dict[str, LockInfo] = {}
        self.fail = False  # simula indisponibilidade (ex.: Redis fora do ar)

    def add(
        self,
        user: User,
        room_id: int,
        start: time,
        end: time,
        *,
        d: date = D,
        booking_id: str | None = None,
    ) -> LockInfo:
        lock = LockInfo(
            lock_id=uuid.uuid4().hex,
            room_id=room_id,
            date=d,
            start=start,
            end=end,
            user_id=user.id,
            user_name=user.full_name,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
            booking_id=booking_id,
            slot_keys=tuple(slot_key(room_id, d, t) for t in slot_starts(d, start, end)),
        )
        self.locks[lock.lock_id] = lock
        return lock

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("lock store indisponível")

    async def acquire(
        self, *, room_id, date, start, end, user_id, user_name, conn_id=None, booking_id=None
    ):
        self._check()
        start_dt, end_dt = local_dt(date, start), local_dt(date, end)
        others = [
            lk
            for lk in self.locks.values()
            if lk.overlaps(room_id, start_dt, end_dt) and lk.user_id != user_id
        ]
        if others:
            return None, others
        for lk in list(self.locks.values()):
            if lk.overlaps(room_id, start_dt, end_dt):
                del self.locks[lk.lock_id]
        lock = LockInfo(
            lock_id=uuid.uuid4().hex,
            room_id=room_id,
            date=date,
            start=start,
            end=end,
            user_id=user_id,
            user_name=user_name,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
            conn_id=conn_id,
            booking_id=booking_id,
        )
        self.locks[lock.lock_id] = lock
        return lock, []

    async def release(self, lock_id: str, *, user_id: int | None = None) -> LockInfo | None:
        self._check()
        lock = self.locks.get(lock_id)
        if lock is None or (user_id is not None and lock.user_id != user_id):
            return None
        return self.locks.pop(lock_id)

    async def refresh(self, lock_id: str, *, user_id: int) -> LockInfo | None:
        self._check()
        return self.locks.get(lock_id)

    async def get(self, lock_id: str) -> LockInfo | None:
        self._check()
        return self.locks.get(lock_id)

    async def list_active(self, d: date | None = None) -> list[LockInfo]:
        self._check()
        return [lk for lk in self.locks.values() if d is None or lk.date == d]

    async def find_overlapping(self, room_id: int, start_dt: datetime, end_dt: datetime):
        self._check()
        return [lk for lk in self.locks.values() if lk.overlaps(room_id, start_dt, end_dt)]

    async def release_connection(self, conn_id: str) -> list[LockInfo]:
        released = [lk for lk in self.locks.values() if lk.conn_id == conn_id]
        for lk in released:
            del self.locks[lk.lock_id]
        return released

    async def pop_expired(self) -> list[LockInfo]:
        return []
