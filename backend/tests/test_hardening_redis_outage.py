"""Queda do Redis: quando a assinatura volta, os eventos do intervalo podem ter se perdido, então
cada instância fecha seus WebSockets com 1012 e os clientes reconectam e recarregam a grade."""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from app.config import settings
from app.realtime.bus import CHANNEL, RedisBus, encode_message
from app.realtime.events import EventType, make_envelope
from app.realtime.runtime import RESYNC_REASON
from tests.hardening_support import closed_with, wait_until
from tests.realtime_support import FakeRedisTcp, WsClient, lock_data, token_for
from tests.test_realtime_bus import RecordingHub
from tests.test_realtime_ws import D, realtime_tasks, run_server


async def tcp_client(url: str, **kwargs) -> Redis:
    return Redis.from_url(url, decode_responses=True, protocol=2, socket_connect_timeout=2,
                          **kwargs)


@pytest.fixture
def fake_redis():
    """FakeRedisTcp reiniciável na mesma porta (fake_redis.restart())."""

    class Restartable:
        def __init__(self) -> None:
            self.server = FakeRedisTcp().start()
            self.port = self.server.port
            self.url = self.server.url

        def stop(self) -> None:
            self.server.stop()

        def start(self) -> None:
            self.server = FakeRedisTcp(self.port).start()

        def restart(self) -> None:
            self.stop()
            self.start()

    fake = Restartable()
    yield fake
    fake.stop()


async def publish_from_outside(url: str, event_type: str) -> None:
    client = await tcp_client(url)
    try:
        await client.publish(CHANNEL, encode_message("outra", make_envelope(event_type, {})))
    finally:
        await client.aclose()


# ---------------------------------------------------------------------- RedisBus


@pytest.mark.parametrize("blocking", [False, True], ids=["restart-em-paralelo", "loop-parado"])
async def test_quick_redis_restart_triggers_exactly_one_resync(fake_redis, blocking):
    """Com novas tentativas automáticas, o redis-py refaz conexão e SUBSCRIBE em silêncio quando o
    Redis volta rápido, e os eventos do intervalo se perdem sem ninguém saber: por isso a conexão
    da assinatura roda sem elas e até uma reinicialização rápida chega ao laço do assinante."""
    retry = Retry(ExponentialBackoff(cap=0.2, base=0.02), 5)  # cliente que reconectaria sozinho
    client = await tcp_client(fake_redis.url, retry=retry)
    hub, calls = RecordingHub(), []
    bus = RedisBus(client, hub, node_id="a", min_backoff=0.05, max_backoff=0.2,
                   on_resubscribe=lambda: calls.append("resync"))
    await bus.start()
    try:
        assert bus.subscribed
        await asyncio.sleep(0.3)
        assert calls == []  # a primeira assinatura não conta

        if blocking:  # o loop só percebe a queda quando o Redis novo já está no ar
            fake_redis.restart()
        else:
            await asyncio.to_thread(fake_redis.restart)
        await wait_until(lambda: calls, timeout=15)
        await asyncio.sleep(0.5)
        assert calls == ["resync"]  # uma queda, um aviso

        await publish_from_outside(fake_redis.url, "ROOM_UPDATED")
        await hub.wait_for(1, timeout=5)  # a assinatura voltou de verdade
        assert hub.envelopes[0]["type"] == "ROOM_UPDATED"
    finally:
        await bus.stop()
    # as conexões devolvidas ao pool (usadas também pelas travas) mantêm as novas tentativas
    pool = client.connection_pool
    pooled = [*pool._available_connections, *pool._in_use_connections]
    assert pooled and all(conn.retry.get_retries() == 5 for conn in pooled)
    await client.aclose()
    assert realtime_tasks() == []


async def test_long_outage_resubscribes_and_triggers_resync_once(fake_redis):
    client = await tcp_client(fake_redis.url)
    calls = []
    bus = RedisBus(client, RecordingHub(), node_id="a", min_backoff=0.05, max_backoff=0.2,
                   on_resubscribe=lambda: calls.append("resync"))
    await bus.start()
    try:
        fake_redis.stop()
        await wait_until(lambda: not bus.subscribed, timeout=20)  # a falha chegou ao laço
        assert calls == []
        fake_redis.start()
        await wait_until(lambda: bus.subscribed and calls, timeout=15)
        await asyncio.sleep(0.5)
        assert calls == ["resync"]
    finally:
        await bus.stop()
        await client.aclose()


async def test_resubscribe_hook_errors_do_not_kill_the_subscriber(fake_redis):
    client = await tcp_client(fake_redis.url)
    hub = RecordingHub()

    def broken() -> None:
        raise RuntimeError("falha no tratamento")

    bus = RedisBus(client, hub, node_id="a", min_backoff=0.05, max_backoff=0.2,
                   on_resubscribe=broken)
    await bus.start()
    try:
        await asyncio.to_thread(fake_redis.restart)
        await asyncio.sleep(1.0)
        await wait_until(lambda: bus.subscribed, timeout=15)
        await publish_from_outside(fake_redis.url, "PONG")
        await hub.wait_for(1, timeout=5)
    finally:
        await bus.stop()
        await client.aclose()


# ---------------------------------------------------------------------- instância inteira


async def test_sockets_reconnect_with_1012_after_redis_comes_back(
    fake_redis, receptionist, receptionist2, video_room, monkeypatch
):
    monkeypatch.setattr(settings, "redis_url", fake_redis.url)
    async with run_server(heartbeat_interval=0.2, presence_ttl=1.0) as server:
        assert server.runtime.backend == "redis"
        a, b = await server.connect(receptionist), await server.connect(receptionist2)
        await asyncio.sleep(0.5)
        await a.send("PING")
        await a.expect("PONG")  # depois da primeira assinatura ninguém foi desconectado

        await asyncio.to_thread(fake_redis.restart)
        for client in (a, b):
            assert await closed_with(client, timeout=15) == (1012, RESYNC_REASON)

        # o frontend reconecta, recebe um HELLO novo e recarrega; tudo volta a funcionar
        a2 = await WsClient.connect(server.base, token_for(receptionist))
        b2 = await WsClient.connect(server.base, token_for(receptionist2))
        server.clients.extend([a2, b2])
        assert {u["id"] for u in b2.hello["data"]["online"]} == {receptionist.id, receptionist2.id}
        await a2.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"))
        lock = (await a2.expect("LOCK_ACQUIRED"))["data"]["lock"]
        assert (await b2.expect("SLOT_LOCKED"))["data"]["lock"] == lock
        await publish_from_outside(fake_redis.url, EventType.BOOKINGS_IMPORTED)
        await b2.expect("BOOKINGS_IMPORTED")  # eventos de outras instâncias chegam de novo
        assert len(server.runtime.hub) == 2
