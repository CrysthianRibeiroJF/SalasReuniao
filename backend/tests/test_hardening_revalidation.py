"""Revalidação dos WebSockets: JWT que vence durante a conexão ou revogado pela troca de senha
(4401) e usuário desativado ou removido (4403)."""

from __future__ import annotations

import asyncio
from collections.abc import Collection

import asyncpg
import pytest
from sqlalchemy import delete

from app.models import User
from app.realtime.bus import MemoryBus
from app.realtime.hub import ConnectionHub
from app.realtime.memory import MemoryLockStore
from app.realtime.presence import MemoryPresence
from app.realtime.runtime import (
    ACCESS_REVOKED_REASON,
    SESSION_EXPIRED_REASON,
    SESSION_REVOKED_REASON,
    RealtimeRuntime,
)
from app.realtime.ws import token_expiry
from app.services import users as users_service
from tests.conftest import TEST_DB_URL, _plain_dsn
from tests.hardening_support import closed_with, token_expiring_in, wait_until
from tests.realtime_support import WsClient, lock_data, token_for
from tests.test_realtime_ws import D, realtime_tasks, run_server

# Conexão nova com o PostgreSQL pode levar segundos no Windows sob carga (NullPool nos testes).
DB_TIMEOUT = 15.0


def online_ids(msg: dict) -> dict[int, int]:
    return {u["id"]: u["connections"] for u in msg["data"]["online"]}


# ---------------------------------------------------------------------- vencimento do JWT


async def test_socket_closes_with_4401_when_its_token_expires(receptionist, receptionist2,
                                                              video_room):
    async with run_server() as server:
        short = await WsClient.connect(server.base, token_expiring_in(receptionist, 3.0))
        server.clients.append(short)
        other = await server.connect(receptionist2)
        await short.send("LOCK_SLOT", lock_data(video_room.id, D, "09:00", "10:00"))
        lock = (await short.expect("LOCK_ACQUIRED"))["data"]["lock"]

        code, reason = await closed_with(short, timeout=8)
        assert (code, reason) == (4401, SESSION_EXPIRED_REASON)
        # a saída é a de sempre: travas liberadas e presença atualizada para os demais
        released = await other.expect("SLOT_UNLOCKED")
        assert released["data"] == {"lock": lock, "reason": "disconnected"}
        await other.expect(
            "PRESENCE", where=lambda m: online_ids(m) == {receptionist2.id: 1}
        )
        await other.send("PING")
        await other.expect("PONG")  # quem tem token válido continua conectado
        assert len(server.runtime.hub) == 1


async def test_expiry_timer_dies_with_the_connection(receptionist):
    async with run_server() as server:
        client = await WsClient.connect(server.base, token_expiring_in(receptionist, 2.0))
        await client.close()
        await asyncio.sleep(2.5)  # o vencimento passa depois que a conexão já saiu
        assert len(server.runtime.hub) == 0
    assert realtime_tasks() == []


def test_token_expiry_reads_the_exp_claim(receptionist):
    expires = token_expiry(token_expiring_in(receptionist, 60))
    assert expires is not None and expires.tzinfo is not None
    assert token_expiry("lixo") is None


# ---------------------------------------------------------------------- usuário desativado


async def test_deactivated_user_is_disconnected_by_the_periodic_check(
    receptionist, receptionist2, session
):
    async with run_server(revalidate_interval=0.2) as server:
        ana_tabs = [await server.connect(receptionist) for _ in range(2)]
        bia = await server.connect(receptionist2)
        await asyncio.sleep(0.5)  # algumas rodadas sem mudança: ninguém sai
        assert len(server.runtime.hub) == 3

        receptionist.is_active = False
        await session.commit()
        for tab in ana_tabs:
            assert await closed_with(tab, timeout=DB_TIMEOUT) == (4403, ACCESS_REVOKED_REASON)
        await bia.expect(
            "PRESENCE", where=lambda m: online_ids(m) == {receptionist2.id: 1}, timeout=5
        )
        await bia.send("PING")
        await bia.expect("PONG")
        # e não consegue voltar: o handshake recusa usuário inativo
        retry = await WsClient.connect(server.base, token_for(receptionist), hello=False)
        assert await closed_with(retry, timeout=DB_TIMEOUT) == (4403, ACCESS_REVOKED_REASON)


async def test_deleted_user_is_disconnected(make_user, session):
    carla = await make_user("carla", "Carla Recepção")
    async with run_server(revalidate_interval=0.2) as server:
        client = await server.connect(carla)
        await session.execute(delete(User).where(User.id == carla.id))
        await session.commit()
        assert await closed_with(client, timeout=DB_TIMEOUT) == (4403, ACCESS_REVOKED_REASON)


