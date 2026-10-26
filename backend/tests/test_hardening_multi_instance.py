"""Duas instâncias da aplicação REAL (app.main:app, processos uvicorn separados) no mesmo banco de
teste e no mesmo Redis (fakeredis via TCP): REST numa instância, WebSocket na outra.

Diferente de test_realtime_multi.py (que sobe só o tempo real), aqui passam os serviços REST de
verdade: soft lock no Redis respeitado pelo POST, eventos após o commit, presença agregada.
"""

from __future__ import annotations

import time as clock
from contextlib import suppress
from dataclasses import dataclass, field

import httpx
import pytest
from redis.asyncio import Redis

from app.realtime.presence import RedisPresence
from app.realtime.redis_store import EXPIRY_KEY
from tests.conftest import auth_headers
from tests.core_support import booking_payload
from tests.hardening_support import AppServerProcess, assert_clean_shutdown, wait_until
from tests.realtime_support import FakeRedisTcp, ServerProcess, WsClient, http_client, lock_data

D = "2026-10-26"
RNF01_SECONDS = 0.2
ISSUED_TOKENS: list[str] = []  # todos os JWTs usados em /ws?token= (nenhum pode ir para o log)


def instance_env(fake: FakeRedisTcp, name: str) -> dict[str, str]:
    return {
        "AGENDA_REDIS_URL": fake.url,
        "AGENDA_INSTANCE_ID": name,
        "AGENDA_TEST_LOG_LEVEL": "info",  # log de acesso e "WebSocket ... [accepted]" ligados
    }


@pytest.fixture(scope="module")
def fake_redis():
    with FakeRedisTcp() as fake:
        yield fake


@pytest.fixture(scope="module")
def instances(fake_redis):
    procs = [AppServerProcess(name, instance_env(fake_redis, name)) for name in ("app-a", "app-b")]
    ServerProcess.start_all(procs)
    try:
        yield procs
    finally:
        outputs = [proc.stop() for proc in procs]
    for proc, output in zip(procs, outputs):
        assert_clean_shutdown(proc, output)


@pytest.fixture(scope="module")
async def http_clients(instances):
    clients = {proc.name: http_client(proc.base) for proc in instances}
    yield clients
    for client in clients.values():
        await client.aclose()


@pytest.fixture(scope="module")
async def redis(fake_redis):
    client = Redis.from_url(fake_redis.url, decode_responses=True, protocol=2)
    yield client
    await client.aclose()


@dataclass
class Cluster:
    a: AppServerProcess
    b: AppServerProcess
    http: dict[str, httpx.AsyncClient]
    clients: list[WsClient] = field(default_factory=list)

    async def connect(self, proc: AppServerProcess, user) -> WsClient:
        token = auth_headers(user)["Authorization"].removeprefix("Bearer ")
        ISSUED_TOKENS.append(token)
        client = await WsClient.connect(proc.base, token)
        self.clients.append(client)
        return client

    def api(self, proc: AppServerProcess) -> httpx.AsyncClient:
        return self.http[proc.name]


@pytest.fixture
async def cluster(instances, http_clients, redis):
    cluster = Cluster(*instances, http=http_clients)
    yield cluster
    for client in cluster.clients:
        with suppress(Exception):
            await client.close()
    # O próximo teste começa com as instâncias ociosas: sem travas e sem ninguém online.
    presence = RedisPresence(redis, "test-observer", ttl_seconds=30)

    async def idle() -> bool:
        return await redis.zcard(EXPIRY_KEY) == 0 and await presence.online() == []

    await wait_until(idle, timeout=10, interval=0.05)


def lock_of(msg: dict) -> dict:
    return msg["data"]["lock"]


def online_ids(msg: dict) -> dict[int, int]:
    return {u["id"]: u["connections"] for u in msg["data"]["online"]}


# ---------------------------------------------------------------------- testes


async def test_both_instances_use_redis(cluster):
    for proc in (cluster.a, cluster.b):
        health = (await cluster.api(proc).get("/api/health")).json()
        assert health == {"status": "ok", "database": True, "realtime": "redis", "redis": "ok"}


