"""WebSocket /ws de ponta a ponta: uvicorn real (no loop dos testes) + cliente `websockets`."""

from __future__ import annotations

import asyncio
import socket
import time as clock
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
import pytest
from websockets.exceptions import ConnectionClosed

from app.config import settings
from app.models import RoomType
from app.realtime import events, registry, setup
from app.realtime.events import EventType
from app.realtime.rooms import RoomDirectory
from app.serializers import room_out
from tests.conftest import TEST_DB_URL, _plain_dsn
from tests.realtime_app import create_test_app
from tests.realtime_support import FakeRedisTcp, WsClient, http_client, lock_data, serve_app, token_for

D = "2026-10-26"  # segunda-feira
RNF01_SECONDS = 0.2
DB_ROUND_TRIP_TIMEOUT = 15.0


def realtime_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("realtime:") and not t.done()]


class RealtimeServer:
    def __init__(self, base: str, app) -> None:
        self.base = base
        self.app = app
        self.clients: list[WsClient] = []

    @property
    def runtime(self):
        return self.app.state.realtime

    async def connect(self, user, **kwargs) -> WsClient:
        client = await WsClient.connect(self.base, token_for(user), **kwargs)
        self.clients.append(client)
        return client

    async def close_clients(self) -> None:
        for client in self.clients:
            with suppress(Exception):
                await client.close()


@asynccontextmanager
async def run_server(**startup_options) -> AsyncIterator[RealtimeServer]:
    app = create_test_app(sweep_interval=0.1, **startup_options)
    async with serve_app(app) as base:
        server = RealtimeServer(base, app)
        try:
            yield server
        finally:
            await server.close_clients()
    assert realtime_tasks() == []


@pytest.fixture
async def server():
    async with run_server() as srv:
        yield srv


@pytest.fixture
async def ana(receptionist):
    return receptionist


@pytest.fixture
async def bia(receptionist2):
    return receptionist2


def lock_of(msg: dict) -> dict:
    return msg["data"]["lock"]


# ---------------------------------------------------------------------- conexão


async def test_invalid_tokens_close_with_4401(server, make_user):
    expired = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    for token in (None, "", "lixo", expired):
        # o handshake completa (101) e só então o servidor fecha: o navegador enxerga o 4401
        client = await WsClient.connect(server.base, token, hello=False)
        with pytest.raises(ConnectionClosed) as exc:
            await asyncio.wait_for(client.ws.recv(), 3)
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 4401, token
        assert exc.value.rcvd.reason == "Sessão expirada ou inválida. Entre novamente."
    assert len(server.runtime.hub) == 0


async def test_disabled_or_revoked_accounts_are_refused_with_their_own_codes(
    server, make_user, session
):
    inactive = await make_user("ines", "Inês Inativa", is_active=False)
    changed = await make_user("caio", "Caio Senha Nova")
    old_token = token_for(changed)
    changed.token_version += 1  # senha trocada depois de emitir o token
    await session.commit()
    cases = [
        (token_for(inactive), 4403, "Seu acesso foi desativado. Fale com o administrador."),
        (old_token, 4401,
         "Sua sessão foi encerrada porque a senha foi alterada. Entre novamente."),
    ]
    for token, code, reason in cases:
        client = await WsClient.connect(server.base, token, hello=False)
        with pytest.raises(ConnectionClosed) as exc:
            await asyncio.wait_for(client.ws.recv(), 5)
        assert exc.value.rcvd is not None
        assert (exc.value.rcvd.code, exc.value.rcvd.reason) == (code, reason)
    assert len(server.runtime.hub) == 0
    ok = await server.connect(changed)  # token novo (versão atual) entra normalmente
    assert ok.hello["data"]["user"]["id"] == changed.id


