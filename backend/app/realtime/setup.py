"""Ponto de integração do tempo real com a aplicação.

app/main.py faz:
    from app.realtime.setup import router as ws_router, startup as realtime_startup, shutdown as realtime_shutdown
    app.include_router(ws_router)            # expõe /ws
    await realtime_startup(app)              # no lifespan, antes de servir
    await realtime_shutdown(app)             # no lifespan, ao encerrar

Com settings.redis_url usa Redis (travas + pub/sub entre instâncias); sem ele, memória
(1 instância). O estado fica em app.state.realtime; LockStore e EventBus são registrados
globalmente para os serviços (registry / events).
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import settings
from app.realtime import events, registry
from app.realtime.bus import MemoryBus, RedisBus
from app.realtime.hub import ConnectionHub
from app.realtime.memory import MemoryLockStore
from app.realtime.presence import MemoryPresence, RedisPresence
from app.realtime.redis_store import RedisLockStore
from app.realtime.runtime import (
    HEARTBEAT_INTERVAL,
    PRESENCE_TTL,
    REVALIDATE_INTERVAL,
    SWEEP_INTERVAL,
    RealtimeRuntime,
)
from app.realtime.ws import router

__all__ = ["router", "startup", "shutdown", "realtime_backend_name", "connect_redis", "redact_url"]

log = logging.getLogger(__name__)


async def startup(
    app: FastAPI,
    *,
    sweep_interval: float | None = None,
    heartbeat_interval: float | None = None,
    presence_ttl: float | None = None,
    revalidate_interval: float | None = None,
) -> None:
    """Escolhe Redis (settings.redis_url) ou memória; registra LockStore, EventBus e tarefas.

    Idempotente. Os intervalos opcionais existem para testes (padrões: varredura de travas 2 s,
    heartbeat 10 s, TTL de presença 20 s, revalidação dos usuários conectados 60 s).
    Redis configurado mas inacessível -> loga e levanta RuntimeError (a aplicação não sobe).
    """
    if getattr(app.state, "realtime", None) is not None:
        return
    runtime = await _build_runtime(
        sweep_interval=sweep_interval or SWEEP_INTERVAL,
        heartbeat_interval=heartbeat_interval or HEARTBEAT_INTERVAL,
        presence_ttl=presence_ttl or PRESENCE_TTL,
        revalidate_interval=revalidate_interval or REVALIDATE_INTERVAL,
    )
    try:
        await runtime.start()
    except BaseException:
        await runtime.stop()
        raise
    registry.set_lock_store(runtime.store)
    events.set_event_bus(runtime.bus)
    app.state.realtime = runtime
    log.info("Tempo real iniciado (backend %s, instância %s)", runtime.backend, runtime.instance_id)


async def shutdown(app: FastAPI) -> None:
    """Para tarefas de fundo, fecha conexões WebSocket (1012) e o cliente Redis. Idempotente."""
    runtime: RealtimeRuntime | None = getattr(app.state, "realtime", None)
    if runtime is None:
        return
    app.state.realtime = None
    if registry.get_lock_store() is runtime.store:
        registry.set_lock_store(registry.NullLockStore())
    if events.get_event_bus() is runtime.bus:
        events.set_event_bus(None)
    await runtime.stop()
    log.info("Tempo real encerrado (instância %s)", runtime.instance_id)


def realtime_backend_name() -> str:
    """'redis' ou 'memory' (exposto em GET /api/config e /api/health)."""
    return "redis" if settings.redis_url else "memory"


async def _build_runtime(
    *,
    sweep_interval: float,
    heartbeat_interval: float,
    presence_ttl: float,
    revalidate_interval: float,
) -> RealtimeRuntime:
    hub = ConnectionHub()
    # node_id único por processo/startup, mesmo que AGENDA_INSTANCE_ID se repita entre instâncias.
    node_id = f"{settings.instance_id}-{uuid4().hex[:6]}"
    if not settings.redis_url:
        return RealtimeRuntime(
            backend="memory",
            store=MemoryLockStore(),
            bus=MemoryBus(hub),
            hub=hub,
            presence=MemoryPresence(),
            node_id=node_id,
            sweep_interval=sweep_interval,
            heartbeat_interval=None,
            revalidate_interval=revalidate_interval,
        )

    client = await connect_redis(settings.redis_url)
    return RealtimeRuntime(
        backend="redis",
        store=RedisLockStore(client),
        bus=RedisBus(client, hub, node_id=node_id),
        hub=hub,
        presence=RedisPresence(client, node_id, ttl_seconds=presence_ttl),
        node_id=node_id,
        redis=client,
        sweep_interval=sweep_interval,
        heartbeat_interval=heartbeat_interval,
        revalidate_interval=revalidate_interval,
    )


async def connect_redis(url: str) -> Redis:
    # RESP2: compatível com qualquer servidor Redis (inclusive versões antigas/Memurai).
    client = Redis.from_url(
        url,
        decode_responses=True,
        protocol=2,
        health_check_interval=15,
        socket_connect_timeout=5,
        socket_keepalive=True,
    )
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        log.error("Redis inacessível em %s: %s", redact_url(url), exc)
        raise RuntimeError(f"Redis inacessível em {redact_url(url)}") from exc
    return client


def redact_url(url: str) -> str:
    """URL do Redis sem a senha (para logs e mensagens)."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = f"{parts.username}:" if parts.username else ":"
    return urlunsplit(parts._replace(netloc=f"{user}***@{host}"))
