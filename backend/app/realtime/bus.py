"""EventBus: entrega envelopes a todas as conexões de todas as instâncias.

- MemoryBus: uma instância só, entrega direto ao hub local.
- RedisBus: entrega ao hub local na hora e publica no canal `agenda:events`; cada instância tem UMA
  tarefa assinante que repassa ao seu hub as mensagens das OUTRAS instâncias (a própria origem é
  ignorada, então nada chega duas vezes localmente). Reconecta sozinha com backoff exponencial.
  Toda vez que a assinatura volta depois de uma queda os eventos do intervalo podem ter se perdido:
  `on_resubscribe` avisa o runtime, que manda os clientes locais reconectarem e recarregarem.
- RedisPublisher: só publica (processos sem WebSocket, ex.: a CLI) com uma origem própria.

Mensagem no canal: {"origin": <node_id de quem publicou>, "envelope": {type, data, ts, ...}}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.realtime.codec import dumps

log = logging.getLogger(__name__)

CHANNEL = "agenda:events"


class LocalHub(Protocol):
    def broadcast(self, envelope: dict[str, Any]) -> int: ...


def encode_message(origin: str, envelope: dict[str, Any]) -> str:
    return dumps({"origin": origin, "envelope": envelope})


def external_origin(kind: str = "cli") -> str:
    """Origem de quem publica sem assinar. node_id de instância é `<instance_id>-<6 hex>`, então
    `cli:<32 hex>` nunca coincide com o de uma instância (nenhuma descarta a mensagem)."""
    return f"{kind}:{uuid4().hex}"


class MemoryBus:
    def __init__(self, hub: LocalHub) -> None:
        self._hub = hub

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, envelope: dict[str, Any]) -> None:
        self._hub.broadcast(envelope)


class RedisPublisher:
    """Publica envelopes no canal sem entregar a nenhum hub local."""

    def __init__(self, redis: Redis, *, origin: str | None = None, channel: str = CHANNEL) -> None:
        self._redis = redis
        self.origin = origin or external_origin()
        self._channel = channel

    async def publish(self, envelope: dict[str, Any]) -> int:
        """Retorna quantos assinantes (instâncias) receberam a mensagem."""
        return await self._redis.publish(self._channel, encode_message(self.origin, envelope))


class RedisBus:
    def __init__(
        self,
        redis: Redis,
        hub: LocalHub,
        *,
        node_id: str,
        channel: str = CHANNEL,
        min_backoff: float = 0.5,
        max_backoff: float = 10.0,
        on_resubscribe: Callable[[], object] | None = None,
    ) -> None:
        self._redis = redis
        self._hub = hub
        self._node_id = node_id
        self._channel = channel
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._task: asyncio.Task[None] | None = None
        self._subscribed = asyncio.Event()
        self._ever_subscribed = False
        # Chamado (síncrono) a cada assinatura restabelecida após uma queda; nunca na primeira.
        self.on_resubscribe = on_resubscribe

    @property
    def subscribed(self) -> bool:
        return self._subscribed.is_set()

    async def start(self, timeout: float = 5.0) -> None:
        """Inicia a assinatura e espera a confirmação (eventos de outras instâncias já chegam)."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="realtime:redis-subscriber")
        try:
            await asyncio.wait_for(self._subscribed.wait(), timeout)
        except TimeoutError:
            log.warning(
                "Assinatura de %s não confirmada em %.0fs; seguindo com novas tentativas",
                self._channel, timeout,
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
        self._subscribed.clear()

    async def publish(self, envelope: dict[str, Any]) -> None:
        self._hub.broadcast(envelope)
        await self._redis.publish(self._channel, encode_message(self._node_id, envelope))

    async def _run(self) -> None:
        backoff = self._min_backoff
        failures = 0
        while True:
            pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            connection, original_retry = None, None
            try:
                await pubsub.subscribe(self._channel)
                # Sem novas tentativas automáticas do redis-py nesta conexão: ele refaria conexão e
                # SUBSCRIBE em silêncio e os eventos do intervalo se perderiam sem ninguém saber.
                # Assim toda queda chega a este laço, que reassina e avisa (on_resubscribe).
                connection = pubsub.connection
                original_retry = getattr(connection, "retry", None)
                if original_retry is not None:
                    connection.retry = Retry(NoBackoff(), 0)
                if failures:
                    log.info("Assinatura de %s restabelecida", self._channel)
                failures, backoff = 0, self._min_backoff
                self._subscribed.set()
                if self._ever_subscribed:
                    self._resubscribed()
                self._ever_subscribed = True
                await self._listen(pubsub)
            except Exception as exc:
                failures += 1
                self._subscribed.clear()
                delay = backoff * random.uniform(0.8, 1.2)
                log.warning(
                    "Assinatura de %s perdida (%s: %s); nova tentativa em %.1fs",
                    self._channel, type(exc).__name__, exc, delay,
                )
                await _close_quietly(pubsub, connection, original_retry)
                connection = None
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, self._max_backoff)
            finally:
                await _close_quietly(pubsub, connection, original_retry)

    def _resubscribed(self) -> None:
        callback = self.on_resubscribe
        if callback is None:
            return
        try:
            callback()
        except Exception:
            log.exception("Falha ao tratar a assinatura restabelecida de %s", self._channel)

    async def _listen(self, pubsub: PubSub) -> None:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None and message.get("type") == "message":
                self._deliver(message["data"])

    def _deliver(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
            origin, envelope = message["origin"], message["envelope"]
        except (ValueError, KeyError, TypeError):
            log.warning("Mensagem inválida no canal %s ignorada: %.200r", self._channel, raw)
            return
        if origin == self._node_id:
            return  # já entregue localmente em publish()
        if not isinstance(envelope, dict) or not isinstance(envelope.get("type"), str):
            log.warning("Envelope inválido no canal %s ignorado: %.200r", self._channel, raw)
            return
        self._hub.broadcast(envelope)


async def _close_quietly(
    pubsub: PubSub, connection: Any = None, original_retry: Retry | None = None
) -> None:
    if connection is not None and original_retry is not None:
        connection.retry = original_retry  # a conexão volta ao pool como veio
    try:
        await asyncio.wait_for(pubsub.aclose(), 2.0)
    except Exception:
        pass
