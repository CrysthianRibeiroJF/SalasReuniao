"""ConnectionHub (filas por conexão, cliente lento) e presença agregada."""

from __future__ import annotations

import asyncio
import json

import fakeredis
import pytest

from app.realtime.events import EventType, make_envelope
from app.realtime.hub import CLOSE_TOO_SLOW, ConnectionHub
from app.realtime.presence import MemoryPresence, RedisPresence, merge_online
from app.schemas import OnlineUser


class FakeSocket:
    def __init__(self, *, blocked: bool = False) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str | None] | None = None
        self.unblock = asyncio.Event()
        if not blocked:
            self.unblock.set()
        self.got_message = asyncio.Event()

    async def send_text(self, data: str) -> None:
        await self.unblock.wait()
        self.sent.append(json.loads(data))
        self.got_message.set()

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def wait_until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condição não satisfeita a tempo")
        await asyncio.sleep(0.005)


def realtime_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("realtime:") and not t.done()]


async def test_hello_goes_first_then_queued_broadcasts():
    hub = ConnectionHub()
    ws = FakeSocket()
    conn = hub.add(ws, user_id=1, user_name="Ana")
    hub.broadcast(make_envelope(EventType.SLOT_LOCKED, {"n": 1}))
    hub.broadcast(make_envelope(EventType.SLOT_LOCKED, {"n": 2}))
    await asyncio.sleep(0.02)
    assert ws.sent == []  # nada sai antes do HELLO
    conn.start(json.dumps(make_envelope(EventType.HELLO, {})))
    await wait_until(lambda: len(ws.sent) == 3)
    assert ws.types() == ["HELLO", "SLOT_LOCKED", "SLOT_LOCKED"]
    assert [m["data"].get("n") for m in ws.sent[1:]] == [1, 2]
    assert hub.remove(conn) and not hub.remove(conn)
    await conn.stop()
    await hub.aclose()
    assert realtime_tasks() == []


async def test_broadcast_and_send_to():
    hub = ConnectionHub()
    sockets = [FakeSocket() for _ in range(3)]
    conns = [hub.add(ws, user_id=i, user_name=f"U{i}") for i, ws in enumerate(sockets, 1)]
    for conn in conns:
        conn.start(json.dumps({"type": "HELLO"}))
    assert hub.broadcast(make_envelope(EventType.ROOM_UPDATED, {"room": {"id": 1}})) == 3
    assert hub.send_to(conns[1].conn_id, make_envelope(EventType.PONG, {}))
    assert not hub.send_to("desconhecida", make_envelope(EventType.PONG, {}))
    await wait_until(lambda: all(len(ws.sent) >= 2 for ws in sockets) and len(sockets[1].sent) == 3)
    assert [ws.types() for ws in sockets] == [
        ["HELLO", "ROOM_UPDATED"], ["HELLO", "ROOM_UPDATED", "PONG"], ["HELLO", "ROOM_UPDATED"],
    ]
    await hub.aclose()


async def test_slow_client_never_blocks_others_and_is_closed_on_overflow():
    hub = ConnectionHub(queue_size=5)
    slow, fast = FakeSocket(blocked=True), FakeSocket()
    slow_conn = hub.add(slow, user_id=1, user_name="Lenta")
    fast_conn = hub.add(fast, user_id=2, user_name="Rápida")
    slow_conn.start(json.dumps({"type": "HELLO"}))
    fast_conn.start(json.dumps({"type": "HELLO"}))
    for n in range(20):
        hub.broadcast(make_envelope(EventType.SLOT_LOCKED, {"n": n}))
        await asyncio.sleep(0)
    await wait_until(lambda: len(fast.sent) == 21, timeout=1.0)
    await wait_until(lambda: slow.closed is not None, timeout=1.0)
    assert slow.closed[0] == CLOSE_TOO_SLOW
    assert slow_conn.closed and not fast_conn.closed
    assert slow.sent == []  # a escritora presa foi cancelada
    # o rápido continua recebendo normalmente
    hub.broadcast(make_envelope(EventType.SLOT_UNLOCKED, {}))
    await wait_until(lambda: fast.types()[-1] == "SLOT_UNLOCKED")
    await hub.aclose()
    assert realtime_tasks() == []


async def test_failed_send_marks_connection_closed():
    class BrokenSocket(FakeSocket):
        async def send_text(self, data: str) -> None:
            raise ConnectionError("cliente sumiu")

    hub = ConnectionHub()
    conn = hub.add(BrokenSocket(), user_id=1, user_name="Ana")
    conn.start(json.dumps({"type": "HELLO"}))
    await wait_until(lambda: conn.closed)
    assert hub.broadcast(make_envelope(EventType.PONG, {})) == 0
    await hub.aclose()


