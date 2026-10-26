"""Auditoria (todos os usuários logados podem consultar)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep
from app.models import LogAction
from app.schemas import MAX_DB_INT, AuditEntryOut, LocalDate
from app.services import audit as audit_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit(
    session: SessionDep,
    _: CurrentUser,
    day: Annotated[LocalDate | None, Query(alias="date", description="Dia (local) da ação")] = None,
    booking_date: Annotated[
        LocalDate | None,
        Query(description="Dia da reserva (antes ou depois da alteração registrada)"),
    ] = None,
    user_id: Annotated[int | None, Query(gt=0, le=MAX_DB_INT)] = None,
    action: LogAction | None = None,
    limit: Annotated[int, Query(ge=1, le=audit_service.MAX_AUDIT_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_DB_INT)] = 0,
) -> list[AuditEntryOut]:
    return await audit_service.list_audit(
        session,
        day=day,
        booking_date=booking_date,
        user_id=user_id,
        action=action,
        limit=limit,
        offset=offset,
    )