async def test_hello_contents(server, ana, bia, video_room):
    a = await server.connect(ana)
    data = a.hello["data"]
    assert data["user"] == {"id": ana.id, "full_name": "Ana Recepção"}
    assert data["instance_id"] == settings.instance_id
    assert data["conn_id"]
    server_time = datetime.fromisoformat(data["server_time"])
    assert server_time.tzinfo is not None
    assert abs(datetime.now(timezone.utc) - server_time) < timedelta(seconds=5)
    assert data["locks"] == []
    assert data["online"] == [{"id": ana.id, "full_name": "Ana Recepção", "connections": 1}]

    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"), "r1")
    acquired = await a.expect("LOCK_ACQUIRED")
    b = await server.connect(bia)
    assert b.hello["data"]["locks"] == [lock_of(acquired)]
    assert b.hello["data"]["online"] == [
        {"id": ana.id, "full_name": "Ana Recepção", "connections": 1},
        {"id": bia.id, "full_name": "Bia Recepção", "connections": 1},
    ]


async def test_socket_does_not_hold_db_connections(server, ana, bia, video_room):
    conn = await asyncpg.connect(_plain_dsn(TEST_DB_URL))

    async def db_connections() -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )

    try:
        baseline = await db_connections()  # inclui a sessão das fixtures
        a, b = await server.connect(ana), await server.connect(bia)
        await a.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"))
        await a.expect("LOCK_ACQUIRED")
        await b.expect("SLOT_LOCKED")
        assert await db_connections() == baseline
    finally:
        await conn.close()


def room_event(room) -> dict:
    """Corpo de ROOM_CREATED/ROOM_UPDATED exatamente como os serviços publicam."""
    return {"room": room_out(room).model_dump(mode="json")}


async def test_room_events_update_the_room_cache_without_the_database(
    server, ana, video_room, make_room, session, monkeypatch
):
    a = await server.connect(ana)  # a autenticação já carregou as salas
    video_room.is_active = False
    await session.commit()
    # sem evento, vale o cache (a sala ainda parece ativa)...
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"), "c1")
    assert (await a.expect("LOCK_ACQUIRED"))["request_id"] == "c1"

    # ...até o serviço publicar ROOM_UPDATED depois do commit. O evento traz a sala inteira: o
    # cache é atualizado na hora e o LOCK_SLOT seguinte não espera uma conexão nova com o banco
    # (antes ele recarregava as salas, e no Windows sob carga isso passava dos 3 s do teste).
    async def no_database(self, session):
        raise AssertionError("o cache deveria vir do evento, sem consultar o banco")

    with monkeypatch.context() as patch:
        patch.setattr(RoomDirectory, "load", no_database)
        await events.publish(EventType.ROOM_UPDATED, room_event(video_room))
        await a.send("LOCK_SLOT", lock_data(video_room.id, D, "11:00", "12:00"), "c2")
        denied = await a.expect("LOCK_DENIED")
        assert (denied["request_id"], denied["data"]["reason"]) == ("c2", "ROOM_INACTIVE")

        new_room = await make_room("Sala Nova", RoomType.NORMAL)
        await events.publish(EventType.ROOM_CREATED, room_event(new_room))
        await a.send("LOCK_SLOT", lock_data(new_room.id, D, "09:00", "10:00"), "c3")
        assert (await a.expect("LOCK_ACQUIRED"))["request_id"] == "c3"


async def test_room_event_without_room_state_reloads_from_the_database(server, ana, video_room,
                                                                       session):
    a = await server.connect(ana)
    video_room.is_active = False
    await session.commit()
    await events.publish(EventType.ROOM_UPDATED, {"room": {"id": video_room.id}})
    assert server.runtime.rooms.stale
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "11:00", "12:00"), "r1")
    # Recarregar abre uma conexão nova com o PostgreSQL (NullPool nos testes): sob carga, no
    # Windows, isso já levou mais de 3 s. O prazo maior mede a correção, não a latência.
    denied = await a.expect("LOCK_DENIED", timeout=DB_ROUND_TRIP_TIMEOUT)
    assert (denied["request_id"], denied["data"]["reason"]) == ("r1", "ROOM_INACTIVE")
    assert not server.runtime.rooms.stale


