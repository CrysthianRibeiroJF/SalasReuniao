"""Várias instâncias (processos uvicorn separados) compartilhando um Redis (fakeredis via TCP).

LockStore/EventBus são globais por processo, por isso cada instância roda em seu próprio processo
(tests/realtime_server.py). O Redis falso roda numa thread deste processo.
"""

from __future__ import annotations

import asyncio
import time as clock
from contextlib import suppress
from dataclasses import dataclass, field

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from tests.realtime_support import (
    FakeRedisTcp,
    ServerProcess,
    WsClient,
    http_client,
    lock_data,
    token_for,
)

D = "2026-10-26"
LOCK_TTL = 3
PRESENCE_TTL = 1.0
RNF01_SECONDS = 0.2


def instance_env(fake: FakeRedisTcp, name: str) -> dict[str, str]:
    return {
        "AGENDA_REDIS_URL": fake.url,
        "AGENDA_INSTANCE_ID": name,
        "AGENDA_LOCK_TTL_SECONDS": str(LOCK_TTL),
        "AGENDA_TEST_SWEEP_INTERVAL": "0.2",
        "AGENDA_TEST_HEARTBEAT_INTERVAL": "0.3",
        "AGENDA_TEST_PRESENCE_TTL": str(PRESENCE_TTL),
    }


def assert_clean_shutdown(proc: ServerProcess, output: str) -> None:
    assert proc.returncode == 0, output
    assert "LEFTOVER_TASKS=[]" in output, output
    for marker in ("Traceback", "Task was destroyed", "ResourceWarning", "ERROR"):
        assert marker not in output, output


@pytest.fixture(scope="module")
def fake_redis():
    with FakeRedisTcp() as fake:
        yield fake


@pytest.fixture(scope="module")
def instances(fake_redis):
    procs = [ServerProcess(name, instance_env(fake_redis, name)) for name in ("inst-a", "inst-b")]
    ServerProcess.start_all(procs)
    try:
        yield procs
    finally:
        outputs = [proc.stop() for proc in procs]
    for proc, output in zip(procs, outputs):
        assert_clean_shutdown(proc, output)


@pytest.fixture(scope="module")
async def http_clients(instances):
    # Criar um httpx.AsyncClient custa centenas de ms no Windows (contexto TLS): reaproveita.
    clients = {proc.name: http_client(proc.base) for proc in instances}
    yield clients
    for client in clients.values():
        await client.aclose()


@dataclass
class Cluster:
    a: ServerProcess
    b: ServerProcess
    http: dict[str, httpx.AsyncClient]
    clients: list[WsClient] = field(default_factory=list)

    async def connect(self, proc: ServerProcess, user) -> WsClient:
        client = await WsClient.connect(proc.base, token_for(user))
        self.clients.append(client)
        return client

    async def state(self, proc: ServerProcess) -> dict:
        response = await self.http[proc.name].get("/test/state")
        response.raise_for_status()
        return response.json()


@pytest.fixture
async def cluster(instances, http_clients):
    cluster = Cluster(*instances, http=http_clients)
    yield cluster
    for client in cluster.clients:
        with suppress(Exception):
            await client.close()
    # Próximo teste começa com as instâncias ociosas (sem conexões, travas nem presença).
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCK_TTL + 5
    while True:
        states = [await cluster.state(proc) for proc in instances]
        if all(s["connections"] == 0 and not s["locks"] and not s["online"] for s in states):
            break
        assert loop.time() < deadline, states
        await asyncio.sleep(0.1)


def lock_of(msg: dict) -> dict:
    return msg["data"]["lock"]


def online_ids(msg: dict) -> dict[int, int]:
    return {u["id"]: u["connections"] for u in msg["data"]["online"]}


async def test_instances_share_one_redis(cluster):
    states = [await cluster.state(proc) for proc in (cluster.a, cluster.b)]
    assert [s["backend"] for s in states] == ["redis", "redis"]
    assert [s["instance_id"] for s in states] == ["inst-a", "inst-b"]


async def test_lock_on_a_is_visible_and_enforced_on_b(cluster, receptionist, receptionist2,
                                                      make_user, video_room):
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    assert (a.hello["data"]["instance_id"], b.hello["data"]["instance_id"]) == ("inst-a", "inst-b")

    started = clock.perf_counter()
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"), "a1")
    lock = lock_of(await a.expect("LOCK_ACQUIRED"))
    assert lock_of(await b.expect("SLOT_LOCKED")) == lock
    elapsed = clock.perf_counter() - started
    assert elapsed < RNF01_SECONDS, elapsed  # RNF01 entre instâncias

    await b.send("LOCK_SLOT", lock_data(video_room.id, D, "14:30", "15:30"), "b1")
    denied = await b.expect("LOCK_DENIED")
    assert denied["request_id"] == "b1"
    assert denied["data"]["reason"] == "LOCKED_BY_OTHER"
    assert denied["data"]["conflicts"] == [lock]
    assert denied["data"]["message"] == "Ana Recepção está reservando este horário agora."

    # quem conecta depois em B recebe a trava de A no HELLO
    carla = await make_user("carla", "Carla Recepção")
    late = await cluster.connect(cluster.b, carla)
    assert late.hello["data"]["locks"] == [lock]

    # dona libera em A -> todos veem em B
    await a.send("UNLOCK_SLOT", {"lock_id": lock["lock_id"]})
    for client in (b, late):
        assert (await client.expect("SLOT_UNLOCKED"))["data"] == {"lock": lock, "reason": "released"}


