"""Cadastro de usuários (somente administradores)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import AdminUser, SessionDep
from app.schemas import MAX_DB_INT, UserCreate, UserOut, UserUpdate
from app.serializers import user_out
from app.services import users as users_service

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, _: AdminUser) -> list[UserOut]:
    return [user_out(u) for u in await users_service.list_users(session)]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(data: UserCreate, session: SessionDep, _: AdminUser) -> UserOut:
    return user_out(await users_service.create_user(session, data))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: Annotated[int, Path(gt=0, le=MAX_DB_INT)],
    data: UserUpdate,
    session: SessionDep,
    admin: AdminUser,
) -> UserOut:
    """Trocar a senha (ou desativar) encerra as sessões abertas do usuário: os tokens antigos
    passam a receber 401 SESSION_REVOKED / ACCOUNT_DISABLED."""
    return user_out(await users_service.update_user(session, user_id, data, admin))