# ---------------------------------------------------------------------- travas


async def test_lock_reaches_sender_and_others_within_200ms(server, ana, bia, video_room):
    a, b = await server.connect(ana), await server.connect(bia)
    await b.expect("PRESENCE")
    started = clock.perf_counter()
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"), "req-1")
    acquired = await a.expect("LOCK_ACQUIRED")
    ack_elapsed = clock.perf_counter() - started
    locked = await b.expect("SLOT_LOCKED")
    broadcast_elapsed = clock.perf_counter() - started
    assert ack_elapsed < RNF01_SECONDS, ack_elapsed
    assert broadcast_elapsed < RNF01_SECONDS, broadcast_elapsed  # RNF01

    assert acquired["request_id"] == "req-1" and "request_id" not in locked
    lock = lock_of(acquired)
    assert lock_of(locked) == lock
    assert {k: lock[k] for k in ("room_id", "date", "start", "end", "user", "booking_id")} == {
        "room_id": video_room.id,
        "date": D,
        "start": "14:00",
        "end": "15:00",
        "user": {"id": ana.id, "full_name": "Ana Recepção"},
        "booking_id": None,
    }
    expires_at = datetime.fromisoformat(lock["expires_at"])
    expected = datetime.now(timezone.utc) + timedelta(seconds=settings.lock_ttl_seconds)
    assert abs(expires_at - expected) < timedelta(seconds=5)
    assert lock_of(await a.expect("SLOT_LOCKED")) == lock  # o broadcast também chega ao remetente
    stored = await registry.get_lock_store().get(lock["lock_id"])
    assert stored is not None and stored.user_id == ana.id


async def test_overlapping_lock_of_other_user_is_denied(server, ana, bia, video_room):
    a, b = await server.connect(ana), await server.connect(bia)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"))
    ana_lock = lock_of(await a.expect("LOCK_ACQUIRED"))
    await b.expect("SLOT_LOCKED")

    await b.send("LOCK_SLOT", lock_data(video_room.id, D, "14:30", "15:30"), "b1")
    denied = await b.expect("LOCK_DENIED")
    assert denied["request_id"] == "b1"
    assert denied["data"] == {
        "reason": "LOCKED_BY_OTHER",
        "message": "Ana Recepção está reservando este horário agora.",
        "conflicts": [ana_lock],
    }
    await b.assert_silent("LOCK_ACQUIRED", within=0.2)
    await a.assert_silent("SLOT_LOCKED", within=0.1, where=lambda m: lock_of(m)["user"]["id"] == bia.id)
    # horário adjacente continua livre
    await b.send("LOCK_SLOT", lock_data(video_room.id, D, "15:00", "16:00"), "b2")
    assert (await b.expect("LOCK_ACQUIRED"))["request_id"] == "b2"


async def test_new_selection_of_same_user_replaces_previous(server, ana, bia, video_room):
    a, b = await server.connect(ana), await server.connect(bia)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"))
    first = lock_of(await a.expect("LOCK_ACQUIRED"))
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:30", "16:00"))
    second = lock_of(await a.expect("LOCK_ACQUIRED"))
    unlocked = await b.expect("SLOT_UNLOCKED", where=lambda m: lock_of(m)["lock_id"] == first["lock_id"])
    assert unlocked["data"]["reason"] == "released"
    await b.expect("SLOT_LOCKED", where=lambda m: lock_of(m)["lock_id"] == second["lock_id"])
    order = [(m["type"], lock_of(m)["lock_id"]) for m in b.received if m["type"].startswith("SLOT_")]
    assert order == [
        ("SLOT_LOCKED", first["lock_id"]),
        ("SLOT_UNLOCKED", first["lock_id"]),
        ("SLOT_LOCKED", second["lock_id"]),
    ]
    assert [lk.lock_id for lk in await registry.get_lock_store().list_active()] == [second["lock_id"]]


