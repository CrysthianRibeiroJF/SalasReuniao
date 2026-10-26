"""Situação das contas dos usuários conectados (revalidação periódica dos WebSockets)."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User


async def active_user_ids(user_ids: Collection[int]) -> dict[int, int]:
    """Quais dos `user_ids` ainda existem e estão ativos, com a token_version atual de cada um.

    Uma consulta, sessão curta.
    """
    if not user_ids:
        return {}
    async with SessionLocal() as session:
        result = await session.execute(
            select(User.id, User.token_version).where(
                User.id.in_(list(user_ids)), User.is_active.is_(True)
            )
        )
        return {user_id: int(version or 0) for user_id, version in result.all()}
