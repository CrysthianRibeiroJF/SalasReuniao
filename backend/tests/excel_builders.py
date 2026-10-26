"""Apoio aos testes do módulo Excel: planilhas legadas sob medida e leitura das exportações.

O construtor de abas (`LegacySheet`) é o mesmo usado pelo gerador da planilha de exemplo
(backend/scripts/make_sample_legacy.py). As fixtures ficam em tests/excel_fixtures.py.
"""

from __future__ import annotations

from datetime import date, time
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Booking, BookingStatus, RoomType
from app.schemas import ImportReport, ImportSheetReport
from app.timeutil import to_local
from scripts.make_sample_legacy import SAMPLE_ROOMS, TIME_FORMATS, LegacySheet

__all__ = [
    "LegacySheet",
    "SAMPLE_ROOMS",
    "TIME_FORMATS",
    "MONDAY",
    "TODAY",
    "HEADERS",
    "ROOMS",
    "active_bookings",
    "booking_tuples",
    "legacy",
    "new_workbook",
    "sheet_report",
    "t",
    "to_bytes",
    "reload",
]

TODAY = date(2026, 10, 23)  # sexta-feira: "hoje" fixo passado ao importador
MONDAY = date(2026, 10, 26)

# Salas padrão dos testes de importação: (nome, tipo, capacidade)
ROOMS: tuple[tuple[str, RoomType, int], ...] = (
    ("Sala 01 - Executiva", RoomType.VIDEO, 12),
    ("Sala 02 - Diretoria", RoomType.VIDEO, 10),
    ("Sala 05 - Reunião", RoomType.NORMAL, 6),
    ("Sala 07 - Atendimento", RoomType.NORMAL, 4),
)
HEADERS = tuple(name for name, _, _ in ROOMS)


def new_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def legacy(
    wb: Workbook, name: str, rooms: tuple[str, ...] | list[str] = HEADERS, **kw: Any
) -> LegacySheet:
    """Nova aba `name` no formato legado (kw: ver LegacySheet)."""
    return LegacySheet(wb.create_sheet(name), rooms, **kw)


def to_bytes(wb: Workbook) -> bytes:
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def reload(content: bytes, *, data_only: bool = True) -> Workbook:
    return load_workbook(BytesIO(content), data_only=data_only)


def sheet_report(report: ImportReport, name: str) -> ImportSheetReport:
    for sheet in report.sheets:
        if sheet.sheet_name == name:
            return sheet
    raise AssertionError(
        f"aba {name!r} não está no relatório: {[s.sheet_name for s in report.sheets]}"
    )


async def active_bookings(session: AsyncSession) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.status != BookingStatus.CANCELLED)
        .options(selectinload(Booking.room))
        .order_by(Booking.start_time, Booking.room_id)
        .execution_options(populate_existing=True)
    )
    return list((await session.scalars(stmt)).all())


def booking_tuples(bookings: list[Booking]) -> set[tuple]:
    """Campos que a importação reproduz (para comparar exportação -> importação)."""
    result = set()
    for b in bookings:
        s, e = to_local(b.start_time), to_local(b.end_time)
        result.add(
            (
                b.room.name,
                s.date(),
                s.time(),
                e.time(),
                str(b.period_type),
                b.is_full_day,
                b.ticket_number,
                b.requester_name,
                b.title,
                b.requires_video,
                b.attendees,
            )
        )
    return result


def t(value: str) -> time:
    """ "HH:MM" -> time."""
    return time.fromisoformat(value)