async def test_rest_booking_on_a_reaches_websocket_on_b_within_200ms(
    cluster, receptionist, receptionist2, video_room
):
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    assert (a.hello["data"]["instance_id"], b.hello["data"]["instance_id"]) == ("app-a", "app-b")

    resp = await cluster.api(cluster.a).post(
        "/api/bookings", json=booking_payload(video_room), headers=auth_headers(receptionist)
    )
    answered = clock.perf_counter()
    assert resp.status_code == 201, resp.text
    booking = resp.json()["booking"]
    created = await b.expect("BOOKING_CREATED")
    elapsed = clock.perf_counter() - answered
    assert elapsed < RNF01_SECONDS, elapsed  # RNF01 entre instâncias, contado da resposta HTTP
    assert created["data"] == {"booking": booking}  # o mesmo JSON da API REST
    assert (await a.expect("BOOKING_CREATED"))["data"] == {"booking": booking}
    for client in (a, b):
        assert await client.count("BOOKING_CREATED", within=0.3) == 0  # sem duplicatas


async def test_websocket_lock_on_b_blocks_rest_on_a_until_the_holder_leaves(
    cluster, receptionist, receptionist2, make_user, video_room
):
    carla = await make_user("carla", "Carla Recepção")
    holder = await cluster.connect(cluster.b, receptionist2)
    watcher_b = await cluster.connect(cluster.b, carla)
    ana_a = await cluster.connect(cluster.a, receptionist)
    await holder.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"), "h1")
    lock = lock_of(await holder.expect("LOCK_ACQUIRED"))
    assert lock_of(await ana_a.expect("SLOT_LOCKED")) == lock

    payload = booking_payload(video_room, start="14:30", end="15:30")
    api_a, headers = cluster.api(cluster.a), auth_headers(receptionist)
    resp = await api_a.post("/api/bookings", json=payload, headers=headers)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "SLOT_LOCKED"
    assert detail["message"] == "Bia Recepção está reservando este horário agora."
    assert [lk["lock_id"] for lk in detail["locks"]] == [lock["lock_id"]]

    await holder.close()  # aba fechada: a instância B libera as travas da conexão
    released = await watcher_b.expect("SLOT_UNLOCKED")
    assert released["data"] == {"lock": lock, "reason": "disconnected"}
    assert (await ana_a.expect("SLOT_UNLOCKED"))["data"] == released["data"]

    resp = await api_a.post("/api/bookings", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    booking_id = resp.json()["booking"]["id"]
    created = await watcher_b.expect("BOOKING_CREATED")
    assert created["data"]["booking"]["id"] == booking_id


async def test_patch_and_cancel_on_b_reach_clients_on_a(
    cluster, receptionist, receptionist2, video_room, normal_room
):
    api_b, bia = cluster.api(cluster.b), auth_headers(receptionist2)
    resp = await api_b.post("/api/bookings", json=booking_payload(video_room), headers=bia)
    assert resp.status_code == 201, resp.text
    booking = resp.json()["booking"]
    a = await cluster.connect(cluster.a, receptionist)

    resp = await api_b.patch(
        f"/api/bookings/{booking['id']}",
        json={"version": booking["version"], "room_id": normal_room.id},
        headers=bia,
    )
    assert resp.status_code == 200, resp.text
    moved = resp.json()["booking"]
    updated = await a.expect("BOOKING_UPDATED")
    assert updated["data"] == {
        "booking": moved,
        "previous": {"room_id": video_room.id, "date": D, "start": "14:00", "end": "15:00"},
    }

    resp = await api_b.post(
        f"/api/bookings/{booking['id']}/cancel",
        json={"version": moved["version"], "reason": "Reunião desmarcada"},
        headers=bia,
    )
    assert resp.status_code == 200, resp.text
    cancelled = await a.expect("BOOKING_CANCELLED")
    assert cancelled["data"] == {"booking": resp.json()}
    assert cancelled["data"]["booking"]["status"] == "CANCELLED"


async def test_presence_lists_users_of_both_instances(cluster, receptionist, receptionist2):
    ana, bia = receptionist.id, receptionist2.id
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    assert online_ids(b.hello) == {ana: 1, bia: 1}
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 1, bia: 1})

    second_tab = await cluster.connect(cluster.b, receptionist)
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 2, bia: 1})
    await second_tab.close()
    await b.close()
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 1})


async def test_websocket_tokens_never_reach_the_logs(cluster, receptionist):
    procs = (cluster.a, cluster.b)
    for proc in procs:
        client = await cluster.connect(proc, receptionist)
        await client.send("PING")
        await client.expect("PONG")

    async def accepted_logged() -> bool:
        return all('"WebSocket /ws?token=***" [accepted]' in proc.output for proc in procs)

    await wait_until(accepted_logged, timeout=5)  # o stdout chega pela thread leitora
    for proc in procs:
        leaked = [token for token in ISSUED_TOKENS if token in proc.output]
        assert leaked == [], f"JWT no log de {proc.name}"
