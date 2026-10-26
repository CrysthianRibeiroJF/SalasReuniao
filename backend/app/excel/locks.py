"""Travas de seleção (soft locks) vistas pela importação fora do servidor (CLI).

Pela tela/API a importação usa o LockStore do próprio servidor (app.realtime.registry). A CLI
roda num processo sem servidor: com AGENDA_REDIS_URL ela lê as travas no mesmo Redis das
instâncias (RedisLockStore), então uma reserva que alguém está selecionando na grade é pulada
com aviso, igual à importação pela tela. Sem Redis cada servidor guarda as travas na própria
memória e a CLI não tem como vê-las (documentado em docs/IMPORTACAO.md).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.realtime.redis_store import RedisLockStore
from app.realtime.registry import LockStore

log = logging.getLogger(__name__)

__all__ = ["external_lock_store"]


@asynccontextmanager
async def external_lock_store(
    redis_url: str | None, *, timeout: float = 3.0
) -> AsyncIterator[LockStore | None]:
    """RedisLockStore temporário (ou None sem Redis). A conexão só é aberta na primeira
    consulta; se o Redis estiver fora, a importação avisa e segue conferindo só o banco."""
    if not redis_url:
        yield None
        return
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        protocol=2,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        # Uma tentativa só: com o Redis fora, a importação avisa e segue (no Windows cada
        # conexão recusada já leva ~2 s).
        retry=Retry(NoBackoff(), retries=0),
    )
    try:
        yield RedisLockStore(client)
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — fechar uma conexão que nem abriu não é erro
            log.debug("Falha ao fechar a conexão Redis das travas", exc_info=True)
