"""Configuração do calendário exposta ao frontend."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.config import settings
from app.realtime.setup import realtime_backend_name
from app.schemas import ConfigOut, PeriodWindow
from app.timeutil import today_local

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", response_model=ConfigOut)
async def get_config(_: CurrentUser) -> ConfigOut:
    return ConfigOut(
        timezone=settings.timezone,
        today=today_local(),
        day_start=settings.day_start,
        day_end=settings.day_end,
        slot_minutes=settings.slot_minutes,
        morning=PeriodWindow(start=settings.day_start, end=settings.morning_end),
        afternoon=PeriodWindow(start=settings.afternoon_start, end=settings.day_end),
        work_days=list(settings.work_days),
        lock_ttl_seconds=settings.lock_ttl_seconds,
        ticket_url_template=settings.ticket_url_template,
        realtime_backend=realtime_backend_name(),
    )
