"""Limite de tentativas de login (força bruta), por usuário e por IP do cliente.

Regra (configurável em app/config.py): `login_max_failures` (5) falhas seguidas, sem intervalo
maior que `login_failure_window_seconds` (15 min) entre elas, bloqueiam por 1 min; cada nova falha
depois disso bloqueia por 5 e depois 15 min (`login_block_seconds`). Durante o bloqueio o login é
recusado com 429 TOO_MANY_ATTEMPTS sem nem conferir a senha (e sem contar como nova falha).
Um login bem-sucedido zera o contador do usuário e encerra a sequência de falhas do IP.

Com AGENDA_REDIS_URL o estado fica no Redis (chaves com TTL, compartilhado entre instâncias);
sem Redis, na memória do processo. Se o Redis falhar, a memória assume até ele voltar.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.config import settings
from app.services.errors import BusinessError

log = logging.getLogger(__name__)

KEY_PREFIX = "agenda:login"
STORE_RETRY_SECONDS = 30.0  # com o Redis fora do ar, tenta de novo a cada 30 s
_MEMORY_PRUNE_SIZE = 10_000


@dataclass(frozen=True)
class Policy:
    max_failures: int
    window_seconds: float
    block_seconds: tuple[float, ...]

    @classmethod
    def from_settings(cls) -> Policy:
        return cls(
            max_failures=max(1, settings.login_max_failures),
            window_seconds=float(settings.login_failure_window_seconds),
            block_seconds=tuple(float(s) for s in settings.login_block_seconds) or (60.0,),
        )

    def block_for(self, failures: int) -> float:
        """Duração do bloqueio depois da `failures`-ésima falha seguida (0 = sem bloqueio)."""
        if failures < self.max_failures:
            return 0.0
        return self.block_seconds[min(failures - self.max_failures, len(self.block_seconds) - 1)]

    def ttl(self, block: float) -> float:
        # O contador sobrevive ao bloqueio: a falha seguinte ao fim dele ainda escala.
        return self.window_seconds + block


@dataclass(frozen=True)
class Attempts:
    failures: int = 0
    blocked_until: float = 0.0


class ThrottleStore(Protocol):
    async def blocked_until(self, keys: list[str]) -> list[float]: ...

    async def add_failure(self, key: str, now: float, policy: Policy) -> Attempts: ...

    async def reset(self, keys: list[str]) -> None: ...

    async def aclose(self) -> None: ...


class MemoryThrottleStore:
    """Um processo só (sem Redis). As entradas vencidas são descartadas."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[Attempts, float]] = {}  # chave -> (estado, vence em)

    def _get(self, key: str) -> Attempts | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[1] <= self._clock():
            del self._entries[key]
            return None
        return entry[0]

    async def blocked_until(self, keys: list[str]) -> list[float]:
        return [(state.blocked_until if (state := self._get(k)) else 0.0) for k in keys]

    async def add_failure(self, key: str, now: float, policy: Policy) -> Attempts:
        current = self._get(key) or Attempts()
        failures = current.failures + 1
        block = policy.block_for(failures)
        state = Attempts(failures, now + block if block else current.blocked_until)
        self._entries[key] = (state, now + policy.ttl(block))
        if len(self._entries) > _MEMORY_PRUNE_SIZE:
            self._prune()
        return state

    async def reset(self, keys: list[str]) -> None:
        for key in keys:
            self._entries.pop(key, None)

    async def aclose(self) -> None:
        return None

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, (_, expires) in self._entries.items() if expires <= now]:
            del self._entries[key]


