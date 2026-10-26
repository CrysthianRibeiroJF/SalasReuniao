"""Travas em memória: válidas para UMA instância (desenvolvimento sem Redis).

Mesma semântica do RedisLockStore. Os métodos não têm `await` internos, então cada operação é
atômica dentro do loop de eventos (não é thread-safe: use a partir de um único loop).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time

from app.realtime.locks import (
    AcquireResult,
    lock_ttl,
    new_lock_id,
    slot_keys_for,
    sort_locks,
    unique_locks,
    utcnow,
)
from app.realtime.registry import LockInfo


class MemoryLockStore:
    def __init__(self) -> None:
        self._locks: dict[str, LockInfo] = {}
        # slot -> lock_id. Uma trava expirada (ainda não varrida) pode perder seus slots para uma
        # trava nova; pop_expired() ainda a anuncia e só limpa os slots que continuam dela.
        self._slots: dict[str, str] = {}

    async def acquire(self, **kwargs) -> tuple[LockInfo | None, list[LockInfo]]:
        result = await self.acquire_detailed(**kwargs)
        return result.lock, result.conflicts

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
    ) -> AcquireResult:
        keys = slot_keys_for(room_id, date, start, end)
        now = utcnow()
        holders = unique_locks(
            lock
            for key in keys
            if (lock := self._locks.get(self._slots.get(key, ""))) is not None
            and lock.expires_at > now
        )
        conflicts = [h for h in holders if h.user_id != user_id]
        if conflicts:
            return AcquireResult(None, sort_locks(conflicts))
        replaced = [lock for h in holders if (lock := self._remove(h.lock_id)) is not None]
        info = LockInfo(
            lock_id=new_lock_id(),
            room_id=room_id,
            date=date,
            start=start,
            end=end,
            user_id=user_id,
            user_name=user_name,
            expires_at=now + lock_ttl(),
            conn_id=conn_id,
            booking_id=booking_id,
            slot_keys=keys,
        )
        self._locks[info.lock_id] = info
        for key in keys:
            self._slots[key] = info.lock_id
        return AcquireResult(info, [], replaced)

    async def release(self, lock_id: str, *, user_id: int | None = None) -> LockInfo | None:
        info = self._locks.get(lock_id)
        if info is None or (user_id is not None and info.user_id != user_id):
            return None
        return self._remove(lock_id)

    async def refresh(self, lock_id: str, *, user_id: int) -> LockInfo | None:
        info = await self.get(lock_id)
        if info is None or info.user_id != user_id:
            return None
        refreshed = replace(info, expires_at=utcnow() + lock_ttl())
        self._locks[lock_id] = refreshed
        return refreshed

    async def get(self, lock_id: str) -> LockInfo | None:
        info = self._locks.get(lock_id)
        if info is None or info.expires_at <= utcnow():
            return None
        return info

    async def list_active(self, d: date | None = None) -> list[LockInfo]:
        now = utcnow()
        return sort_locks(
            lock
            for lock in self._locks.values()
            if lock.expires_at > now and (d is None or lock.date == d)
        )

    async def find_overlapping(
        self, room_id: int, start_dt: datetime, end_dt: datetime
    ) -> list[LockInfo]:
        if end_dt <= start_dt:
            return []
        active = await self.list_active()
        return [lock for lock in active if lock.overlaps(room_id, start_dt, end_dt)]

    async def release_connection(self, conn_id: str) -> list[LockInfo]:
        owned = [lock.lock_id for lock in self._locks.values() if lock.conn_id == conn_id]
        return sort_locks(lock for lock_id in owned if (lock := self._remove(lock_id)) is not None)

    async def pop_expired(self) -> list[LockInfo]:
        now = utcnow()
        expired = [lock.lock_id for lock in self._locks.values() if lock.expires_at <= now]
        return sort_locks(
            lock for lock_id in expired if (lock := self._remove(lock_id)) is not None
        )

    def _remove(self, lock_id: str) -> LockInfo | None:
        info = self._locks.pop(lock_id, None)
        if info is not None:
            for key in info.slot_keys:
                if self._slots.get(key) == lock_id:
                    del self._slots[key]
        return info
