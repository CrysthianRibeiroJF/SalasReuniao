"""Eventos gerados fora do servidor web (ex.: `python -m app.cli import-xlsx --commit`).

O processo da CLI não tem hub nem WebSockets. Durante o comando, os serviços publicam num
EventCollector (registrado como EventBus do processo); no fim, relay_to_instances() envia os
envelopes pelo canal do Redis no mesmo formato do RedisBus e com uma origem própria, então TODAS
as instâncias repassam aos navegadores conectados como se o evento fosse delas.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from app.realtime import events
from app.realtime.bus import RedisPublisher
from app.realtime.setup import redact_url


class RelayError(Exception):
    """Falha ao repassar eventos (mensagem pt-BR, sem senha)."""


class EventCollector:
    """EventBus que só guarda os envelopes publicados."""

    def __init__(self) -> None:
        self.envelopes: list[dict[str, Any]] = []

    async def publish(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)


@contextmanager
def collect_events() -> Iterator[EventCollector]:
    """Registra um EventCollector como EventBus do processo enquanto o bloco roda."""
    previous = events.get_event_bus()
    collector = EventCollector()
    events.set_event_bus(collector)
    try:
        yield collector
    finally:
        events.set_event_bus(previous)


async def relay_to_instances(
    redis_url: str, envelopes: Sequence[dict[str, Any]], *, timeout: float = 5.0
) -> int:
    """Publica os envelopes no canal das instâncias. Retorna quantas instâncias receberam (as
    assinantes do canal no momento da última publicação). Falha -> RelayError."""
    client: Redis | None = None
    try:
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            protocol=2,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            # A CLI não espera o Redis voltar: poucas tentativas e o aviso sai logo.
            retry=Retry(ExponentialBackoff(cap=0.5, base=0.1), retries=2),
        )
        publisher = RedisPublisher(client)
        receivers = 0
        for envelope in envelopes:
            receivers = await publisher.publish(envelope)
        return receivers
    except Exception as exc:
        raise RelayError(
            f"Redis inacessível em {redact_url(redis_url)} ({type(exc).__name__})"
        ) from exc
    finally:
        if client is not None:
            await client.aclose()
