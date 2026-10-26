"""Estado do tempo real de UMA instância: travas, bus, hub, presença e tarefas de fundo."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from app.config import settings
from app.realtime.accounts import active_user_ids
from app.realtime.bus import MemoryBus, RedisBus
from app.realtime.codec import dumps
from app.realtime.events import EventType, make_envelope
from app.realtime.hub import Connection, ConnectionHub, SocketLike
from app.realtime.locks import DetailedLockStore, utcnow
from app.realtime.presence import PresenceStore
from app.realtime.registry import LockInfo
from app.realtime.rooms import RoomDirectory

log = logging.getLogger(__name__)

SWEEP_INTERVAL = 2.0
HEARTBEAT_INTERVAL = 10.0
# Com heartbeat de 10 s, usuários de uma instância morta somem em 20–30 s.
PRESENCE_TTL = 20.0
# Usuário desativado/removido ou com a senha trocada perde o tempo real em até 1 minuto (o REST já
# recusa na hora).
REVALIDATE_INTERVAL = 60.0
_SHUTDOWN_DRAIN_TIMEOUT = 5.0

# Códigos de fechamento: 4401 -> sessão vencida/inválida/revogada, o frontend volta ao login;
# 4403 -> conta desativada ou removida; 1012 -> reconecta e recarrega a grade.
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_SERVICE_RESTART = 1012
SESSION_EXPIRED_REASON = "Sessão expirada ou inválida. Entre novamente."
SESSION_REVOKED_REASON = "Sua sessão foi encerrada porque a senha foi alterada. Entre novamente."
ACCESS_REVOKED_REASON = "Seu acesso foi desativado. Fale com o administrador."
RESYNC_REASON = "Reconectando para sincronizar os dados."

# ids pedidos -> token_version atual de cada um que ainda existe e está ativo
ActiveUserCheck = Callable[[Collection[int]], Awaitable[dict[int, int]]]


def lock_payload(lock: LockInfo) -> dict[str, Any]:
    return lock.to_out().model_dump(mode="json")


class RealtimeRuntime:
    def __init__(
        self,
        *,
        backend: str,
        store: DetailedLockStore,
        bus: MemoryBus | RedisBus,
        hub: ConnectionHub,
        presence: PresenceStore,
        node_id: str,
        redis: Redis | None = None,
        rooms: RoomDirectory | None = None,
        sweep_interval: float = SWEEP_INTERVAL,
        heartbeat_interval: float | None = HEARTBEAT_INTERVAL,
        revalidate_interval: float | None = REVALIDATE_INTERVAL,
        active_users: ActiveUserCheck = active_user_ids,
    ) -> None:
        self.backend = backend
        self.store = store
        self.bus = bus
        self.hub = hub
        self.presence = presence
        self.rooms = rooms or RoomDirectory()
        hub.observe(self.rooms.observe)
        self.node_id = node_id
        self.instance_id = settings.instance_id
        self._redis = redis
        self._sweep_interval = sweep_interval
        self._heartbeat_interval = heartbeat_interval
        self._revalidate_interval = revalidate_interval
        self._active_users = active_users
        self._tasks: list[asyncio.Task[None]] = []
        self._presence_lock = asyncio.Lock()
        self.accepting = False
        if isinstance(bus, RedisBus):
            bus.on_resubscribe = self.resync_local_clients

    # ------------------------------------------------------------------ ciclo de vida

    async def start(self) -> None:
        await self.bus.start()
        await self.presence.publish_local(self.hub.local_users())
        self._tasks.append(asyncio.create_task(self._sweep_loop(), name="realtime:lock-sweeper"))
        if self._heartbeat_interval:
            self._tasks.append(
                asyncio.create_task(self._heartbeat_loop(), name="realtime:presence-heartbeat")
            )
        if self._revalidate_interval:
            self._tasks.append(
                asyncio.create_task(self._revalidate_loop(), name="realtime:user-revalidation")
            )
        self.accepting = True

    async def stop(self) -> None:
        self.accepting = False
        if len(self.hub):
            # Os handlers de cada conexão liberam as travas e atualizam a presença ao sair.
            await self.hub.close_all(CLOSE_SERVICE_RESTART, "Servidor reiniciando")
            if not await self.hub.wait_empty(_SHUTDOWN_DRAIN_TIMEOUT):
                log.warning("%d conexão(ões) não encerraram a tempo", len(self.hub))
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.bus.stop()
        await self.hub.aclose()
        try:
            await self.presence.clear()
        except Exception:
            log.warning("Não foi possível remover a presença desta instância", exc_info=True)
        if self._redis is not None:
            await self._redis.aclose()

    # ------------------------------------------------------------------ conexões

    async def open_connection(
        self,
        websocket: SocketLike,
        *,
        user_id: int,
        user_name: str,
        expires_at: datetime | None = None,
        token_version: int = 0,
    ) -> Connection:
        """Registra antes de tirar o retrato do HELLO: nenhum evento se perde no meio.

        `expires_at` (vencimento do JWT): nesse instante a conexão é fechada com 4401.
        `token_version` (claim "tv"): se a versão do usuário mudar (senha trocada), a revalidação
        periódica fecha a conexão com 4401.
        """
        conn = self.hub.add(
            websocket, user_id=user_id, user_name=user_name, token_version=token_version
        )
        try:
            online = await self._sync_presence()
            locks = await self.store.list_active()
        except Exception:
            self.hub.remove(conn)
            await self.presence_changed()
            raise
        hello = make_envelope(
            EventType.HELLO,
            {
                "user": {"id": user_id, "full_name": user_name},
                "conn_id": conn.conn_id,
                "instance_id": self.instance_id,
                "server_time": utcnow().isoformat(),
                "locks": [lock_payload(lock) for lock in locks],
                "online": online,
            },
        )
        if expires_at is not None:
            self.hub.close_at(conn, expires_at, CLOSE_UNAUTHORIZED, SESSION_EXPIRED_REASON)
        conn.start(dumps(hello))
        await self.publish(make_envelope(EventType.PRESENCE, {"online": online}))
        return conn

    async def close_connection(self, conn: Connection) -> None:
        if not self.hub.remove(conn):
            return
        await conn.stop()
        try:
            released = await self.store.release_connection(conn.conn_id)
        except Exception:
            log.exception("Falha ao liberar as travas da conexão %s", conn.conn_id)
            released = []
        for lock in released:
            await self.announce_unlocked(lock, "disconnected")
        await self.presence_changed()

    # ------------------------------------------------------------------ eventos

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Nunca levanta: falha de entrega não pode derrubar o socket nem a varredura."""
        try:
            await self.bus.publish(envelope)
        except Exception:
            log.exception("Falha ao publicar evento %s", envelope.get("type"))

    async def announce_locked(self, lock: LockInfo) -> None:
        await self.publish(make_envelope(EventType.SLOT_LOCKED, {"lock": lock_payload(lock)}))

    async def announce_unlocked(self, lock: LockInfo, reason: str) -> None:
        await self.publish(
            make_envelope(EventType.SLOT_UNLOCKED, {"lock": lock_payload(lock), "reason": reason})
        )

    async def presence_changed(self) -> None:
        try:
            online = await self._sync_presence()
        except Exception:
            log.exception("Falha ao atualizar a presença")
            return
        await self.publish(make_envelope(EventType.PRESENCE, {"online": online}))

    async def revalidate_users(self) -> int:
        """Fecha as conexões de usuários desativados ou removidos (4403) e as de tokens revogados
        pela troca de senha (4401, versão do token anterior à atual). Retorna quantas.

        Uma consulta por rodada para todos os usuários conectados nesta instância (nenhuma se não
        houver conexões); a sessão do banco é aberta e devolvida dentro da própria consulta.
        """
        user_ids = self.hub.local_user_ids()
        if not user_ids:
            return 0
        versions = await self._active_users(user_ids)
        disabled = user_ids - set(versions)
        closed = 0
        if disabled:
            n = self.hub.close_users_soon(disabled, CLOSE_FORBIDDEN, ACCESS_REVOKED_REASON)
            log.info("Usuário(s) %s sem acesso: %d conexão(ões) encerrada(s)", sorted(disabled), n)
            closed += n
        # "<" e não "!=": a versão só cresce, e uma conexão aberta com um token emitido depois
        # desta consulta (versão maior que a lida aqui) é legítima.
        stale = self.hub.close_where_soon(
            lambda c: c.user_id in versions and c.token_version < versions[c.user_id],
            CLOSE_UNAUTHORIZED,
            SESSION_REVOKED_REASON,
        )
        if stale:
            log.info("Sessões revogadas (senha alterada): %d conexão(ões) encerrada(s)", stale)
            closed += stale
        return closed

    async def redis_status(self, timeout: float = 2.0) -> str:
        """'ok' | 'down' (PING no Redis desta instância) ou 'disabled' sem Redis. Nunca levanta."""
        if self._redis is None:
            return "disabled"
        try:
            async with asyncio.timeout(timeout):
                await self._redis.ping()
        except Exception:
            return "down"
        return "ok"

    def resync_local_clients(self) -> int:
        """A assinatura do Redis voltou depois de uma queda e os eventos do período podem ter se
        perdido: os clientes locais reconectam (1012) e recarregam salas e semana no HELLO."""
        closed = self.hub.close_all_soon(CLOSE_SERVICE_RESTART, RESYNC_REASON)
        if closed:
            log.warning(
                "Eventos podem ter se perdido durante a queda do Redis: %d conexão(ões) "
                "reiniciada(s) para ressincronizar",
                closed,
            )
        return closed

    async def _sync_presence(self) -> list[dict[str, Any]]:
        async with self._presence_lock:
            await self.presence.publish_local(self.hub.local_users())
            return [user.model_dump(mode="json") for user in await self.presence.online()]

    # ------------------------------------------------------------------ tarefas de fundo

    async def _sweep_loop(self) -> None:
        failures = 0
        while True:
            await asyncio.sleep(self._sweep_interval)
            try:
                expired = await self.store.pop_expired()
            except Exception:
                failures += 1
                if failures == 1 or failures % 30 == 0:
                    log.exception("Falha na varredura de travas expiradas (%d seguidas)", failures)
                continue
            failures = 0
            for lock in expired:
                await self.announce_unlocked(lock, "expired")

    async def _heartbeat_loop(self) -> None:
        assert self._heartbeat_interval
        failures = 0
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                online = await self._sync_presence()
            except Exception:
                failures += 1
                if failures == 1 or failures % 30 == 0:
                    log.exception("Falha no heartbeat de presença (%d seguidas)", failures)
                continue
            failures = 0
            # Instância que morreu sem avisar (TTL venceu) ou PRESENCE que chegou fora de ordem:
            # cada instância corrige os SEUS clientes, sem passar pelo bus.
            if online != self.hub.last_online:
                self.hub.broadcast(make_envelope(EventType.PRESENCE, {"online": online}))

    async def _revalidate_loop(self) -> None:
        assert self._revalidate_interval
        failures = 0
        while True:
            await asyncio.sleep(self._revalidate_interval)
            try:
                await self.revalidate_users()
            except Exception:
                failures += 1
                if failures == 1 or failures % 30 == 0:
                    log.exception(
                        "Falha ao revalidar os usuários conectados (%d seguidas)", failures
                    )
                continue
            failures = 0
