"""Disponibilidade: salas livres num horário e salas livres o dia inteiro."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep
from app.models import PeriodType, RoomType
from app.schemas import LocalDate, LocalTime, RoomOut
from app.serializers import room_out
from app.services import availability as availability_service
from app.services.errors import unprocessable
from app.timeutil import resolve_period

router = APIRouter(prefix="/api/availability", tags=["availability"])


@router.get("/free-rooms", response_model=list[RoomOut])
async def free_rooms(
    session: SessionDep,
    _: CurrentUser,
    day: Annotated[LocalDate, Query(alias="date")],
    start: LocalTime | None = None,
    end: LocalTime | None = None,
    period_type: PeriodType | None = None,
    room_type: RoomType | None = None,
    exclude_booking_id: uuid.UUID | None = None,
) -> list[RoomOut]:
    """Salas ativas sem reserva ativa no intervalo. Aceita `period_type` no lugar de start/end."""
    period = period_type or PeriodType.CUSTOM
    if period == PeriodType.CUSTOM and (start is None or end is None):
        raise unprocessable("INVALID_TIME", "Informe início e fim (ou o tipo de período).")
    try:
        start_dt, end_dt, _full_day = resolve_period(period, day, start, end)
    except ValueError:
        raise unprocessable(
            "INVALID_INTERVAL", "O horário de término deve ser depois do início."
        ) from None
    rooms = await availability_service.free_rooms(
        session, start_dt, end_dt, room_type=room_type, exclude_booking_id=exclude_booking_id
    )
    return [room_out(r) for r in rooms]


@router.get("/full-day", response_model=list[RoomOut])
async def full_day(
    session: SessionDep, _: CurrentUser, day: Annotated[LocalDate, Query(alias="date")]
) -> list[RoomOut]:
    return [room_out(r) for r in await availability_service.full_day_free_rooms(session, day)]