async def test_password_change_closes_only_the_sockets_of_old_tokens(
    receptionist, receptionist2, session
):
    """Senha trocada (users.set_password, o mesmo do PATCH /api/users e do reset-password): as abas
    abertas com o token antigo saem com 4401 na próxima revalidação; uma aba aberta depois, com
    o token novo, continua."""
    async with run_server(revalidate_interval=0.2) as server:
        old_tab = await server.connect(receptionist)
        bia = await server.connect(receptionist2)
        await users_service.set_password(receptionist, "senha-nova-123")
        await session.commit()
        new_tab = await server.connect(receptionist)  # token com a versão nova
        assert await closed_with(old_tab, timeout=DB_TIMEOUT) == (4401, SESSION_REVOKED_REASON)
        await asyncio.sleep(0.6)  # mais algumas rodadas: ninguém mais sai
        for tab in (new_tab, bia):
            await tab.send("PING")
            await tab.expect("PONG")
        assert len(server.runtime.hub) == 2


async def test_revalidation_does_not_hold_database_connections(receptionist, receptionist2):
    conn = await asyncpg.connect(_plain_dsn(TEST_DB_URL))

    async def db_connections() -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )

    try:
        async with run_server() as server:
            await server.connect(receptionist)
            await server.connect(receptionist2)
            baseline = await db_connections()  # inclui a sessão das fixtures
            for _ in range(3):
                assert await server.runtime.revalidate_users() == 0

            async def released() -> bool:
                return await db_connections() == baseline

            # o processo do PostgreSQL some logo depois que a conexão fecha
            await wait_until(released, timeout=5)
    finally:
        await conn.close()


# ---------------------------------------------------------------------- unidade (sem rede)


class FakeSocket:
    def __init__(self) -> None:
        self.closed: tuple[int, str | None] | None = None

    async def send_text(self, data: str) -> None:
        return None

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)


def make_runtime(check) -> RealtimeRuntime:
    hub = ConnectionHub()
    return RealtimeRuntime(
        backend="memory",
        store=MemoryLockStore(),
        bus=MemoryBus(hub),
        hub=hub,
        presence=MemoryPresence(),
        node_id="n1",
        revalidate_interval=None,
        active_users=check,
    )


@pytest.mark.parametrize("connected", [0, 3])
async def test_one_query_per_round_for_all_connected_users(connected):
    calls: list[set[int]] = []

    async def check(user_ids: Collection[int]) -> dict[int, int]:
        calls.append(set(user_ids))
        return {user_id: 0 for user_id in user_ids if user_id != 2}

    runtime = make_runtime(check)
    sockets = [FakeSocket() for _ in range(connected)]
    for n, socket in enumerate(sockets):
        runtime.hub.add(socket, user_id=n + 1, user_name=f"U{n + 1}").start("{}")
        runtime.hub.add(socket, user_id=n + 1, user_name=f"U{n + 1}").start("{}")  # 2 abas
    closed = await runtime.revalidate_users()
    if not connected:
        assert calls == [] and closed == 0  # sem conexões, nada de banco
        return
    assert calls == [{1, 2, 3}]  # uma consulta com todos os usuários
    assert closed == 2  # as duas abas do usuário 2
    await asyncio.sleep(0.05)
    assert sockets[1].closed == (4403, ACCESS_REVOKED_REASON)
    assert sockets[0].closed is None and sockets[2].closed is None
    await runtime.hub.aclose()


async def test_revalidation_closes_connections_of_an_older_token_version():
    async def check(user_ids: Collection[int]) -> dict[int, int]:
        return {1: 3, 2: 0}  # a senha do usuário 1 foi trocada: versão atual 3

    runtime = make_runtime(check)
    stale, current, other = FakeSocket(), FakeSocket(), FakeSocket()
    runtime.hub.add(stale, user_id=1, user_name="Ana", token_version=2).start("{}")
    runtime.hub.add(current, user_id=1, user_name="Ana", token_version=3).start("{}")
    runtime.hub.add(other, user_id=2, user_name="Bia").start("{}")
    assert await runtime.revalidate_users() == 1
    await asyncio.sleep(0.05)
    assert stale.closed == (4401, SESSION_REVOKED_REASON)
    assert current.closed is None and other.closed is None
    await runtime.hub.aclose()


async def test_close_at_is_cancelled_when_the_connection_stops():
    from datetime import datetime, timedelta, timezone

    hub = ConnectionHub()
    gone, kept = FakeSocket(), FakeSocket()
    soon = datetime.now(timezone.utc) + timedelta(seconds=0.2)
    conns = []
    for socket in (gone, kept):
        conn = hub.add(socket, user_id=1, user_name="Ana")
        conn.start("{}")
        hub.close_at(conn, soon, 4401, SESSION_EXPIRED_REASON)
        conns.append(conn)
    hub.remove(conns[0])
    await conns[0].stop()  # saiu antes de vencer: o timer vai junto
    await asyncio.sleep(0.4)
    assert gone.closed is None
    assert kept.closed == (4401, SESSION_EXPIRED_REASON)
    await hub.aclose()
