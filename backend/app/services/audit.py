"""Histórico de reservas e tela de auditoria (RF07)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Booking, BookingLog, LogAction, Room
from app.schemas import AuditEntryOut, BookingLogOut
from app.serializers import log_out, user_brief
from app.services.errors import not_found
from app.timeutil import calendar_day_bounds, to_local

MAX_AUDIT_LIMIT = 500


async def booking_logs(session: AsyncSession, booking_id: uuid.UUID | str) -> list[BookingLogOut]:
    """Histórico de uma reserva, do mais antigo para o mais recente."""
    try:
        bid = booking_id if isinstance(booking_id, uuid.UUID) else uuid.UUID(str(booking_id))
    except ValueError:
        bid = None
    if bid is None or await session.get(Booking, bid) is None:
        raise not_found("BOOKING_NOT_FOUND", "Reserva não encontrada.")
    stmt = (
        select(BookingLog)
        .where(BookingLog.booking_id == bid)
        .options(selectinload(BookingLog.user))
        .order_by(BookingLog.created_at, BookingLog.id)
    )
    return [log_out(entry) for entry in (await session.scalars(stmt)).all()]


async def list_audit(
    session: AsyncSession,
    *,
    day: date | None = None,
    booking_date: date | None = None,
    user_id: int | None = None,
    action: LogAction | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEntryOut]:
    """Entradas mais recentes primeiro.

    `day` filtra pelo dia (local) em que a ação aconteceu; `booking_date`, pelo dia em que a
    reserva estava NAQUELA entrada (antes ou depois da alteração): uma reserva criada para 18/11 e
    depois remarcada para 19/11 aparece nos dois dias. O resumo de cada linha (sala, data,
    solicitante, chamado) também é o da própria entrada, não o estado atual da reserva.
    """
    stmt = (
        select(
            BookingLog, Booking.ticket_number, Booking.requester_name, Booking.start_time, Room.name
        )
        .join(Booking, Booking.id == BookingLog.booking_id)
        .join(Room, Room.id == Booking.room_id)
        .options(selectinload(BookingLog.user))
        .order_by(BookingLog.created_at.desc(), BookingLog.id.desc())
        .limit(max(1, min(limit, MAX_AUDIT_LIMIT)))
        .offset(max(0, offset))
    )
    if day is not None:
        start_dt, end_dt = calendar_day_bounds(day)
        stmt = stmt.where(BookingLog.created_at >= start_dt, BookingLog.created_at < end_dt)
    if booking_date is not None:
        iso = booking_date.isoformat()
        stmt = stmt.where(
            or_(
                BookingLog.new_state["date"].astext == iso,
                BookingLog.previous_state["date"].astext == iso,
            )
        )
    if user_id is not None:
        stmt = stmt.where(BookingLog.user_id == user_id)
    if action is not None:
        stmt = stmt.where(BookingLog.action == action)

    rows = (await session.execute(stmt)).all()
    return [
        _audit_entry(entry, ticket, requester, start_time, room_name)
        for entry, ticket, requester, start_time, room_name in rows
    ]


def _audit_entry(entry: BookingLog, ticket, requester, start_time, room_name) -> AuditEntryOut:
    """Resumo pelo retrato da entrada (new_state; num cancelamento ele ainda tem sala e data).
    Retratos sem algum campo caem no estado atual da reserva."""
    snapshot: dict[str, Any] = entry.new_state or entry.previous_state or {}
    booking_day = to_local(start_time).date()
    if isinstance(snapshot.get("date"), str):
        try:
            booking_day = date.fromisoformat(snapshot["date"])
        except ValueError:
            pass
    return AuditEntryOut(
        id=entry.id,
        booking_id=str(entry.booking_id),
        user=user_brief(entry.user),
        action=entry.action,
        previous_state=entry.previous_state,
        new_state=entry.new_state,
        created_at=entry.created_at,
        booking_ticket_number=snapshot["ticket_number"] if "ticket_number" in snapshot else ticket,
        booking_requester_name=snapshot.get("requester_name") or requester,
        booking_room_name=snapshot.get("room_name") or room_name,
        booking_date=booking_day,
    )