async def test_unlock_only_by_owner(server, ana, bia, video_room):
    a, b = await server.connect(ana), await server.connect(bia)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"))
    lock = lock_of(await a.expect("LOCK_ACQUIRED"))

    await b.send("UNLOCK_SLOT", {"lock_id": lock["lock_id"]}, "u1")
    error = await b.expect("ERROR")
    assert error["request_id"] == "u1"
    assert error["data"] == {
        "code": "NOT_LOCK_OWNER",
        "message": "Somente quem fez a seleção pode liberá-la.",
    }
    assert await registry.get_lock_store().get(lock["lock_id"]) is not None
    await b.assert_silent("SLOT_UNLOCKED", within=0.1)

    await a.send("UNLOCK_SLOT", {"lock_id": lock["lock_id"]}, "u2")
    for client in (a, b):
        unlocked = await client.expect("SLOT_UNLOCKED")
        assert unlocked["data"] == {"lock": lock, "reason": "released"}
    assert await registry.get_lock_store().get(lock["lock_id"]) is None

    await a.send("UNLOCK_SLOT", {"lock_id": lock["lock_id"]}, "u3")
    gone = await a.expect("ERROR")
    assert (gone["request_id"], gone["data"]["code"]) == ("u3", "LOCK_NOT_FOUND")
    await a.send("UNLOCK_SLOT", {}, "u4")
    assert (await a.expect("ERROR"))["data"]["code"] == "INVALID_MESSAGE"


async def test_refresh_lock(server, ana, bia, video_room):
    a, b = await server.connect(ana), await server.connect(bia)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"))
    lock = lock_of(await a.expect("LOCK_ACQUIRED"))
    await b.expect("SLOT_LOCKED")
    await asyncio.sleep(0.05)

    await a.send("REFRESH_LOCK", {"lock_id": lock["lock_id"]}, "f1")
    refreshed = await a.expect("LOCK_REFRESHED")
    assert refreshed["request_id"] == "f1"
    new_lock = lock_of(refreshed)
    assert new_lock["lock_id"] == lock["lock_id"]
    assert datetime.fromisoformat(new_lock["expires_at"]) > datetime.fromisoformat(lock["expires_at"])
    assert lock_of(await b.expect("SLOT_LOCKED")) == new_lock

    await b.send("REFRESH_LOCK", {"lock_id": lock["lock_id"]}, "f2")
    assert (await b.expect("ERROR"))["data"]["code"] == "NOT_LOCK_OWNER"
    await a.send("REFRESH_LOCK", {"lock_id": "nao-existe"}, "f3")
    missing = await a.expect("ERROR")
    assert missing["data"] == {
        "code": "LOCK_NOT_FOUND",
        "message": "Esta seleção expirou ou já foi liberada.",
    }


async def test_disconnect_releases_locks_and_updates_presence(server, ana, bia, video_room,
                                                              normal_room):
    a, b = await server.connect(ana), await server.connect(bia)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"))
    first = lock_of(await a.expect("LOCK_ACQUIRED"))
    await a.send("LOCK_SLOT", lock_data(normal_room.id, D, "10:00", "11:00"))
    second = lock_of(await a.expect("LOCK_ACQUIRED"))
    await a.close()

    released = {}
    for _ in range(2):
        msg = await b.expect("SLOT_UNLOCKED")
        assert msg["data"]["reason"] == "disconnected"
        released[lock_of(msg)["lock_id"]] = lock_of(msg)
    assert released == {first["lock_id"]: first, second["lock_id"]: second}
    presence = await b.expect("PRESENCE", where=lambda m: len(m["data"]["online"]) == 1)
    assert presence["data"]["online"] == [{"id": bia.id, "full_name": "Bia Recepção", "connections": 1}]
    assert await registry.get_lock_store().list_active() == []


