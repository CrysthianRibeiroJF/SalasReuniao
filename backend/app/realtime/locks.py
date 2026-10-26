"""Peças comuns às implementações de LockStore (memória e Redis)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from app.config import settings
from app.realtime.registry import LockInfo, LockStore, slot_key
from app.timeutil import slot_starts


@dataclass(frozen=True)
class AcquireResult:
    lock: LockInfo | None
    conflicts: list[LockInfo] = field(default_factory=list)
    # Travas do MESMO usuário substituídas pela nova seleção: o chamador anuncia SLOT_UNLOCKED.
    # Pode vir preenchida mesmo quando lock é None (substituiu e depois perdeu a corrida).
    replaced: list[LockInfo] = field(default_factory=list)


class DetailedLockStore(LockStore, Protocol):
    """LockStore que também informa quais travas foram substituídas (usado pelo WebSocket)."""

    async def acquire_detailed(
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
    ) -> AcquireResult: ...


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def lock_ttl() -> timedelta:
    # Lido a cada uso: os testes reduzem settings.lock_ttl_seconds em tempo de execução.
    return timedelta(seconds=settings.lock_ttl_seconds)


def new_lock_id() -> str:
    return uuid4().hex


def slot_keys_for(room_id: int, d: date, start: time, end: time) -> tuple[str, ...]:
    if end <= start:
        raise ValueError("O horário de término deve ser depois do início")
    return tuple(slot_key(room_id, d, t) for t in slot_starts(d, start, end))


def unique_locks(locks: Iterable[LockInfo]) -> list[LockInfo]:
    seen: dict[str, LockInfo] = {}
    for lock in locks:
        seen.setdefault(lock.lock_id, lock)
    return list(seen.values())


def sort_locks(locks: Iterable[LockInfo]) -> list[LockInfo]:
    return sorted(locks, key=lambda lk: (lk.date, lk.room_id, lk.start, lk.lock_id))