async def test_local_users_counts_connections_per_user():
    hub = ConnectionHub()
    hub.add(FakeSocket(), user_id=1, user_name="Ana")
    hub.add(FakeSocket(), user_id=1, user_name="Ana")
    bia = hub.add(FakeSocket(), user_id=2, user_name="Bia")
    users = {u.id: u.connections for u in hub.local_users()}
    assert users == {1: 2, 2: 1}
    hub.remove(bia)
    assert {u.id: u.connections for u in hub.local_users()} == {1: 2}
    assert len(hub) == 2


async def test_presence_broadcast_is_remembered():
    hub = ConnectionHub()
    online = [{"id": 1, "full_name": "Ana", "connections": 1}]
    hub.broadcast(make_envelope(EventType.PRESENCE, {"online": online}))
    assert hub.last_online == online


async def test_wait_empty():
    hub = ConnectionHub()
    assert await hub.wait_empty(0.01)
    conn = hub.add(FakeSocket(), user_id=1, user_name="Ana")
    assert not await hub.wait_empty(0.05)
    asyncio.get_running_loop().call_later(0.05, hub.remove, conn)
    assert await hub.wait_empty(1.0)


# ---------------------------------------------------------------------- presença


def test_merge_online_sums_connections_and_sorts():
    merged = merge_online(
        [
            [OnlineUser(id=2, full_name="bia", connections=1), OnlineUser(id=1, full_name="Ana")],
            [OnlineUser(id=2, full_name="bia", connections=2), OnlineUser(id=3, full_name="Álvaro")],
        ]
    )
    # ordem por nome sem acento nem caixa: Álvaro, Ana, bia
    assert [(u.id, u.connections) for u in merged] == [(3, 1), (1, 1), (2, 3)]


async def test_memory_presence():
    presence = MemoryPresence()
    await presence.publish_local([OnlineUser(id=1, full_name="Ana", connections=2)])
    assert [u.model_dump() for u in await presence.online()] == [
        {"id": 1, "full_name": "Ana", "connections": 2}
    ]
    await presence.clear()
    assert await presence.online() == []


@pytest.fixture
async def redis_pair():
    server = fakeredis.FakeServer()
    clients = [fakeredis.FakeAsyncRedis(server=server, decode_responses=True) for _ in range(2)]
    yield clients
    for client in clients:
        await client.aclose()


async def test_redis_presence_aggregates_nodes(redis_pair):
    a = RedisPresence(redis_pair[0], "node-a", ttl_seconds=30)
    b = RedisPresence(redis_pair[1], "node-b", ttl_seconds=30)
    await a.publish_local([OnlineUser(id=1, full_name="Ana", connections=2)])
    await b.publish_local(
        [OnlineUser(id=1, full_name="Ana", connections=1), OnlineUser(id=2, full_name="Bia")]
    )
    expected = [
        {"id": 1, "full_name": "Ana", "connections": 3},
        {"id": 2, "full_name": "Bia", "connections": 1},
    ]
    assert [u.model_dump() for u in await a.online()] == expected
    assert [u.model_dump() for u in await b.online()] == expected
    await b.publish_local([])  # todas as conexões de B saíram
    assert [u.model_dump() for u in await a.online()] == [
        {"id": 1, "full_name": "Ana", "connections": 2}
    ]
    await a.clear()
    assert await b.online() == []


async def test_redis_presence_dead_node_vanishes_after_ttl(redis_pair):
    a = RedisPresence(redis_pair[0], "node-a", ttl_seconds=0.3)
    b = RedisPresence(redis_pair[1], "node-b", ttl_seconds=0.3)
    await a.publish_local([OnlineUser(id=1, full_name="Ana")])
    await b.publish_local([OnlineUser(id=2, full_name="Bia")])
    assert {u.id for u in await a.online()} == {1, 2}
    await asyncio.sleep(0.2)
    await a.publish_local([OnlineUser(id=1, full_name="Ana")])  # heartbeat só de A
    await asyncio.sleep(0.2)
    assert {u.id for u in await a.online()} == {1}
    assert await redis_pair[0].zrange("presence:nodes", 0, -1) == ["node-a"]


# ---------------------------------------------------------------------- salas


class _FakeSession:
    def __init__(self, rows, gate: asyncio.Event | None = None) -> None:
        self.rows = rows
        self.gate = gate
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        rows = self.rows

        class _Result:
            def all(self):
                return rows

        return _Result()