class RedisThrottleStore:
    """Hash por chave (failures, blocked_until) com TTL; compartilhado entre instâncias."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def blocked_until(self, keys: list[str]) -> list[float]:
        async with self._client.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.hget(key, "blocked_until")
            values = await pipe.execute()
        return [float(v) if v else 0.0 for v in values]

    async def add_failure(self, key: str, now: float, policy: Policy) -> Attempts:
        longest = max(policy.block_seconds)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hincrby(key, "failures", 1)
            pipe.hget(key, "blocked_until")
            pipe.expire(key, math.ceil(policy.ttl(longest)))
            failures, blocked, _ = await pipe.execute()
        failures = int(failures)
        block = policy.block_for(failures)
        blocked_until = float(blocked) if blocked else 0.0
        if block:
            blocked_until = now + block
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.hset(key, "blocked_until", repr(blocked_until))
                pipe.expire(key, math.ceil(policy.ttl(block)))
                await pipe.execute()
        else:
            await self._client.expire(key, math.ceil(policy.ttl(0)))
        return Attempts(failures, blocked_until)

    async def reset(self, keys: list[str]) -> None:
        await self._client.delete(*keys)

    async def aclose(self) -> None:
        await self._client.aclose()


class LoginThrottle:
    def __init__(
        self,
        store: ThrottleStore,
        *,
        policy: Policy | None = None,
        clock: Callable[[], float] = time.time,
        fallback: MemoryThrottleStore | None = None,
    ) -> None:
        self._store = store
        self._policy = policy or Policy.from_settings()
        self._clock = clock
        # Redis fora do ar: o limite continua valendo (por instância) em vez de sumir.
        self._fallback = fallback or MemoryThrottleStore(clock)
        self._store_failing = False
        self._retry_store_at = 0.0

    @property
    def backend(self) -> str:
        return "redis" if isinstance(self._store, RedisThrottleStore) else "memory"

    async def retry_after(self, username: str, ip: str) -> int | None:
        """Segundos até poder tentar de novo, ou None se o login está liberado."""
        until = await self._call("blocked_until", _keys(username, ip))
        remaining = max(until, default=0.0) - self._clock()
        return math.ceil(remaining) if remaining > 0 else None

    async def failure(self, username: str, ip: str) -> int | None:
        """Registra uma falha. Devolve os segundos de bloqueio se esta falha bloqueou."""
        now = self._clock()
        until = 0.0
        for key in _keys(username, ip):
            state = await self._call("add_failure", key, now, self._policy)
            until = max(until, state.blocked_until)
        remaining = until - now
        return math.ceil(remaining) if remaining > 0 else None

    async def success(self, username: str, ip: str) -> None:
        await self._call("reset", _keys(username, ip))

    async def aclose(self) -> None:
        await self._store.aclose()

    async def _call(self, method: str, *args):
        if self._store_failing and time.monotonic() < self._retry_store_at:
            # Redis caiu há pouco: não espera o timeout de conexão a cada login.
            return await getattr(self._fallback, method)(*args)
        try:
            result = await getattr(self._store, method)(*args)
        except Exception:
            if not self._store_failing:
                log.warning(
                    "Limite de tentativas de login: Redis indisponível, usando a memória deste "
                    "processo até ele voltar",
                    exc_info=True,
                )
            self._store_failing = True
            self._retry_store_at = time.monotonic() + STORE_RETRY_SECONDS
            return await getattr(self._fallback, method)(*args)
        if self._store_failing:
            log.info("Limite de tentativas de login: Redis de volta")
            self._store_failing = False
        return result


def build_login_throttle() -> LoginThrottle:
    """Redis quando configurado (conexão preguiçosa: não falha o startup), senão memória."""
    if settings.redis_url:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            protocol=2,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        return LoginThrottle(RedisThrottleStore(client))
    return LoginThrottle(MemoryThrottleStore())


def normalize_username(username: str) -> str:
    return username.strip().lower()


def _keys(username: str, ip: str) -> list[str]:
    return [f"{KEY_PREFIX}:user:{normalize_username(username)}", f"{KEY_PREFIX}:ip:{ip}"]


def too_many_attempts(retry_after: int) -> BusinessError:
    retry_after = max(1, int(retry_after))
    return BusinessError(
        429,
        "TOO_MANY_ATTEMPTS",
        f"Muitas tentativas de entrada sem sucesso. Tente novamente em {format_wait(retry_after)}.",
        {"retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


def format_wait(seconds: int) -> str:
    if seconds < 60:
        return "1 segundo" if seconds == 1 else f"{seconds} segundos"
    minutes = math.ceil(seconds / 60)
    return "1 minuto" if minutes == 1 else f"{minutes} minutos"
