"""Usuários e login (seções 5.1 e 5.2)."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from functools import cache

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole
from app.schemas import UserCreate, UserUpdate
from app.security import hash_password, verify_password
from app.services.errors import conflict, not_found, unauthorized, unprocessable


@cache
def _dummy_hash() -> str:
    # Usuário inexistente também paga um PBKDF2 completo: o tempo não revela quem existe.
    return hash_password(secrets.token_urlsafe(16))


async def _hash(password: str) -> str:
    # PBKDF2 leva centenas de ms: roda fora do event loop para não atrasar WebSocket e requisições.
    return await asyncio.to_thread(hash_password, password)


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(
        select(User).where(func.lower(User.username) == username.strip().lower())
    )


async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    """Valida as credenciais e registra last_login_at. Usuário inativo também recebe 401."""
    user = await get_by_username(session, username)
    stored = user.password_hash if user is not None else _dummy_hash()
    valid = await asyncio.to_thread(verify_password, password, stored)
    if user is None or not valid or not user.is_active:
        raise unauthorized("INVALID_CREDENTIALS", "Usuário ou senha inválidos.")
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    return user


async def list_users(session: AsyncSession) -> list[User]:
    stmt = select(User).order_by(User.is_active.desc(), func.lower(User.full_name), User.id)
    return list((await session.scalars(stmt)).all())


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("USER_NOT_FOUND", "Usuário não encontrado.")
    return user


async def create_user(session: AsyncSession, data: UserCreate) -> User:
    username = data.username.strip()
    if await get_by_username(session, username) is not None:
        raise _username_taken(username)
    user = User(
        username=username,
        full_name=_clean_name(data.full_name),
        password_hash=await _hash(data.password),
        role=data.role,
        is_active=True,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_users_username_lower" in str(exc.orig):
            raise _username_taken(username) from exc
        raise
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user_id: int, data: UserUpdate, actor: User) -> User:
    user = await get_user(session, user_id)
    fields = data.model_fields_set
    if user.id == actor.id:
        deactivating = "is_active" in fields and data.is_active is False
        demoting = "role" in fields and data.role is not None and data.role != UserRole.ADMIN
        if deactivating or demoting:
            raise conflict(
                "CANNOT_DEMOTE_SELF",
                "Você não pode desativar a própria conta nem remover seu papel de administrador.",
            )
    # Campos não anuláveis enviados com null são ignorados.
    if "full_name" in fields and data.full_name is not None:
        user.full_name = _clean_name(data.full_name)
    if "role" in fields and data.role is not None:
        user.role = data.role
    if "is_active" in fields and data.is_active is not None:
        if user.is_active and not data.is_active:
            # Reativar a conta depois não ressuscita os tokens emitidos antes da desativação.
            revoke_sessions(user)
        user.is_active = data.is_active
    if "password" in fields and data.password is not None:
        await set_password(user, data.password)
    if session.is_modified(user):
        await session.commit()
        await session.refresh(user)
    return user


def revoke_sessions(user: User) -> None:
    """Invalida todos os tokens já emitidos (claim "tv" diferente): REST 401 SESSION_REVOKED e
    WebSocket 4401 na próxima revalidação."""
    user.token_version = (user.token_version or 0) + 1


async def set_password(user: User, password: str) -> None:
    """Troca a senha e encerra as sessões abertas (quem chama faz o commit)."""
    user.password_hash = await _hash(password)
    revoke_sessions(user)


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if len(name) < 2:
        raise unprocessable("INVALID_NAME", "Informe o nome completo.")
    return name


def _username_taken(username: str):
    return conflict("USERNAME_TAKEN", f'O usuário "{username}" já existe.')