async def test_room_directory_staleness_and_invalidation():
    from app.realtime.rooms import RoomDirectory

    rooms = RoomDirectory(ttl=60)
    assert rooms.stale
    await rooms.load(_FakeSession([(1, True), (2, False)]))
    assert not rooms.stale
    assert await rooms.is_active(1) is True and await rooms.is_active(2) is False
    rooms.observe(make_envelope(EventType.BOOKING_CREATED, {}))
    assert not rooms.stale
    rooms.observe(make_envelope(EventType.ROOM_UPDATED, {"room": {"id": 2}}))
    assert rooms.stale

    # invalidação durante uma carga em andamento não se perde
    gate = asyncio.Event()
    session = _FakeSession([(1, True)], gate)
    loading = asyncio.create_task(rooms.load(session))
    await asyncio.sleep(0)
    rooms.invalidate()
    gate.set()
    await loading
    assert rooms.stale


def _room_event(event_type: EventType, room_id: int, active: bool) -> dict:
    return make_envelope(event_type, {"room": {"id": room_id, "name": "X", "is_active": active}})


async def test_room_directory_applies_room_events_without_reloading():
    from app.realtime.rooms import RoomDirectory

    rooms = RoomDirectory(ttl=60, miss_reload_interval=60)
    await rooms.load(_FakeSession([(1, True)]))
    rooms.observe(_room_event(EventType.ROOM_UPDATED, 1, False))
    rooms.observe(_room_event(EventType.ROOM_CREATED, 2, True))
    assert not rooms.stale  # nada a recarregar: is_active() não vai ao banco
    assert await rooms.is_active(1) is False and await rooms.is_active(2) is True
    # payload incompleto (sem is_active) ou id inválido: invalida e recarrega no próximo uso
    for room in ({"id": 1}, {"id": True, "is_active": True}, None):
        rooms = RoomDirectory(ttl=60)
        await rooms.load(_FakeSession([(1, True)]))
        rooms.observe(make_envelope(EventType.ROOM_UPDATED, {"room": room}))
        assert rooms.stale, room


async def test_room_directory_keeps_events_that_arrive_during_a_load():
    from app.realtime.rooms import RoomDirectory

    rooms = RoomDirectory(ttl=60)
    gate = asyncio.Event()
    loading = asyncio.create_task(rooms.load(_FakeSession([(1, True), (2, True)], gate)))
    await asyncio.sleep(0)
    # o retrato da consulta é anterior a este evento: o evento vence
    rooms.observe(_room_event(EventType.ROOM_UPDATED, 2, False))
    gate.set()
    await loading
    assert not rooms.stale
    assert await rooms.is_active(1) is True and await rooms.is_active(2) is False


async def test_room_directory_older_load_never_overwrites_a_newer_one():
    from app.realtime.rooms import RoomDirectory

    rooms = RoomDirectory(ttl=60)
    old_gate = asyncio.Event()
    old = asyncio.create_task(rooms.load(_FakeSession([(1, True)], old_gate)))  # ex.: no HELLO
    await asyncio.sleep(0)
    rooms.observe(_room_event(EventType.ROOM_UPDATED, 1, False))
    await rooms.load(_FakeSession([(1, False), (2, True)]))  # carga nova termina antes
    old_gate.set()
    await old
    assert await rooms.is_active(1) is False and await rooms.is_active(2) is True


async def test_hub_observers_see_every_broadcast():
    hub = ConnectionHub()
    seen: list[str] = []
    hub.observe(lambda env: seen.append(env["type"]))
    hub.observe(lambda env: 1 / 0)  # um observador quebrado não impede a entrega
    hub.broadcast(make_envelope(EventType.ROOM_CREATED, {}))
    hub.broadcast(make_envelope(EventType.PRESENCE, {"online": []}))
    assert seen == ["ROOM_CREATED", "PRESENCE"]


async def test_direct_replies_also_respect_the_queue_limit():
    hub = ConnectionHub(queue_size=2)
    slow = FakeSocket(blocked=True)
    conn = hub.add(slow, user_id=1, user_name="Lenta")
    conn.start(json.dumps({"type": "HELLO"}))
    results = [conn.send(make_envelope(EventType.PONG, {})) for _ in range(4)]
    assert results[:2] == [True, True] and results[2:] == [False, False]
    await wait_until(lambda: slow.closed is not None, timeout=1.0)
    assert slow.closed[0] == CLOSE_TOO_SLOW
    assert not hub.send_to(conn.conn_id, make_envelope(EventType.PONG, {}))
    await hub.aclose()
    assert realtime_tasks() == []
