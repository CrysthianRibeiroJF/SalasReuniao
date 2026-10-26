"""EventBus em memória e no Redis (pub/sub entre instâncias, reconexão, serialização)."""

from __future__ import annotations

import asyncio
import json
import time as clock
from datetime import date, datetime, time, timezone
from uuid import UUID

import fakeredis
import pytest
from redis.asyncio import Redis

from app.models import RoomType
from app.realtime import events
from app.realtime.bus import CHANNEL, MemoryBus, RedisBus
from app.realtime.codec import dumps
from app.realtime.events import EventType, make_envelope
from app.schemas import UserBrief
from tests.realtime_support import FakeRedisTcp


class RecordingHub:
    def __init__(self) -> None:
        self.envelopes: list[dict] = []
        self.changed = asyncio.Event()

    def broadcast(self, envelope: dict) -> int:
        self.envelopes.append(envelope)
        self.changed.set()
        return 1

    async def wait_for(self, count: int, timeout: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self.envelopes) < count:
            if loop.time() > deadline:
                raise AssertionError(f"esperava {count} envelopes, chegaram {len(self.envelopes)}")
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), max(0.0, deadline - loop.time()))
            except TimeoutError:
                pass


def realtime_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("realtime:") and not t.done()]


@pytest.fixture
async def fake_clients():
    server = fakeredis.FakeServer()
    clients: list[fakeredis.FakeAsyncRedis] = []

    def _make() -> fakeredis.FakeAsyncRedis:
        client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


async def test_memory_bus_delivers_locally_once():
    hub = RecordingHub()
    bus = MemoryBus(hub)
    await bus.start()
    envelope = make_envelope(EventType.ROOM_CREATED, {"room": {"id": 1}})
    await bus.publish(envelope)
    assert hub.envelopes == [envelope]
    await bus.stop()


async def test_events_publish_uses_registered_bus():
    hub = RecordingHub()
    events.set_event_bus(MemoryBus(hub))
    await events.publish(EventType.BOOKINGS_IMPORTED, {"dates": ["2026-10-26"], "count": 3})
    assert [(e["type"], e["data"]) for e in hub.envelopes] == [
        ("BOOKINGS_IMPORTED", {"dates": ["2026-10-26"], "count": 3})
    ]
    assert "ts" in hub.envelopes[0] and "request_id" not in hub.envelopes[0]


async def test_redis_bus_fans_out_without_local_duplicates(fake_clients):
    hub_a, hub_b = RecordingHub(), RecordingHub()
    bus_a = RedisBus(fake_clients(), hub_a, node_id="a")
    bus_b = RedisBus(fake_clients(), hub_b, node_id="b")
    await bus_a.start()
    await bus_b.start()
    try:
        assert bus_a.subscribed and bus_b.subscribed
        await bus_a.publish(make_envelope(EventType.BOOKING_CREATED, {"booking": {"id": "x"}}))
        await hub_b.wait_for(1)
        await bus_b.publish(make_envelope(EventType.BOOKING_CANCELLED, {"booking": {"id": "y"}}))
        await hub_a.wait_for(2)
        await asyncio.sleep(0.2)  # dá tempo de uma eventual cópia duplicada chegar
        assert [e["type"] for e in hub_a.envelopes] == ["BOOKING_CREATED", "BOOKING_CANCELLED"]
        assert [e["type"] for e in hub_b.envelopes] == ["BOOKING_CREATED", "BOOKING_CANCELLED"]
    finally:
        await bus_a.stop()
        await bus_b.stop()
    assert realtime_tasks() == []


