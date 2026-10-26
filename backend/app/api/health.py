"""Verificação de saúde (público; usado por load balancer / docker healthcheck).

HTTP 503 somente com o banco fora do ar (a aplicação não funciona sem ele). Redis fora do ar deixa
o status "degraded" com HTTP 200: o REST continua funcionando (o hard lock está no banco) e o tempo
real se recupera sozinho quando o Redis volta.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.realtime.setup import realtime_backend_name

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

REDIS_TIMEOUT_SECONDS = 2.0


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    realtime: Literal["memory", "redis"]
    redis: Literal["ok", "down", "disabled"]


async def check_database() -> bool:
    try:
        async with asyncio.timeout(settings.health_db_timeout_seconds):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("Health check: banco de dados indisponível", exc_info=True)
        return False


async def check_redis(request: Request) -> str:
    """'disabled' sem AGENDA_REDIS_URL; senão PING no cliente do tempo real desta instância."""
    if not settings.redis_url:
        return "disabled"
    runtime = getattr(request.app.state, "realtime", None)
    if runtime is None:
        return "down"
    status = await runtime.redis_status(REDIS_TIMEOUT_SECONDS)
    if status == "down":
        log.warning("Health check: Redis indisponível")
    return status


@router.get(
    "/health",
    response_model=HealthOut,
    responses={503: {"model": HealthOut, "description": "Banco de dados indisponível"}},
)
async def health(request: Request) -> JSONResponse:
    database, redis = await asyncio.gather(check_database(), check_redis(request))
    body = HealthOut(
        status="ok" if database and redis != "down" else "degraded",
        database=database,
        realtime=realtime_backend_name(),
        redis=redis,
    )
    return JSONResponse(body.model_dump(), status_code=200 if database else 503)
