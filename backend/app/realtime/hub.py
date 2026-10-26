"""Conexões WebSocket locais desta instância.

Cada conexão tem uma fila de saída e uma tarefa escritora própria: um cliente lento nunca atrasa os
demais. Se a fila de um cliente estoura, ele é desconectado (1013) e reconecta/recarrega sozinho.
O hub também encerra conexões por iniciativa do servidor (sessão vencida, usuário desativado,
ressincronização): fecha o socket e o handler da conexão faz a limpeza de sempre ao sair.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Collection, Coroutine
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.realtime.codec import dumps
from app.realtime.events import EventType
from app.schemas import OnlineUser

log = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 512
CLOSE_TOO_SLOW = 1013  # "Try Again Later"


class SocketLike(Protocol):
    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class Connection:
    def __init__(
        self,
        websocket: SocketLike,
        *,
        user_id: int,
        user_name: str,
        queue_size: int,
        on_overflow: Callable[[Connection], None] | None = None,
        token_version: int = 0,
    ) -> None:
        self.conn_id = uuid4().hex
        self.user_id = user_id
        self.user_name = user_name
        self.token_version = token_version  # claim "tv" do token usado na conexão
        self._ws = websocket
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._writer: asyncio.Task[None] | None = None
        self._on_overflow = on_overflow
        self._timers: list[asyncio.TimerHandle] = []
        self.closed = False  # não enfileira mais nada (fechando, caiu ou estourou a fila)

    def __repr__(self) -> str:
        return f"<Connection {self.conn_id[:8]} user={self.user_id}>"

    def start(self, first_message: str) -> None:
        """Inicia a escrita: `first_message` (HELLO) sai antes de tudo o que já está na fila."""
        if self._writer is None:
            self._writer = asyncio.create_task(
                self._write_loop(first_message), name=f"realtime:writer:{self.conn_id[:8]}"
            )

    def enqueue(self, text: str) -> bool:
        """True se a mensagem entrou na fila. Fila cheia = cliente lento: marca como fechada e
        avisa o hub, que encerra a conexão (respostas diretas e broadcasts passam por aqui)."""
        if self.closed:
            return False
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            self.closed = True
            if self._on_overflow is not None:
                self._on_overflow(self)
            return False
        return True

    def send(self, envelope: dict[str, Any]) -> bool:
        return self.enqueue(dumps(envelope))

    def call_later(self, delay: float, callback: Callable[..., None], *args: Any) -> None:
        """Agenda `callback` enquanto a conexão viver (cancelado em stop())."""
        if not self.closed:
            loop = asyncio.get_running_loop()
            self._timers.append(loop.call_later(max(0.0, delay), callback, *args))

    async def _write_loop(self, first_message: str) -> None:
        try:
            await self._ws.send_text(first_message)
            while True:
                await self._ws.send_text(await self._queue.get())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # cliente sumiu no meio do envio
            self.closed = True
            log.debug("Escrita encerrada para %r: %s", self, exc)

    async def stop(self) -> None:
        """Para a escrita sem mandar frame de fechamento (cliente já desconectou)."""
        self.closed = True
        timers, self._timers = self._timers, []
        for timer in timers:
            timer.cancel()
        writer = self._writer
        if writer is not None and not writer.done():
            writer.cancel()
            await asyncio.wait({writer})

    async def close(self, code: int, reason: str) -> None:
        await self.stop()
        try:
            await self._ws.close(code=code, reason=reason)
        except Exception:  # já fechado pelo cliente/servidor
            pass


class ConnectionHub:
    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._connections: dict[str, Connection] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._empty = asyncio.Event()
        self._empty.set()
        self._observers: list[Callable[[dict[str, Any]], None]] = []
        # Última lista de presença entregue aos clientes locais (o heartbeat compara com ela).
        self.last_online: list[dict[str, Any]] | None = None

    def __len__(self) -> int:
        return len(self._connections)

    def add(
        self, websocket: SocketLike, *, user_id: int, user_name: str, token_version: int = 0
    ) -> Connection:
        """Registra a conexão: ela passa a receber broadcasts (enfileirados até start())."""
        conn = Connection(
            websocket,
            user_id=user_id,
            user_name=user_name,
            queue_size=self._queue_size,
            on_overflow=self._close_slow,
            token_version=token_version,
        )
        self._connections[conn.conn_id] = conn
        self._empty.clear()
        return conn

    def remove(self, conn: Connection) -> bool:
        removed = self._connections.pop(conn.conn_id, None) is not None
        if not self._connections:
            self._empty.set()
        return removed

    def observe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Chamado para todo envelope difundido (locais e vindos de outras instâncias)."""
        self._observers.append(callback)

    def broadcast(self, envelope: dict[str, Any]) -> int:
        if envelope.get("type") == EventType.PRESENCE:
            self.last_online = (envelope.get("data") or {}).get("online")
        for callback in self._observers:
            try:
                callback(envelope)
            except Exception:
                log.exception("Observador de eventos falhou")
        text = dumps(envelope)
        return sum(conn.enqueue(text) for conn in list(self._connections.values()))

    def send_to(self, conn_id: str, envelope: dict[str, Any]) -> bool:
        conn = self._connections.get(conn_id)
        return conn is not None and conn.send(envelope)

    def local_user_ids(self) -> set[int]:
        return {conn.user_id for conn in self._connections.values()}

    def local_users(self) -> list[OnlineUser]:
        counts = Counter(conn.user_id for conn in self._connections.values())
        names = {conn.user_id: conn.user_name for conn in self._connections.values()}
        return [
            OnlineUser(id=user_id, full_name=names[user_id], connections=n)
            for user_id, n in counts.items()
        ]

    async def close_all(self, code: int, reason: str) -> None:
        await asyncio.gather(
            *(conn.close(code, reason) for conn in list(self._connections.values())),
            return_exceptions=True,
        )

    def close_all_soon(self, code: int, reason: str) -> int:
        """Fecha todas as conexões em segundo plano (quem chama não espera). Retorna quantas."""
        targets = list(self._connections.values())
        return sum(self._close_soon(conn, code, reason) for conn in targets)

    def close_users_soon(self, user_ids: Collection[int], code: int, reason: str) -> int:
        """Fecha em segundo plano as conexões dos usuários dados. Retorna quantas."""
        return self.close_where_soon(lambda c: c.user_id in user_ids, code, reason)

    def close_where_soon(
        self, predicate: Callable[[Connection], bool], code: int, reason: str
    ) -> int:
        """Fecha em segundo plano as conexões que satisfazem `predicate`. Retorna quantas."""
        targets = [c for c in self._connections.values() if predicate(c)]
        return sum(self._close_soon(conn, code, reason) for conn in targets)

    def close_at(self, conn: Connection, when: datetime, code: int, reason: str) -> None:
        """Fecha a conexão no instante `when` (aware), ex.: quando o JWT dela vence."""
        delay = (when - datetime.now(timezone.utc)).total_seconds()
        conn.call_later(delay, self._close_soon, conn, code, reason)

    async def wait_empty(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._empty.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def aclose(self) -> None:
        """Encerra o que sobrou (escritores e fechamentos pendentes). Usado no shutdown."""
        for conn in list(self._connections.values()):
            await conn.stop()
        pending = [task for task in self._background if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending)

    def _close_soon(self, conn: Connection, code: int, reason: str) -> bool:
        if conn.closed or self._connections.get(conn.conn_id) is not conn:
            return False  # já está saindo: o handler da conexão cuida do resto
        conn.closed = True  # nada mais é enfileirado para quem está sendo desligado
        self._spawn(conn.close(code, reason), f"realtime:close:{conn.conn_id[:8]}")
        return True

    def _close_slow(self, conn: Connection) -> None:
        log.warning("Cliente lento demais (%r): fila cheia, desconectando", conn)
        self._spawn(
            conn.close(CLOSE_TOO_SLOW, "Conexão lenta demais"), f"realtime:close:{conn.conn_id[:8]}"
        )

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
