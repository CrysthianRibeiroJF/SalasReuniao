"""Limite de tentativas de login: por usuário e por IP, bloqueio escalonado, 429 + Retry-After."""

from __future__ import annotations

import httpx
import pytest
from redis.asyncio import Redis

from app.config import settings
from app.main import create_app
from app.services.login_throttle import (
    LoginThrottle,
    MemoryThrottleStore,
    RedisThrottleStore,
    format_wait,
)
from tests.realtime_support import FakeRedisTcp


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def throttled(app, clock):
    app.state.login_throttle = LoginThrottle(MemoryThrottleStore(clock), clock=clock)
    return app


def client_from(app, ip: str = "10.0.0.1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post("/api/auth/login", json={"username": username, "password": password})


def assert_blocked(resp: httpx.Response, seconds: int, wait_text: str) -> None:
    assert resp.status_code == 429, resp.text
    detail = resp.json()["detail"]
    assert detail == {
        "code": "TOO_MANY_ATTEMPTS",
        "message": f"Muitas tentativas de entrada sem sucesso. Tente novamente em {wait_text}.",
        "retry_after_seconds": seconds,
    }
    assert resp.headers["retry-after"] == str(seconds)


async def test_five_failures_block_one_minute_then_escalate(throttled, clock, receptionist):
    async with client_from(throttled) as c:
        for _ in range(4):
            assert (await login(c, "ana", "errada")).status_code == 401
        assert_blocked(await login(c, "ana", "errada"), 60, "1 minuto")  # 5ª falha bloqueia
        # bloqueado: nem a senha certa entra, e a tentativa não conta como nova falha
        clock.advance(30)
        assert_blocked(await login(c, "ANA ", "senha123"), 30, "30 segundos")
        clock.advance(31)
        assert_blocked(await login(c, "ana", "errada"), 300, "5 minutos")  # 6ª: 5 min
        clock.advance(301)
        assert_blocked(await login(c, "ana", "errada"), 900, "15 minutos")  # 7ª: 15 min
        clock.advance(901)
        assert_blocked(await login(c, "ana", "errada"), 900, "15 minutos")  # e fica em 15
        clock.advance(901)
        resp = await login(c, "ana", "senha123")
        assert resp.status_code == 200  # acertou: zera
        for _ in range(4):
            assert (await login(c, "ana", "errada")).status_code == 401
        assert (await login(c, "ana", "senha123")).status_code == 200


async def test_failures_spread_over_more_than_15_minutes_do_not_add_up(
    throttled, clock, receptionist
):
    async with client_from(throttled) as c:
        for _ in range(2):
            for _ in range(4):
                assert (await login(c, "ana", "errada")).status_code == 401
            clock.advance(16 * 60)  # pausa maior que a janela: a contagem recomeça
        assert (await login(c, "ana", "senha123")).status_code == 200
        # falhas com menos de 15 min entre elas continuam contando como seguidas
        for _ in range(4):
            assert (await login(c, "ana", "errada")).status_code == 401
            clock.advance(14 * 60)
        assert_blocked(await login(c, "ana", "errada"), 60, "1 minuto")


async def test_block_is_per_username_and_per_ip(throttled, clock, receptionist, receptionist2):
    async with client_from(throttled, "10.0.0.1") as office, client_from(
        throttled, "10.0.0.2"
    ) as other:
        for _ in range(5):
            await login(office, "ana", "errada")
        # usuário bloqueado vale de qualquer IP
        assert_blocked(await login(other, "ana", "senha123"), 60, "1 minuto")
        # e o IP de onde vieram as falhas fica bloqueado para qualquer usuário
        assert_blocked(await login(office, "bia", "senha123"), 60, "1 minuto")
        # outro usuário em outro IP segue normal
        assert (await login(other, "bia", "senha123")).status_code == 200


async def test_spraying_many_usernames_from_one_ip_is_blocked(throttled, receptionist):
    async with client_from(throttled, "10.9.9.9") as c:
        for name in ("u1", "u2", "u3", "u4"):
            assert (await login(c, name, "x")).status_code == 401
        assert_blocked(await login(c, "u5", "x"), 60, "1 minuto")
        assert_blocked(await login(c, "ana", "senha123"), 60, "1 minuto")


async def test_success_ends_the_ip_streak(throttled, receptionist, receptionist2):
    """Recepção atrás do mesmo IP (NAT): erros de digitação de pessoas diferentes, intercalados
    com logins certos, não bloqueiam o escritório inteiro."""
    async with client_from(throttled, "10.0.0.1") as c:
        for _ in range(3):
            assert (await login(c, "ana", "errada")).status_code == 401
            assert (await login(c, "bia", "senha123")).status_code == 200
        for _ in range(3):
            assert (await login(c, "bia", "errada")).status_code == 401
        assert (await login(c, "ana", "senha123")).status_code == 200


async def test_lifespan_uses_memory_without_redis(app):
    assert app.state.login_throttle.backend == "memory"


async def test_redis_store_is_shared_between_instances(monkeypatch, receptionist):
    with FakeRedisTcp() as fake:
        monkeypatch.setattr(settings, "redis_url", fake.url)
        apps = [create_app(), create_app()]
        contexts = [application.router.lifespan_context(application) for application in apps]
        for ctx in contexts:
            await ctx.__aenter__()
        try:
            assert all(a.state.login_throttle.backend == "redis" for a in apps)
            async with client_from(apps[0]) as a, client_from(apps[1], "10.0.0.7") as b:
                for _ in range(4):
                    assert (await login(a, "ana", "errada")).status_code == 401
                resp = await login(b, "ana", "errada")  # 5ª falha, na outra instância
                assert resp.status_code == 429
                resp = await login(a, "ana", "senha123")
                assert resp.status_code == 429 and 0 < int(resp.headers["retry-after"]) <= 60
            client = Redis.from_url(fake.url, decode_responses=True, protocol=2)
            try:
                ttl = await client.ttl("agenda:login:user:ana")
                assert 900 < ttl <= 960  # janela + bloqueio: as chaves somem sozinhas
            finally:
                await client.aclose()
        finally:
            for ctx in reversed(contexts):
                await ctx.__aexit__(None, None, None)


async def test_redis_outage_falls_back_to_memory(clock, receptionist):
    store = RedisThrottleStore(Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2))
    throttle = LoginThrottle(store, clock=clock)
    for _ in range(4):
        assert await throttle.failure("ana", "10.0.0.1") is None
    assert await throttle.failure("ana", "10.0.0.1") == 60
    assert await throttle.retry_after("ana", "10.0.0.3") == 60
    await throttle.aclose()


def test_format_wait():
    assert [format_wait(s) for s in (1, 30, 59, 60, 61, 300, 900)] == [
        "1 segundo",
        "30 segundos",
        "59 segundos",
        "1 minuto",
        "2 minutos",
        "5 minutos",
        "15 minutos",
    ]
