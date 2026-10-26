"""Salas: ordem da grade, cadastro (admin) e vídeo em manutenção (RF01/RF02)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Booking, BookingStatus, Room, RoomType, VideoStatus
from app.realtime.events import EventType, publish
from app.schemas import RoomCreate, RoomOut, RoomUpdate
from app.serializers import booking_out, room_out
from app.services.errors import BusinessError, conflict, not_found, unprocessable

# Reservas listadas no 409 ROOM_HAS_FUTURE_BOOKINGS (o total vem em "count").
MAX_LISTED_FUTURE_BOOKINGS = 50
_ACTIVE_STATUSES = (BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN)


def effective_video_rank():
    """Expressão SQL: 0 para sala com vídeo operacional (effective_type VIDEO), 1 para as demais."""
    return case(
        (and_(Room.room_type == RoomType.VIDEO, Room.video_status == VideoStatus.OPERATIONAL), 0),
        else_=1,
    )


def room_order_by() -> tuple:
    """Ordem da grade: bloco Videoconferência, depois Normais; em cada um display_order e nome."""
    return (effective_video_rank(), Room.display_order, func.lower(Room.name), Room.id)


def room_sort_key(room: Room) -> tuple:
    """Mesma ordem de room_order_by(), para listas já carregadas em memória."""
    return (
        0 if room.effective_type == RoomType.VIDEO else 1,
        room.display_order,
        room.name.lower(),
        room.id,
    )


async def list_rooms(session: AsyncSession, include_inactive: bool = False) -> list[Room]:
    stmt = select(Room).order_by(*room_order_by())
    if not include_inactive:
        stmt = stmt.where(Room.is_active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_room(session: AsyncSession, room_id: int, *, for_update: bool = False) -> Room:
    if for_update:
        # Serializa com as reservas em gravação nessa sala (que seguram a linha com FOR SHARE).
        room = await session.get(Room, room_id, with_for_update=True, populate_existing=True)
    else:
        room = await session.get(Room, room_id)
    if room is None:
        raise not_found("ROOM_NOT_FOUND", "Sala não encontrada.")
    return room


async def create_room(session: AsyncSession, data: RoomCreate) -> RoomOut:
    name = _clean_name(data.name)
    await _ensure_name_available(session, name)
    display_order = data.display_order
    if display_order is None:
        display_order = await _next_display_order(session, data.room_type)
    room = Room(
        name=name,
        room_type=data.room_type,
        capacity=data.capacity,
        resources=_clean_resources(data.resources),
        video_status=VideoStatus.OPERATIONAL,
        display_order=display_order,
        is_active=data.is_active,
    )
    session.add(room)
    await _commit_room(session)
    await session.refresh(room)
    out = room_out(room)
    await publish(EventType.ROOM_CREATED, {"room": out.model_dump(mode="json")})
    return out


async def update_room(session: AsyncSession, room_id: int, data: RoomUpdate) -> RoomOut:
    fields = data.model_fields_set
    deactivating = "is_active" in fields and data.is_active is False
    room = await get_room(session, room_id, for_update=deactivating)
    changed = False

    def assign(attr: str, value) -> None:
        nonlocal changed
        if getattr(room, attr) != value:
            setattr(room, attr, value)
            changed = True

    # Campos não anuláveis enviados com null são ignorados.
    if "name" in fields and data.name is not None:
        name = _clean_name(data.name)
        if name.lower() != room.name.lower():
            await _ensure_name_available(session, name, exclude_id=room.id)
        assign("name", name)
    if "capacity" in fields and data.capacity is not None:
        assign("capacity", data.capacity)
    if "resources" in fields and data.resources is not None:
        assign("resources", _clean_resources(data.resources))
    if "display_order" in fields and data.display_order is not None:
        assign("display_order", data.display_order)
    if "is_active" in fields and data.is_active is not None:
        if room.is_active and not data.is_active:
            await _ensure_no_future_bookings(session, room)
        assign("is_active", data.is_active)
    if "room_type" in fields and data.room_type is not None:
        assign("room_type", data.room_type)
        if data.room_type == RoomType.NORMAL:
            # Sala normal não tem vídeo: qualquer manutenção pendente deixa de fazer sentido.
            assign("video_status", VideoStatus.OPERATIONAL)
            assign("maintenance_ticket_id", None)
            assign("maintenance_since", None)

    if not changed:
        return room_out(room)
    await _commit_room(session)
    await session.refresh(room)
    return await _published_update(room)


async def set_maintenance(session: AsyncSession, room_id: int, ticket_id: str) -> RoomOut:
    room = await get_room(session, room_id)
    if room.room_type != RoomType.VIDEO:
        raise unprocessable(
            "NOT_A_VIDEO_ROOM", "Apenas salas de videoconferência podem ter o vídeo em manutenção."
        )
    ticket_id = ticket_id.strip()
    if room.video_status == VideoStatus.MAINTENANCE and room.maintenance_ticket_id == ticket_id:
        return room_out(room)
    if room.video_status != VideoStatus.MAINTENANCE:
        room.maintenance_since = datetime.now(timezone.utc)
    room.video_status = VideoStatus.MAINTENANCE
    room.maintenance_ticket_id = ticket_id
    await session.commit()
    await session.refresh(room)
    return await _published_update(room)


async def clear_maintenance(session: AsyncSession, room_id: int) -> RoomOut:
    """Volta o vídeo para OPERATIONAL. Idempotente (sala já operacional não muda nada)."""
    room = await get_room(session, room_id)
    if room.video_status == VideoStatus.OPERATIONAL and room.maintenance_ticket_id is None:
        return room_out(room)
    room.video_status = VideoStatus.OPERATIONAL
    room.maintenance_ticket_id = None
    room.maintenance_since = None
    await session.commit()
    await session.refresh(room)
    return await _published_update(room)


# ---------------------------------------------------------------- internos


async def _ensure_no_future_bookings(session: AsyncSession, room: Room) -> None:
    """Desativar some com a sala da grade: antes, as reservas ativas que ainda não terminaram
    precisam ser remanejadas ou canceladas (409 ROOM_HAS_FUTURE_BOOKINGS com a lista)."""
    pending = (
        Booking.room_id == room.id,
        Booking.status.in_(_ACTIVE_STATUSES),
        Booking.end_time > datetime.now(timezone.utc),
    )
    count = await session.scalar(select(func.count()).select_from(Booking).where(*pending))
    if not count:
        return
    bookings = (
        await session.scalars(
            select(Booking)
            .where(*pending)
            .options(
                selectinload(Booking.room),
                selectinload(Booking.creator),
                selectinload(Booking.updater),
            )
            .order_by(Booking.start_time, Booking.id)
            .limit(MAX_LISTED_FUTURE_BOOKINGS)
        )
    ).all()
    raise _has_future_bookings(count, [booking_out(b).model_dump(mode="json") for b in bookings])


def _has_future_bookings(count: int, bookings: list[dict]) -> BusinessError:
    if count == 1:
        message = (
            "A sala tem 1 reserva ativa que ainda não aconteceu. Remaneje ou cancele essa "
            "reserva antes de desativar a sala."
        )
    else:
        message = (
            f"A sala tem {count} reservas ativas que ainda não aconteceram. Remaneje ou cancele "
            "essas reservas antes de desativar a sala."
        )
    return conflict("ROOM_HAS_FUTURE_BOOKINGS", message, count=count, bookings=bookings)


async def _published_update(room: Room) -> RoomOut:
    out = room_out(room)
    await publish(EventType.ROOM_UPDATED, {"room": out.model_dump(mode="json")})
    return out


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise unprocessable("INVALID_ROOM_NAME", "Informe o nome da sala.")
    return name


def _clean_resources(resources: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in resources:
        text = " ".join(str(item).split())
        if text and text.lower() not in seen:
            seen.add(text.lower())
            result.append(text)
    return result


async def _ensure_name_available(
    session: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(Room.id).where(func.lower(Room.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Room.id != exclude_id)
    if (await session.scalar(stmt.limit(1))) is not None:
        raise _name_taken(name)


def _name_taken(name: str):
    return conflict("ROOM_NAME_TAKEN", f'Já existe uma sala chamada "{name}".')


async def _next_display_order(session: AsyncSession, room_type: RoomType) -> int:
    current = await session.scalar(
        select(func.max(Room.display_order)).where(Room.room_type == room_type)
    )
    return (current or 0) + 1


async def _commit_room(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_rooms_name_lower" in str(exc.orig):
            raise conflict("ROOM_NAME_TAKEN", "Já existe uma sala com esse nome.") from exc
        raise
