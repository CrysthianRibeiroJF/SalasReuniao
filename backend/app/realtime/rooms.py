"""Situação das salas (existe / ativa) para validar LOCK_SLOT sem ir ao banco a cada clique.

Salas são poucas: carrega todas numa consulta e mantém em memória. ROOM_CREATED / ROOM_UPDATED
(de qualquer instância, pelo bus) trazem a sala como ficou depois do commit: esse estado é aplicado
na hora, sem nova consulta, e o LOCK_SLOT seguinte não espera o banco. Evento sem esses dados
invalida o cache (recarrega no próximo uso) e, por garantia, tudo é recarregado após `ttl` segundos
(isso também corrige eventos de instâncias diferentes que chegam fora de ordem). O cache é só uma
pré-checagem: o REST valida a sala de novo no banco ao salvar.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Room
from app.realtime.events import EventType

_ROOM_EVENTS = frozenset({EventType.ROOM_CREATED, EventType.ROOM_UPDATED})


def room_state(envelope: dict[str, Any]) -> tuple[int, bool] | None:
    """(id, is_active) de um ROOM_CREATED/ROOM_UPDATED com a sala completa, senão None."""
    data = envelope.get("data")
    room = data.get("room") if isinstance(data, dict) else None
    if not isinstance(room, dict):
        return None
    room_id, active = room.get("id"), room.get("is_active")
    if isinstance(room_id, bool) or not isinstance(room_id, int) or not isinstance(active, bool):
        return None
    return room_id, active


class RoomDirectory:
    def __init__(self, *, ttl: float = 30.0, miss_reload_interval: float = 2.0) -> None:
        self._ttl = ttl
        self._miss_reload_interval = miss_reload_interval
        self._rooms: dict[int, bool] = {}
        self._loaded_at: float | None = None
        # Todo evento de sala avança a geração. Uma carga guarda a geração em que começou: o que
        # chegou durante a consulta não se perde, e uma carga antiga nunca sobrescreve uma nova.
        self._generation = 0
        self._loaded_generation = -1
        self._invalidated_generation = 0  # último evento sem dados da sala (exige recarga)
        self._applied: dict[int, tuple[int, bool]] = {}  # sala -> (geração, ativa) vinda de evento
        self._lock = asyncio.Lock()

    @property
    def stale(self) -> bool:
        return (
            self._loaded_at is None
            or self._invalidated_generation > self._loaded_generation
            or time.monotonic() - self._loaded_at > self._ttl
        )

    def invalidate(self) -> None:
        self._generation += 1
        self._invalidated_generation = self._generation

    def observe(self, envelope: dict[str, Any]) -> None:
        if envelope.get("type") not in _ROOM_EVENTS:
            return
        state = room_state(envelope)
        if state is None:
            self.invalidate()
            return
        self._generation += 1
        room_id, active = state
        self._rooms[room_id] = active
        self._applied[room_id] = (self._generation, active)

    async def load(self, session: AsyncSession) -> None:
        generation = self._generation
        result = await session.execute(select(Room.id, Room.is_active))
        if generation < self._loaded_generation:
            return  # uma carga que começou depois desta já terminou: o retrato dela é mais novo
        rooms = {room_id: bool(active) for room_id, active in result.all()}
        # Eventos de antes da consulta já estão no retrato; os de durante são mais novos que ele.
        self._applied = {rid: st for rid, st in self._applied.items() if st[0] > generation}
        for room_id, (_, active) in self._applied.items():
            rooms[room_id] = active
        self._rooms = rooms
        self._loaded_at = time.monotonic()
        self._loaded_generation = generation

    async def is_active(self, room_id: int) -> bool | None:
        """None = sala inexistente."""
        if self._needs_load(room_id):
            async with self._lock:
                if self._needs_load(room_id):
                    async with SessionLocal() as session:
                        await self.load(session)
        return self._rooms.get(room_id)

    def _needs_load(self, room_id: int) -> bool:
        if self.stale:
            return True
        # Sala recém-criada cujo evento ainda não chegou: recarrega, mas sem martelar o banco.
        assert self._loaded_at is not None
        return (
            room_id not in self._rooms
            and time.monotonic() - self._loaded_at > self._miss_reload_interval
        )