async def test_redis_bus_normalizes_payload_like_the_rest_api(fake_clients):
    hub_a, hub_b = RecordingHub(), RecordingHub()
    bus_a = RedisBus(fake_clients(), hub_a, node_id="a")
    bus_b = RedisBus(fake_clients(), hub_b, node_id="b")
    await bus_a.start()
    await bus_b.start()
    try:
        data = {
            "booking": {
                "id": UUID("12345678-1234-5678-1234-567812345678"),
                "date": date(2026, 10, 26),
                "start": time(14, 0),
                "start_time": datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc),
                "type": RoomType.VIDEO,
                "created_by": UserBrief(id=1, full_name="Ana"),
            }
        }
        await bus_a.publish(make_envelope(EventType.BOOKING_CREATED, data))
        await hub_b.wait_for(1)
        assert hub_b.envelopes[0]["data"] == {
            "booking": {
                "id": "12345678-1234-5678-1234-567812345678",
                "date": "2026-10-26",
                "start": "14:00",
                "start_time": "2026-10-26T17:00:00+00:00",
                "type": "VIDEO",
                "created_by": {"id": 1, "full_name": "Ana"},
            }
        }
    finally:
        await bus_a.stop()
        await bus_b.stop()


async def test_redis_bus_ignores_garbage_on_the_channel(fake_clients):
    hub = RecordingHub()
    raw = fake_clients()
    bus = RedisBus(fake_clients(), hub, node_id="a")
    await bus.start()
    try:
        await raw.publish(CHANNEL, "isto não é json")
        await raw.publish(CHANNEL, json.dumps({"origin": "x"}))
        await raw.publish(CHANNEL, json.dumps({"origin": "x", "envelope": {"data": {}}}))
        await raw.publish(CHANNEL, dumps({"origin": "x", "envelope": make_envelope("PONG", {})}))
        await hub.wait_for(1)
        await asyncio.sleep(0.1)
        assert [e["type"] for e in hub.envelopes] == ["PONG"]
    finally:
        await bus.stop()


async def test_codec_rejects_unknown_types():
    with pytest.raises(TypeError):
        dumps({"x": object()})


# ---------------------------------------------------------------------- Redis real via TCP


async def tcp_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True, protocol=2, socket_connect_timeout=2)


async def test_redis_bus_over_tcp_is_fast():
    with FakeRedisTcp() as fake:
        clients = [await tcp_client(fake.url) for _ in range(2)]
        hub_a, hub_b = RecordingHub(), RecordingHub()
        bus_a = RedisBus(clients[0], hub_a, node_id="a")
        bus_b = RedisBus(clients[1], hub_b, node_id="b")
        await bus_a.start()
        await bus_b.start()
        try:
            latencies = []
            for n in range(5):
                started = clock.perf_counter()
                await bus_a.publish(make_envelope(EventType.SLOT_LOCKED, {"n": n}))
                await hub_b.wait_for(n + 1)
                latencies.append(clock.perf_counter() - started)
            assert [e["data"]["n"] for e in hub_b.envelopes] == list(range(5))
            assert max(latencies) < 0.2, latencies  # RNF01
        finally:
            await bus_a.stop()
            await bus_b.stop()
            for client in clients:
                await client.aclose()


async def test_redis_bus_reconnects_after_redis_restart():
    fake = FakeRedisTcp().start()
    port = fake.port
    clients = [await tcp_client(fake.url) for _ in range(2)]
    hub = RecordingHub()
    bus = RedisBus(clients[0], hub, node_id="a", min_backoff=0.05, max_backoff=0.2)
    await bus.start()
    try:
        assert bus.subscribed
        fake.stop()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while bus.subscribed:
            assert loop.time() < deadline, "a queda do Redis não foi percebida"
            await asyncio.sleep(0.05)

        fake = FakeRedisTcp(port).start()
        while not bus.subscribed:
            assert loop.time() < deadline + 10, "o assinante não reconectou"
            await asyncio.sleep(0.05)
        await clients[1].publish(
            CHANNEL, dumps({"origin": "b", "envelope": make_envelope("ROOM_UPDATED", {})})
        )
        await hub.wait_for(1)
        assert hub.envelopes[0]["type"] == "ROOM_UPDATED"
    finally:
        await bus.stop()
        for client in clients:
            await client.aclose()
        fake.stop()
    assert realtime_tasks() == []
