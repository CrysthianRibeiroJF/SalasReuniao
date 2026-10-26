"""Interfaces do tempo real usadas pelos serviços REST.

Os serviços (app/services) dependem SOMENTE deste módulo e de app/realtime/events.py.
As implementações concretas (memória / Redis) vivem em outros arquivos de app/realtime e são
registradas no startup (app/realtime/setup.py) via set_lock_store()/events.set_event_bus().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Protocol, runtime_checkable

from app.schemas import LockOut, UserBrief
from app.timeutil import local_dt


@dataclass(frozen=True)
class LockInfo:
    """Soft lock de uma faixa [start, end) de uma sala em um dia (horas locais)."""

    lock_id: str
    room_id: int
    date: date
    start: time
    end: time
    user_id: int
    user_name: str
    expires_at: datetime  # aware (UTC)
    conn_id: str | None = None  # conexão WebSocket dona (liberada ao desconectar)
    booking_id: str | None = None  # trava de edição/remanejamento de uma reserva existente
    slot_keys: tuple[str, ...] = field(default=())  # chaves lock:room_{id}:{date}:{HH:MM}

    @property
    def start_dt(self) -> datetime:
        return local_dt(self.date, self.start)

    @property
    def end_dt(self) -> datetime:
        return local_dt(self.date, self.end)

    def overlaps(self, room_id: int, start_dt: datetime, end_dt: datetime) -> bool:
        return self.room_id == room_id and self.start_dt < end_dt and start_dt < self.end_dt

    def to_out(self) -> LockOut:
        return LockOut(
            lock_id=self.lock_id,
            room_id=self.room_id,
            date=self.date,
            start=self.start,
            end=self.end,
            user=UserBrief(id=self.user_id, full_name=self.user_name),
            expires_at=self.expires_at,
            booking_id=self.booking_id,
        )


def slot_key(room_id: int, d: date, t: time) -> str:
    """Formato do planejamento (5.1): lock:room_2:2026-10-23:14:00"""
    return f"lock:room_{room_id}:{d.isoformat()}:{t.strftime('%H:%M')}"


@runtime_checkable
class LockStore(Protocol):
    async def acquire(
        self,
        *,
        room_id: int,
        date: date,
        start: time,
        end: time,
        user_id: int,
        user_name: str,
        conn_id: str | None = None,
        booking_id: str | None = None,
    ) -> tuple[LockInfo | None, list[LockInfo]]:
        """Trava todos os slots de [start, end) atomicamente (tudo ou nada).

        Retorna (lock, []) em sucesso ou (None, travas_conflitantes) se outro usuário segura
        algum slot. Travas do MESMO usuário que se sobrepõem são substituídas (re-seleção).
        """
        ...

    async def release(self, lock_id: str, *, user_id: int | None = None) -> LockInfo | None:
        """Libera; se user_id for dado, só libera se for o dono. Retorna a trava liberada."""
        ...

    async def refresh(self, lock_id: str, *, user_id: int) -> LockInfo | None:
        """Renova o TTL (lock_ttl_seconds) se o usuário for o dono."""
        ...

    async def get(self, lock_id: str) -> LockInfo | None: ...

    async def list_active(self, d: date | None = None) -> list[LockInfo]: ...

    async def find_overlapping(
        self, room_id: int, start_dt: datetime, end_dt: datetime
    ) -> list[LockInfo]: ...

    async def release_connection(self, conn_id: str) -> list[LockInfo]:
        """Libera todas as travas de uma conexão WebSocket (abandono / queda)."""
        ...

    async def pop_expired(self) -> list[LockInfo]:
        """Remove e devolve travas expiradas (cada uma devolvida por UMA única instância)."""
        ...


class NullLockStore:
    """Padrão antes do startup e em testes que não envolvem travas: nada fica travado."""

    async def acquire(self, **kwargs) -> tuple[LockInfo | None, list[LockInfo]]:
        return None, []

    async def release(self, lock_id: str, *, user_id: int | None = None) -> LockInfo | None:
        return None

    async def refresh(self, lock_id: str, *, user_id: int) -> LockInfo | None:
        return None

    async def get(self, lock_id: str) -> LockInfo | None:
        return None

    async def list_active(self, d: date | None = None) -> list[LockInfo]:
        return []

    async def find_overlapping(self, room_id: int, start_dt: datetime, end_dt: datetime) -> list[LockInfo]:
        return []

    async def release_connection(self, conn_id: str) -> list[LockInfo]:
        return []

    async def pop_expired(self) -> list[LockInfo]:
        return []


_lock_store: LockStore = NullLockStore()


def get_lock_store() -> LockStore:
    return _lock_store


def set_lock_store(store: LockStore) -> None:
    global _lock_store
    _lock_store = store
