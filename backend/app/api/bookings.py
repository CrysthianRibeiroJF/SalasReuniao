"""Reservas: grade, busca, criação/edição/cancelamento, status e histórico."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas import (
    BookingCancel,
    BookingCreate,
    BookingLogOut,
    BookingOut,
    BookingStatusIn,
    BookingUpdate,
    BookingWriteResult,
    LocalDate,
)
from app.services import audit as audit_service
from app.services import bookings as bookings_service
from app.services.errors import unprocessable

router = APIRouter(prefix="/api/bookings", tags=["bookings"])

MAX_RANGE_DAYS = 31


@router.get("", response_model=list[BookingOut])
async def list_bookings(
    session: SessionDep,
    _: CurrentUser,
    day: Annotated[LocalDate | None, Query(alias="date")] = None,
    start_date: LocalDate | None = None,
    end_date: LocalDate | None = None,
    include_cancelled: bool = False,
) -> list[BookingOut]:
    start, end = _date_range(day, start_date, end_date)
    return await bookings_service.list_bookings(session, start, end, include_cancelled)


@router.get("/search", response_model=list[BookingOut])
async def search_bookings(
    session: SessionDep,
    _: CurrentUser,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[BookingOut]:
    return await bookings_service.search_bookings(session, q, limit)


@router.get("/{booking_id}", response_model=BookingOut)
async def get_booking(booking_id: uuid.UUID, session: SessionDep, _: CurrentUser) -> BookingOut:
    return await bookings_service.get_booking(session, booking_id)


@router.post("", response_model=BookingWriteResult, status_code=status.HTTP_201_CREATED)
async def create_booking(
    data: BookingCreate, session: SessionDep, user: CurrentUser
) -> BookingWriteResult:
    return await bookings_service.create_booking(session, data, user)


@router.patch("/{booking_id}", response_model=BookingWriteResult)
async def update_booking(
    booking_id: uuid.UUID, data: BookingUpdate, session: SessionDep, user: CurrentUser
) -> BookingWriteResult:
    return await bookings_service.update_booking(session, booking_id, data, user)


@router.post("/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(
    booking_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    data: BookingCancel | None = None,
) -> BookingOut:
    return await bookings_service.cancel_booking(session, booking_id, data or BookingCancel(), user)


@router.post("/{booking_id}/status", response_model=BookingOut)
async def set_status(
    booking_id: uuid.UUID, data: BookingStatusIn, session: SessionDep, user: CurrentUser
) -> BookingOut:
    return await bookings_service.set_booking_status(session, booking_id, data, user)


@router.get("/{booking_id}/logs", response_model=list[BookingLogOut])
async def booking_logs(
    booking_id: uuid.UUID, session: SessionDep, _: CurrentUser
) -> list[BookingLogOut]:
    return await audit_service.booking_logs(session, booking_id)


def _date_range(day: date | None, start: date | None, end: date | None) -> tuple[date, date]:
    if day is not None:
        return day, day
    if start is None:
        raise unprocessable(
            "INVALID_DATE_RANGE", "Informe a data (date) ou o período (start_date e end_date)."
        )
    end = end or start
    if end < start:
        raise unprocessable(
            "INVALID_DATE_RANGE", "A data final deve ser igual ou posterior à inicial."
        )
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise unprocessable(
            "DATE_RANGE_TOO_LARGE", f"Consulte no máximo {MAX_RANGE_DAYS} dias por vez."
        )
    return start, end