async def test_publish_on_a_reaches_every_instance_once(cluster, receptionist, receptionist2):
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    booking = {"id": "b-1", "room_id": 3, "date": D, "start": "09:00", "end": "10:00"}
    started = clock.perf_counter()
    response = await cluster.http[cluster.a.name].post(
        "/test/publish", json={"type": "BOOKING_CREATED", "data": {"booking": booking}}
    )
    assert response.status_code == 200
    for client in (a, b):
        msg = await client.expect("BOOKING_CREATED")
        assert msg["data"] == {"booking": booking}
    assert clock.perf_counter() - started < RNF01_SECONDS
    for client in (a, b):
        assert await client.count("BOOKING_CREATED", within=0.3) == 0  # sem duplicatas


async def test_disconnect_on_a_releases_lock_for_b(cluster, receptionist, receptionist2,
                                                   normal_room):
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    await a.send("LOCK_SLOT", lock_data(normal_room.id, D, "10:00", "11:00"))
    lock = lock_of(await a.expect("LOCK_ACQUIRED"))
    await b.expect("SLOT_LOCKED")
    await a.close()
    released = await b.expect("SLOT_UNLOCKED")
    assert released["data"] == {"lock": lock, "reason": "disconnected"}
    await b.expect("PRESENCE", where=lambda m: online_ids(m) == {receptionist2.id: 1})


async def test_expiry_is_announced_exactly_once_across_instances(cluster, receptionist,
                                                                 receptionist2, video_room):
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "16:00", "16:30"))
    lock = lock_of(await a.expect("LOCK_ACQUIRED"))
    for client in (a, b):
        expired = await client.expect("SLOT_UNLOCKED", timeout=LOCK_TTL + 3)
        assert expired["data"] == {"lock": lock, "reason": "expired"}
    # duas varreduras rodando (uma por instância) e mesmo assim um único anúncio
    for client in (a, b):
        assert await client.count("SLOT_UNLOCKED", within=0.6) == 0


async def test_presence_is_aggregated_across_instances(cluster, receptionist, receptionist2):
    ana, bia = receptionist.id, receptionist2.id
    a = await cluster.connect(cluster.a, receptionist)
    b = await cluster.connect(cluster.b, receptionist2)
    assert online_ids(b.hello) == {ana: 1, bia: 1}
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 1, bia: 1})

    second_tab = await cluster.connect(cluster.b, receptionist)
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 2, bia: 1})
    await second_tab.close()
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 1, bia: 1})
    await b.close()
    await a.expect("PRESENCE", where=lambda m: online_ids(m) == {ana: 1})


async def test_dead_instance_users_and_locks_go_away(fake_redis, cluster, receptionist, make_user,
                                                     video_room):
    carla = await make_user("carla", "Carla Recepção")
    dead = ServerProcess("inst-c", instance_env(fake_redis, "inst-c")).start()
    try:
        c = await WsClient.connect(dead.base, token_for(carla))
        a = await cluster.connect(cluster.a, receptionist)
        assert carla.id in online_ids(a.hello)
        await c.send("LOCK_SLOT", lock_data(video_room.id, D, "17:00", "18:00"))
        lock = lock_of(await c.expect("LOCK_ACQUIRED"))
        await a.expect("SLOT_LOCKED", where=lambda m: lock_of(m)["lock_id"] == lock["lock_id"])

        dead.kill()  # sem shutdown: nada é liberado pela instância C
        await a.expect(
            "PRESENCE", where=lambda m: carla.id not in online_ids(m), timeout=PRESENCE_TTL + 3
        )
        expired = await a.expect("SLOT_UNLOCKED", timeout=LOCK_TTL + 3)
        assert expired["data"] == {"lock": lock, "reason": "expired"}  # varrida por A ou B
        with suppress(Exception):
            await c.close()
    finally:
        dead.kill()


async def test_graceful_shutdown_closes_with_1012_and_releases(fake_redis, cluster, receptionist,
                                                               receptionist2, video_room):
    leaving = ServerProcess("inst-d", instance_env(fake_redis, "inst-d")).start()
    try:
        d = await WsClient.connect(leaving.base, token_for(receptionist))
        b = await cluster.connect(cluster.b, receptionist2)
        await d.send("LOCK_SLOT", lock_data(video_room.id, D, "12:00", "13:00"))
        lock = lock_of(await d.expect("LOCK_ACQUIRED"))
        await b.expect("SLOT_LOCKED")

        output = await asyncio.to_thread(leaving.stop)
        with pytest.raises(ConnectionClosed) as exc:
            while True:
                await asyncio.wait_for(d.ws.recv(), 5)
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 1012
        released = await b.expect("SLOT_UNLOCKED")
        assert released["data"] == {"lock": lock, "reason": "disconnected"}
        await b.expect("PRESENCE", where=lambda m: online_ids(m) == {receptionist2.id: 1})
        assert_clean_shutdown(leaving, output)
    finally:
        leaving.kill()