async def test_expired_lock_is_announced_once(server, ana, bia, video_room, monkeypatch):
    monkeypatch.setattr(settings, "lock_ttl_seconds", 1)
    a, b = await server.connect(ana), await server.connect(bia)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "11:00", "11:30"))
    lock = lock_of(await a.expect("LOCK_ACQUIRED"))
    for client in (a, b):
        expired = await client.expect("SLOT_UNLOCKED", timeout=3)
        assert expired["data"] == {"lock": lock, "reason": "expired"}
    assert await b.count("SLOT_UNLOCKED", within=0.5) == 0
    assert await registry.get_lock_store().list_active() == []


async def test_lock_with_booking_id(server, ana, video_room):
    a = await server.connect(ana)
    booking_id = "8D2C2F6E-4B7A-4D53-9C1E-0F7A2B3C4D5E"
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "16:00", "17:00", booking_id=booking_id))
    assert lock_of(await a.expect("LOCK_ACQUIRED"))["booking_id"] == booking_id.lower()


async def test_lock_request_validation(server, ana, video_room, make_room):
    inactive = await make_room("Sala Desativada", RoomType.NORMAL, is_active=False)
    a = await server.connect(ana)
    room = video_room.id
    cases = [
        ({"room_id": str(room), "date": D, "start": "09:00", "end": "10:00"}, "INVALID_MESSAGE",
         "Sala inválida."),
        ({"room_id": True, "date": D, "start": "09:00", "end": "10:00"}, "INVALID_MESSAGE", None),
        ({"date": D, "start": "09:00", "end": "10:00"}, "INVALID_MESSAGE", None),
        (lock_data(room, "26/10/2026", "09:00", "10:00"), "INVALID_DATE",
         "Data inválida. Use o formato AAAA-MM-DD."),
        (lock_data(room, "2026-02-30", "09:00", "10:00"), "INVALID_DATE", None),
        (lock_data(room, "20261026", "09:00", "10:00"), "INVALID_DATE", None),
        (lock_data(room, D, "9:00", "10:00"), "INVALID_TIME", "Horário inválido. Use o formato HH:MM."),
        (lock_data(room, D, "09:00", "24:00"), "INVALID_TIME", None),
        (lock_data(room, D, "09:00", None), "INVALID_TIME", None),
        (lock_data(room, D, "15:00", "14:00"), "INVALID_INTERVAL",
         "O horário de término deve ser depois do início."),
        (lock_data(room, D, "14:00", "14:00"), "INVALID_INTERVAL", None),
        (lock_data(room, D, "14:03", "15:00"), "INVALID_TIME",
         "Os horários devem ser múltiplos de 5 minutos."),
        (lock_data(room, D, "07:30", "08:30"), "OUTSIDE_OPERATING_HOURS",
         "Fora do horário de funcionamento (08:00–19:00)."),
        (lock_data(room, D, "18:30", "19:30"), "OUTSIDE_OPERATING_HOURS", None),
        (lock_data(room, D, "09:00", "10:00", booking_id="xyz"), "INVALID_MESSAGE",
         "Reserva inválida."),
        (lock_data(9999, D, "09:00", "10:00"), "ROOM_NOT_FOUND", "Sala não encontrada."),
        (lock_data(2**40, D, "09:00", "10:00"), "INVALID_MESSAGE", None),
        (lock_data(inactive.id, D, "09:00", "10:00"), "ROOM_INACTIVE", "Esta sala está desativada."),
    ]
    for n, (data, reason, message) in enumerate(cases):
        await a.send("LOCK_SLOT", data, f"v{n}")
        denied = await a.expect("LOCK_DENIED")
        assert denied["request_id"] == f"v{n}", data
        assert denied["data"]["reason"] == reason, data
        assert denied["data"]["conflicts"] == []
        if message is not None:
            assert denied["data"]["message"] == message
    assert await registry.get_lock_store().list_active() == []
    # o socket continua vivo
    await a.send("LOCK_SLOT", lock_data(room, D, "08:00", "19:00"), "ok")
    assert (await a.expect("LOCK_ACQUIRED"))["request_id"] == "ok"


