"""Travas no Redis (várias instâncias). Sem Lua, para funcionar também com fakeredis.

Chaves:
    lock:room_{id}:{YYYY-MM-DD}:{HH:MM}  -> lock_id               (PX = TTL; uma por slot)
    lockinfo:{lock_id}                   -> JSON da LockInfo       (PX = TTL + folga)
    locks:expiry                         ZSET lock_id -> expires_at (ms)
    locks:date:{YYYY-MM-DD}              SET de lock_ids do dia     (listagem sem SCAN)
    locks:conn:{conn_id}                 SET de lock_ids da conexão (release_connection)

Regra de posse: uma trava está viva enquanto for membro de locks:expiry. Todo caminho que a remove
(release, substituição, desconexão, expiração) faz ZREM primeiro; só quem recebe 1 limpa as chaves e
anuncia a liberação — assim cada trava é anunciada exatamente uma vez, mesmo com N instâncias.
Os metadados sobrevivem aos slots (folga) para que a varredura ainda consiga descrever a trava.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.realtime.codec import decode_lock, encode_lock
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
from app.timeutil import to_local

log = logging.getLogger(__name__)

EXPIRY_KEY = "locks:expiry"
META_GRACE_MS = 5 * 60 * 1000
_ACQUIRE_ATTEMPTS = 4
_WATCH_ATTEMPTS = 8
_POP_BATCH = 200
_MAX_SPAN_DAYS = 62


def meta_key(lock_id: str) -> str:
    return f"lockinfo:{lock_id}"


def date_key(d: date) -> str:
    return f"locks:date:{d.isoformat()}"


def conn_key(conn_id: str) -> str:
    return f"locks:conn:{conn_id}"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _ttl_ms() -> int:
    return max(1, int(lock_ttl().total_seconds() * 1000))


class RedisLockStore:
    def __init__(self, redis: Redis) -> None:
        if not redis.get_encoder().decode_responses:
            raise ValueError("RedisLockStore exige um cliente Redis com decode_responses=True")
        self._redis = redis

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
        replaced: list[LockInfo] = []
        for _ in range(_ACQUIRE_ATTEMPTS):
            ttl = _ttl_ms()
            info = LockInfo(
                lock_id=new_lock_id(),
                room_id=room_id,
                date=date,
                start=start,
                end=end,
                user_id=user_id,
                user_name=user_name,
                expires_at=utcnow() + lock_ttl(),
                conn_id=conn_id,
                booking_id=booking_id,
                slot_keys=keys,
            )
            # Metadados antes dos slots: quem encontrar um slot nosso sempre consegue ler o dono.
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.set(meta_key(info.lock_id), encode_lock(info), px=ttl + META_GRACE_MS)
                for key in keys:
                    pipe.set(key, info.lock_id, nx=True, px=ttl)
                results = await pipe.execute()
            taken = [key for key, ok in zip(keys, results[1:]) if ok]
            if len(taken) == len(keys):
                await self._index(info, ttl)
                return AcquireResult(info, [], replaced)

            blocked = [key for key, ok in zip(keys, results[1:]) if not ok]
            holder_ids = await self._redis.mget(blocked)
            # Tudo ou nada: devolve os slots obtidos nesta tentativa.
            await self._delete_owned(taken, info.lock_id)
            await self._redis.delete(meta_key(info.lock_id))

            holders = await self._load_many(h for h in holder_ids if h)
            now = utcnow()
            conflicts: list[LockInfo] = []
            same_user: list[LockInfo] = []
            stale: list[tuple[str, str]] = []
            for key, holder_id in zip(blocked, holder_ids):
                if holder_id is None:
                    continue  # liberado nesse meio tempo
                holder = holders.get(holder_id)
                if holder is None or holder.expires_at <= now:
                    stale.append((key, holder_id))
                elif holder.user_id == user_id:
                    same_user.append(holder)
                else:
                    conflicts.append(holder)
            if conflicts:
                return AcquireResult(None, sort_locks(unique_locks(conflicts)), replaced)
            for holder in unique_locks(same_user):
                if await self._claim(holder):
                    replaced.append(holder)
            for key, holder_id in stale:
                # Slot de trava já vencida (relógios diferentes) ou órfão: a varredura a anuncia.
                await self._delete_owned([key], holder_id)
        log.warning("Disputa ao travar sala %s em %s %s-%s; desistindo", room_id, date, start, end)
        return AcquireResult(None, [], replaced)

    async def release(self, lock_id: str, *, user_id: int | None = None) -> LockInfo | None:
        info = (await self._load_many([lock_id])).get(lock_id)
        if info is None or (user_id is not None and info.user_id != user_id):
            return None
        return info if await self._claim(info) else None

    async def refresh(self, lock_id: str, *, user_id: int) -> LockInfo | None:
        info = await self.get(lock_id)
        if info is None or info.user_id != user_id or not info.slot_keys:
            return None
        keys = list(info.slot_keys)
        for _ in range(_WATCH_ATTEMPTS):
            ttl = _ttl_ms()
            refreshed = replace(info, expires_at=utcnow() + lock_ttl())
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    # Vigiar os metadados também: se outro caminho já começou a limpar, aborta.
                    await pipe.watch(*keys, meta_key(lock_id))
                    if await pipe.mget(keys) != [lock_id] * len(keys):
                        return None
                    pipe.multi()
                    pipe.zadd(EXPIRY_KEY, {lock_id: _ms(refreshed.expires_at)}, xx=True)
                    pipe.zscore(EXPIRY_KEY, lock_id)
                    for key in keys:
                        pipe.pexpire(key, ttl)
                    pipe.set(meta_key(lock_id), encode_lock(refreshed), px=ttl + META_GRACE_MS)
                    pipe.pexpire(date_key(info.date), ttl + META_GRACE_MS)
                    if info.conn_id:
                        pipe.pexpire(conn_key(info.conn_id), ttl + META_GRACE_MS)
                    results = await pipe.execute()
            except WatchError:
                continue
            # ZSCORE vazio: a trava foi reivindicada (liberada/expirada) antes do EXEC; quem a
            # reivindicou limpa slots e metadados.
            return refreshed if results[1] is not None else None
        return None

    async def get(self, lock_id: str) -> LockInfo | None:
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.get(meta_key(lock_id))
            pipe.zscore(EXPIRY_KEY, lock_id)
            raw, score = await pipe.execute()
        info = decode_lock(raw)
        if info is None or score is None or info.expires_at <= utcnow():
            return None
        return info

    async def list_active(self, d: date | None = None) -> list[LockInfo]:
        now = utcnow()
        if d is None:
            ids = await self._redis.zrangebyscore(EXPIRY_KEY, f"({_ms(now)}", "+inf")
        else:
            ids = await self._redis.smembers(date_key(d))
        infos = await self._load_many(ids)
        return sort_locks(
            info
            for info in infos.values()
            if info.expires_at > now and (d is None or info.date == d)
        )

    async def find_overlapping(
        self, room_id: int, start_dt: datetime, end_dt: datetime
    ) -> list[LockInfo]:
        if end_dt <= start_dt:
            return []
        day = to_local(start_dt).date()
        last = to_local(end_dt - timedelta(microseconds=1)).date()
        if (last - day).days > _MAX_SPAN_DAYS:
            raise ValueError(f"Intervalo maior que {_MAX_SPAN_DAYS} dias")
        found: list[LockInfo] = []
        while day <= last:
            active = await self.list_active(day)
            found.extend(lock for lock in active if lock.overlaps(room_id, start_dt, end_dt))
            day += timedelta(days=1)
        return found

    async def release_connection(self, conn_id: str) -> list[LockInfo]:
        key = conn_key(conn_id)
        infos = await self._load_many(await self._redis.smembers(key))
        released = [info for info in sort_locks(infos.values()) if await self._claim(info)]
        await self._redis.delete(key)
        return released

    async def pop_expired(self) -> list[LockInfo]:
        expired: list[LockInfo] = []
        while True:
            ids = await self._redis.zrangebyscore(
                EXPIRY_KEY, "-inf", _ms(utcnow()), start=0, num=_POP_BATCH
            )
            if not ids:
                break
            async with self._redis.pipeline(transaction=False) as pipe:
                for lock_id in ids:
                    pipe.zrem(EXPIRY_KEY, lock_id)
                removed = await pipe.execute()
            # ZREM == 1: esta instância é a dona da expiração (as outras recebem 0).
            claimed = [lock_id for lock_id, n in zip(ids, removed) if n]
            infos = await self._load_many(claimed)
            for lock_id in claimed:
                info = infos.get(lock_id)
                if info is None:
                    continue  # metadados perdidos (instâncias paradas além da folga)
                await self._cleanup(info)
                expired.append(info)
            if len(ids) < _POP_BATCH:
                break
        return sort_locks(expired)

    # ------------------------------------------------------------------ internos

    async def _index(self, info: LockInfo, ttl: int) -> None:
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.zadd(EXPIRY_KEY, {info.lock_id: _ms(info.expires_at)})
            pipe.sadd(date_key(info.date), info.lock_id)
            pipe.pexpire(date_key(info.date), ttl + META_GRACE_MS)
            if info.conn_id:
                pipe.sadd(conn_key(info.conn_id), info.lock_id)
                pipe.pexpire(conn_key(info.conn_id), ttl + META_GRACE_MS)
            await pipe.execute()

    async def _claim(self, info: LockInfo) -> bool:
        if not await self._redis.zrem(EXPIRY_KEY, info.lock_id):
            return False
        await self._cleanup(info)
        return True

    async def _cleanup(self, info: LockInfo) -> None:
        await self._delete_owned(info.slot_keys, info.lock_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.srem(date_key(info.date), info.lock_id)
            if info.conn_id:
                pipe.srem(conn_key(info.conn_id), info.lock_id)
            pipe.delete(meta_key(info.lock_id))
            await pipe.execute()

    async def _delete_owned(self, keys: Sequence[str], owner: str) -> None:
        """Apaga apenas os slots que ainda pertencem a `owner` (compare-and-delete com WATCH)."""
        keys = list(keys)
        if not keys:
            return
        for _ in range(_WATCH_ATTEMPTS):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(*keys)
                    values = await pipe.mget(keys)
                    owned = [key for key, value in zip(keys, values) if value == owner]
                    if not owned:
                        return
                    pipe.multi()
                    pipe.delete(*owned)
                    await pipe.execute()
                    return
            except WatchError:
                continue
        log.warning("Não foi possível limpar os slots da trava %s (concorrência)", owner)

    async def _load_many(self, ids: Iterable[str]) -> dict[str, LockInfo]:
        unique = list(dict.fromkeys(ids))
        if not unique:
            return {}
        raws = await self._redis.mget([meta_key(lock_id) for lock_id in unique])
        return {
            lock_id: info
            for lock_id, raw in zip(unique, raws)
            if (info := decode_lock(raw)) is not None
        }
