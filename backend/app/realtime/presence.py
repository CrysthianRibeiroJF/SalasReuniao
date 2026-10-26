"""Presença (recepcionistas online) agregada por usuário entre instâncias.

Cada instância publica um retrato das SUAS conexões; a lista online é a soma dos retratos.
No Redis cada retrato tem TTL renovado pelo heartbeat: se a instância morrer, seus usuários somem
sozinhos (TTL + um intervalo de heartbeat no pior caso).

    presence:node:{node_id}  HASH user_id -> {"id", "full_name", "connections"}  (PX = TTL)
    presence:nodes           ZSET node_id -> último heartbeat (ms)
"""

from __future__ import annotations

import json
import time
import unicodedata
from collections.abc import Iterable
from typing import Protocol

from redis.asyncio import Redis

from app.realtime.codec import dumps
from app.schemas import OnlineUser

NODES_KEY = "presence:nodes"


def node_key(node_id: str) -> str:
    return f"presence:node:{node_id}"


def merge_online(snapshots: Iterable[Iterable[OnlineUser]]) -> list[OnlineUser]:
    """Soma conexões do mesmo usuário vindas de várias instâncias; ordena por nome (sem acento)."""
    merged: dict[int, OnlineUser] = {}
    for snapshot in snapshots:
        for user in snapshot:
            current = merged.get(user.id)
            if current is None:
                merged[user.id] = user.model_copy()
            else:
                current.connections += user.connections
    return sorted(merged.values(), key=lambda u: (_sort_key(u.full_name), u.id))


def _sort_key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class PresenceStore(Protocol):
    async def publish_local(self, users: list[OnlineUser]) -> None:
        """Substitui o retrato desta instância (também serve de heartbeat)."""
        ...

    async def online(self) -> list[OnlineUser]: ...

    async def clear(self) -> None:
        """Remove o retrato desta instância (encerramento)."""
        ...


class MemoryPresence:
    def __init__(self) -> None:
        self._local: list[OnlineUser] = []

    async def publish_local(self, users: list[OnlineUser]) -> None:
        self._local = list(users)

    async def online(self) -> list[OnlineUser]:
        return merge_online([self._local])

    async def clear(self) -> None:
        self._local = []


class RedisPresence:
    def __init__(self, redis: Redis, node_id: str, *, ttl_seconds: float) -> None:
        self._redis = redis
        self._node_id = node_id
        self._ttl_ms = max(1, int(ttl_seconds * 1000))

    async def publish_local(self, users: list[OnlineUser]) -> None:
        key = node_key(self._node_id)
        mapping = {str(u.id): dumps(u.model_dump(mode="json")) for u in users}
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if mapping:
                pipe.hset(key, mapping=mapping)
                pipe.pexpire(key, self._ttl_ms)
            pipe.zadd(NODES_KEY, {self._node_id: _now_ms()})
            await pipe.execute()

    async def online(self) -> list[OnlineUser]:
        await self._redis.zremrangebyscore(NODES_KEY, "-inf", _now_ms() - self._ttl_ms)
        nodes = await self._redis.zrange(NODES_KEY, 0, -1)
        if not nodes:
            return []
        async with self._redis.pipeline(transaction=False) as pipe:
            for node in nodes:
                pipe.hgetall(node_key(node))
            hashes = await pipe.execute()
        return merge_online(_parse_snapshot(h) for h in hashes)

    async def clear(self) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(node_key(self._node_id))
            pipe.zrem(NODES_KEY, self._node_id)
            await pipe.execute()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_snapshot(raw: dict[str, str]) -> list[OnlineUser]:
    users: list[OnlineUser] = []
    for value in raw.values():
        try:
            users.append(OnlineUser.model_validate(json.loads(value)))
        except ValueError:
            continue
    return users