async def test_malformed_messages_get_error_without_closing(server, ana):
    a = await server.connect(ana)
    cases = [
        ("isto não é json", "INVALID_JSON", None),
        ("[1, 2]", "INVALID_MESSAGE", None),
        ('{"type": 5}', "INVALID_MESSAGE", None),
        ('{"data": {}}', "INVALID_MESSAGE", None),
        ('{"type": "LOCK_SLOT", "data": "x", "request_id": "m1"}', "INVALID_MESSAGE", "m1"),
        ('{"type": "NOPE", "request_id": "m2"}', "UNKNOWN_TYPE", "m2"),
        ('{"type": "PING", "data": [1]}', "INVALID_MESSAGE", None),
        (b"\xff\xfe\xfd", "INVALID_JSON", None),
        ("x" * 20_000, "MESSAGE_TOO_LARGE", None),
    ]
    for raw, code, request_id in cases:
        await a.send_raw(raw)
        error = await a.expect("ERROR")
        assert error["data"]["code"] == code, raw
        assert error["data"]["message"]
        assert error.get("request_id") == request_id
    unknown = [m for m in a.received if m["type"] == "ERROR" and m["data"]["code"] == "UNKNOWN_TYPE"]
    assert unknown[0]["data"]["message"] == "Tipo de mensagem desconhecido: NOPE."

    await a.send("PING", {}, "p1")
    assert (await a.expect("PONG"))["request_id"] == "p1"
    await a.send_raw('{"type": "PING", "request_id": 7}')
    assert (await a.expect("PONG"))["request_id"] == "7"
    assert len(server.runtime.hub) == 1


# ---------------------------------------------------------------------- eventos e presença


async def test_publish_from_server_process_reaches_all_clients(server, ana, bia):
    a, b = await server.connect(ana), await server.connect(bia)
    await a.expect("PRESENCE", where=lambda m: len(m["data"]["online"]) == 2)
    booking = {"id": "6f1c", "room_id": 1, "date": D, "start": "14:00", "end": "15:00"}
    started = clock.perf_counter()
    await events.publish(EventType.BOOKING_CREATED, {"booking": booking})
    for client in (a, b):
        msg = await client.expect("BOOKING_CREATED")
        assert clock.perf_counter() - started < RNF01_SECONDS
        assert msg["data"] == {"booking": booking}
        assert "ts" in msg and "request_id" not in msg

    async with http_client(server.base) as http:
        response = await http.post(
            "/test/publish", json={"type": "ROOM_UPDATED", "data": {"room": {"id": 7}}}
        )
    assert response.status_code == 200
    for client in (a, b):
        assert (await client.expect("ROOM_UPDATED"))["data"] == {"room": {"id": 7}}
        assert await client.count("ROOM_UPDATED", within=0.2) == 0  # exatamente uma vez


async def test_presence_join_and_leave_is_deduplicated_by_user(server, ana, bia):
    def online(msg: dict) -> dict[int, int]:
        return {u["id"]: u["connections"] for u in msg["data"]["online"]}

    a = await server.connect(ana)
    assert online(a.hello) == {ana.id: 1}
    b = await server.connect(bia)
    assert online(b.hello) == {ana.id: 1, bia.id: 1}
    await a.expect("PRESENCE", where=lambda m: online(m) == {ana.id: 1, bia.id: 1})

    a2 = await server.connect(ana)  # segunda aba da Ana
    await b.expect("PRESENCE", where=lambda m: online(m) == {ana.id: 2, bia.id: 1})
    await a2.close()
    await b.expect("PRESENCE", where=lambda m: online(m) == {ana.id: 1, bia.id: 1})
    await b.close()
    last = await a.expect("PRESENCE", where=lambda m: online(m) == {ana.id: 1})
    assert last["data"]["online"] == [{"id": ana.id, "full_name": "Ana Recepção", "connections": 1}]


# ---------------------------------------------------------------------- ciclo de vida


