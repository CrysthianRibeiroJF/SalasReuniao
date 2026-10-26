"""Salas livres num intervalo (filtros da grade, avisos RF06 e alternativas de conflito)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, BookingStatus, Room, RoomType
from app.services.rooms import effective_video_rank, room_order_by
from app.timeutil import day_bounds


async def free_rooms(
    session: AsyncSession,
    start_dt: datetime,
    end_dt: datetime,
    room_type: RoomType | None = None,
    exclude_booking_id: uuid.UUID | str | None = None,
) -> list[Room]:
    """Salas ativas sem reserva ativa sobreposta a [start_dt, end_dt), na ordem da grade.

    `room_type` filtra pelo tipo EFETIVO (sala de vídeo em manutenção conta como NORMAL).
    `exclude_booking_id` ignora uma reserva (a que está sendo remanejada).
    """
    busy = [
        Booking.room_id == Room.id,
        Booking.status != BookingStatus.CANCELLED,
        Booking.start_time < end_dt,
        Booking.end_time > start_dt,
    ]
    if exclude_booking_id is not None:
        busy.append(Booking.id != _as_uuid(exclude_booking_id))
    stmt = (
        select(Room)
        .where(Room.is_active.is_(True), ~exists().where(*busy))
        .order_by(*room_order_by())
    )
    if room_type is not None:
        rank = 0 if room_type == RoomType.VIDEO else 1
        stmt = stmt.where(effective_video_rank() == rank)
    return list((await session.scalars(stmt)).all())


async def full_day_free_rooms(session: AsyncSession, d: date) -> list[Room]:
    """Salas sem nenhuma reserva ativa no horário de funcionamento do dia ("Livres Dia Inteiro")."""
    start_dt, end_dt = day_bounds(d)
    return await free_rooms(session, start_dt, end_dt)


async def alternative_rooms(
    session: AsyncSession,
    start_dt: datetime,
    end_dt: datetime,
    *,
    requires_video: bool,
    exclude_room_id: int | None = None,
    exclude_booking_id: uuid.UUID | str | None = None,
) -> list[Room]:
    """Salas livres no intervalo inteiro, com o tipo efetivo desejado primeiro (5.4, regra 5)."""
    preferred = RoomType.VIDEO if requires_video else RoomType.NORMAL
    rooms = [
        r
        for r in await free_rooms(session, start_dt, end_dt, exclude_booking_id=exclude_booking_id)
        if r.id != exclude_room_id
    ]
    # sort estável: dentro de cada grupo mantém a ordem da grade
    return sorted(rooms, key=lambda r: r.effective_type != preferred)


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
