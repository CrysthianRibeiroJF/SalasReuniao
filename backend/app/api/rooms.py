"""Salas: listagem (todos), cadastro (admin) e vídeo em manutenção (todos)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.schemas import MAX_DB_INT, MaintenanceIn, RoomCreate, RoomOut, RoomUpdate
from app.serializers import room_out
from app.services import rooms as rooms_service

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

RoomId = Annotated[int, Path(gt=0, le=MAX_DB_INT)]


@router.get("", response_model=list[RoomOut])
async def list_rooms(
    session: SessionDep, _: CurrentUser, include_inactive: bool = False
) -> list[RoomOut]:
    """Salas ativas; `include_inactive=true` (qualquer usuário logado, só leitura) inclui as
    desativadas, para a grade mostrar reservas antigas que ainda estão nelas."""
    return [room_out(r) for r in await rooms_service.list_rooms(session, include_inactive)]


@router.get("/{room_id}", response_model=RoomOut)
async def get_room(room_id: RoomId, session: SessionDep, _: CurrentUser) -> RoomOut:
    return room_out(await rooms_service.get_room(session, room_id))


@router.post("", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
async def create_room(data: RoomCreate, session: SessionDep, _: AdminUser) -> RoomOut:
    return await rooms_service.create_room(session, data)


@router.patch(
    "/{room_id}",
    response_model=RoomOut,
    responses={
        409: {
            "description": "ROOM_NAME_TAKEN, ou ROOM_HAS_FUTURE_BOOKINGS ao desativar uma sala "
            "com reservas ativas que ainda não terminaram ({count, bookings: BookingOut[]})"
        }
    },
)
async def update_room(
    room_id: RoomId, data: RoomUpdate, session: SessionDep, _: AdminUser
) -> RoomOut:
    return await rooms_service.update_room(session, room_id, data)


@router.put("/{room_id}/maintenance", response_model=RoomOut)
async def set_maintenance(
    room_id: RoomId, data: MaintenanceIn, session: SessionDep, _: CurrentUser
) -> RoomOut:
    return await rooms_service.set_maintenance(session, room_id, data.ticket_id)


@router.delete("/{room_id}/maintenance", response_model=RoomOut)
async def clear_maintenance(room_id: RoomId, session: SessionDep, _: CurrentUser) -> RoomOut:
    return await rooms_service.clear_maintenance(session, room_id)