async def test_startup_and_shutdown_are_idempotent():
    app = create_test_app()
    await setup.startup(app)
    runtime = app.state.realtime
    await setup.startup(app)
    assert app.state.realtime is runtime and runtime.backend == "memory"
    assert registry.get_lock_store() is runtime.store
    assert events.get_event_bus() is runtime.bus
    assert setup.realtime_backend_name() == "memory"
    await setup.shutdown(app)
    await setup.shutdown(app)
    assert isinstance(registry.get_lock_store(), registry.NullLockStore)
    assert events.get_event_bus() is None
    assert realtime_tasks() == []


async def test_shutdown_closes_sockets_with_1012(server, ana, video_room):
    a = await server.connect(ana)
    await a.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"))
    await a.expect("LOCK_ACQUIRED")
    store = server.runtime.store
    await setup.shutdown(server.app)
    with pytest.raises(ConnectionClosed) as exc:
        while True:
            await asyncio.wait_for(a.ws.recv(), 3)
    assert exc.value.rcvd is not None and exc.value.rcvd.code == 1012
    assert await store.list_active() == []  # a conexão liberou suas travas ao sair
    assert isinstance(registry.get_lock_store(), registry.NullLockStore)
    assert realtime_tasks() == []
    # sem tempo real, novas conexões são recusadas com código observável
    client = await WsClient.connect(server.base, token_for(ana), hello=False)
    with pytest.raises(ConnectionClosed) as refused:
        await asyncio.wait_for(client.ws.recv(), 3)
    assert refused.value.rcvd.code == 1013


async def test_startup_fails_when_redis_is_unreachable(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(settings, "redis_url", f"redis://:segredo@127.0.0.1:{port}/0")
    assert setup.realtime_backend_name() == "redis"
    app = create_test_app()
    with pytest.raises(RuntimeError, match="Redis inacessível") as exc:
        await setup.startup(app)
    assert "segredo" not in str(exc.value)
    assert getattr(app.state, "realtime", None) is None
    assert isinstance(registry.get_lock_store(), registry.NullLockStore)
    assert events.get_event_bus() is None


# ---------------------------------------------------------------------- backend Redis (TCP)


async def test_redis_backend_end_to_end(ana, bia, video_room, monkeypatch):
    with FakeRedisTcp() as fake:
        monkeypatch.setattr(settings, "redis_url", fake.url)
        async with run_server(heartbeat_interval=0.2, presence_ttl=1.0) as srv:
            assert srv.runtime.backend == "redis"
            a, b = await srv.connect(ana), await srv.connect(bia)
            assert {u["id"] for u in b.hello["data"]["online"]} == {ana.id, bia.id}

            started = clock.perf_counter()
            await a.send("LOCK_SLOT", lock_data(video_room.id, D, "14:00", "15:00"), "r1")
            lock = lock_of(await a.expect("LOCK_ACQUIRED"))
            assert lock_of(await b.expect("SLOT_LOCKED")) == lock
            assert clock.perf_counter() - started < RNF01_SECONDS

            await b.send("LOCK_SLOT", lock_data(video_room.id, D, "14:30", "15:30"), "r2")
            assert (await b.expect("LOCK_DENIED"))["data"]["conflicts"] == [lock]

            monkeypatch.setattr(settings, "lock_ttl_seconds", 1)
            await b.send("LOCK_SLOT", lock_data(video_room.id, D, "16:00", "16:30"))
            short = lock_of(await b.expect("LOCK_ACQUIRED"))
            expired = await a.expect("SLOT_UNLOCKED", timeout=3)
            assert expired["data"] == {"lock": short, "reason": "expired"}

            await a.close()
            gone = await b.expect("SLOT_UNLOCKED", where=lambda m: lock_of(m)["lock_id"] == lock["lock_id"])
            assert gone["data"]["reason"] == "disconnected"
            await b.expect("PRESENCE", where=lambda m: [u["id"] for u in m["data"]["online"]] == [bia.id])
            assert await srv.runtime.store.list_active() == []
