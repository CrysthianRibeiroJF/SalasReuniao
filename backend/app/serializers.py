"""ORM -> schemas de saída. Usado por API, serviços, tempo real e importação (mantém o JSON idêntico)."""

from __future__ import annotations

from urllib.parse import quote

from app.config import settings
from app.models import Booking, BookingLog, BookingStatus, Room, RoomType, User
from app.schemas import BookingLogOut, BookingOut, RoomBrief, RoomOut, UserBrief, UserOut
from app.timeutil import to_local


def ticket_url(ticket: str | None) -> str | None:
    if not ticket or not settings.ticket_url_template:
        return None
    return settings.ticket_url_template.replace("{ticket}", quote(ticket, safe=""))


def user_brief(user: User | None) -> UserBrief | None:
    if user is None:
        return None
    return UserBrief(id=user.id, full_name=user.full_name)


def user_out(user: User) -> UserOut:
    return UserOut.model_validate(user)


def room_out(room: Room) -> RoomOut:
    return RoomOut(
        id=room.id,
        name=room.name,
        room_type=room.room_type,
        effective_type=room.effective_type,
        capacity=room.capacity,
        resources=list(room.resources or []),
        video_status=room.video_status,
        maintenance_ticket_id=room.maintenance_ticket_id,
        maintenance_ticket_url=ticket_url(room.maintenance_ticket_id),
        maintenance_since=room.maintenance_since,
        display_order=room.display_order,
        is_active=room.is_active,
    )


def room_brief(room: Room) -> RoomBrief:
    return RoomBrief(
        id=room.id,
        name=room.name,
        room_type=room.room_type,
        effective_type=room.effective_type,
        capacity=room.capacity,
    )


def booking_out(b: Booking) -> BookingOut:
    """Requer b.room, b.creator e b.updater carregados (selectinload/joinedload)."""
    start_local, end_local = to_local(b.start_time), to_local(b.end_time)
    active = b.status != BookingStatus.CANCELLED
    room_is_video = b.room.effective_type == RoomType.VIDEO
    return BookingOut(
        id=str(b.id),
        room_id=b.room_id,
        room_name=b.room.name,
        ticket_number=b.ticket_number,
        ticket_url=ticket_url(b.ticket_number),
        title=b.title,
        requester_name=b.requester_name,
        notes=b.notes,
        attendees=b.attendees,
        date=start_local.date(),
        start=start_local.time(),
        end=end_local.time(),
        start_time=start_local,
        end_time=end_local,
        period_type=b.period_type,
        is_full_day=b.is_full_day,
        requires_video=b.requires_video,
        status=b.status,
        version=b.version,
        is_relocatable=active and not b.requires_video and room_is_video,
        video_unavailable=active and b.requires_video and not room_is_video,
        created_by=user_brief(b.creator),
        updated_by=user_brief(b.updater),
        created_at=b.created_at,
        updated_at=b.updated_at,
        cancelled_at=b.cancelled_at,
        cancel_reason=b.cancel_reason,
    )


def booking_snapshot(b: Booking) -> dict:
    """Estado gravado em booking_logs.previous_state/new_state (JSON simples, sem relações)."""
    s, e = to_local(b.start_time), to_local(b.end_time)
    return {
        "room_id": b.room_id,
        "room_name": b.room.name if "room" in b.__dict__ and b.room is not None else None,
        "ticket_number": b.ticket_number,
        "title": b.title,
        "requester_name": b.requester_name,
        "notes": b.notes,
        "attendees": b.attendees,
        "date": s.date().isoformat(),
        "start": s.strftime("%H:%M"),
        "end": e.strftime("%H:%M"),
        "period_type": str(b.period_type),
        "is_full_day": b.is_full_day,
        "requires_video": b.requires_video,
        "status": str(b.status),
        "version": b.version,
    }


def log_out(log: BookingLog) -> BookingLogOut:
    """Requer log.user carregado."""
    return BookingLogOut(
        id=log.id,
        booking_id=str(log.booking_id),
        user=user_brief(log.user),
        action=log.action,
        previous_state=log.previous_state,
        new_state=log.new_state,
        created_at=log.created_at,
    )
