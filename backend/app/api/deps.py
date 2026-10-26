"""Dependências comuns dos routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security import get_current_user, require_admin

# FastAPI reaproveita a mesma sessão dentro da requisição: o usuário autenticado e o serviço
# compartilham a transação.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
