"""Conversões de fuso e regras de calendário da recepção.

Convenção: o banco guarda TIMESTAMPTZ (UTC); a API conversa em data local + "HH:MM"
no fuso `settings.timezone`. Uma reserva nunca atravessa a meia-noite.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import PeriodType

TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


def today_local() -> date:
    return now_local().date()


def local_dt(d: date, t: time) -> datetime:
    """Data + hora locais -> datetime aware no fuso da recepção."""
    return datetime.combine(d, t.replace(second=0, microsecond=0), tzinfo=TZ)


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(TZ)


def day_bounds(d: date) -> tuple[datetime, datetime]:
    """Horário de funcionamento do dia (abertura, fechamento)."""
    return local_dt(d, settings.day_start), local_dt(d, settings.day_end)


def calendar_day_bounds(d: date) -> tuple[datetime, datetime]:
    """Meia-noite a meia-noite (para listar tudo que começa no dia)."""
    start = datetime.combine(d, time(0, 0), tzinfo=TZ)
    return start, datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=TZ)


def period_times(period: PeriodType) -> tuple[time, time] | None:
    """Horários fixos de cada preset; None para CUSTOM."""
    match period:
        case PeriodType.FULL_DAY:
            return settings.day_start, settings.day_end
        case PeriodType.MORNING:
            return settings.day_start, settings.morning_end
        case PeriodType.AFTERNOON:
            return settings.afternoon_start, settings.day_end
        case _:
            return None


def resolve_period(
    period: PeriodType, d: date, start: time | None, end: time | None
) -> tuple[datetime, datetime, bool]:
    """Retorna (start_dt, end_dt, is_full_day). Levanta ValueError com mensagem pt-BR."""
    preset = period_times(period)
    if preset is not None:
        start, end = preset
    elif start is None or end is None:
        raise ValueError("Informe início e fim para Horário Específico")
    start_dt, end_dt = local_dt(d, start), local_dt(d, end)
    if end_dt <= start_dt:
        raise ValueError("O horário de término deve ser depois do início")
    return start_dt, end_dt, period == PeriodType.FULL_DAY


def within_operating_hours(start_dt: datetime, end_dt: datetime) -> bool:
    s, e = to_local(start_dt), to_local(end_dt)
    if s.date() != e.date():
        return False
    open_dt, close_dt = day_bounds(s.date())
    return open_dt <= s and e <= close_dt


def is_minute_aligned(t: time, step: int = 5) -> bool:
    return t.second == 0 and t.microsecond == 0 and t.minute % step == 0


def slot_starts(d: date, start: time, end: time) -> list[time]:
    """Inícios dos slots (granularidade settings.slot_minutes) cobertos por [start, end).

    Intervalos desalinhados são expandidos para os slots que tocam (floor/ceil).
    """
    step = settings.slot_minutes
    s = (start.hour * 60 + start.minute) // step * step
    e_min = end.hour * 60 + end.minute
    if end == time(0, 0):
        e_min = 24 * 60
    result: list[time] = []
    m = s
    while m < e_min:
        result.append(time(m // 60, m % 60))
        m += step
    return result


def fmt_hhmm(t: time | datetime) -> str:
    if isinstance(t, datetime):
        t = to_local(t).time()
    return t.strftime("%H:%M")


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())
