"""Exportação da grade para .xlsx no formato espelho da planilha (RF08, contrato 5.6).

Layout de cada aba (um dia):
    linha 1  título ("Agenda de Salas — Segunda-feira, 26/10/2026")
    linha 2  grupos mesclados: "Videoconferência" | "Salas Normais"
    linha 3  salas (nome + capacidade; vídeo em manutenção vai para o bloco normal)
    linha 4+ um slot de `slot_minutes` por linha ("HH:MM" na coluna A); cada reserva ativa é um
             intervalo mesclado com "Chamado <n>", solicitante, assunto e selos.

O texto segue as constantes de app/excel/common.py para que o arquivo possa ser importado de
volta (a importação reconhece a marca EXPORT_MARKER nas propriedades do arquivo).
"""

from __future__ import annotations

import asyncio
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.excel.common import (
    BADGE_AFTERNOON,
    BADGE_CHECKED_IN,
    BADGE_FULL_DAY,
    BADGE_MORNING,
    BADGE_NO_SHOW,
    BADGE_RELOCATABLE,
    BADGE_VIDEO,
    BADGE_VIDEO_UNAVAILABLE,
    EXPORT_CREATOR,
    EXPORT_MARKER,
    GROUP_NORMAL,
    GROUP_VIDEO,
    INACTIVE_NOTE,
    MAINTENANCE_NOTE,
    PART_SEPARATOR,
    TIME_HEADER,
    TITLE_PREFIX,
    attendees_badge,
    is_ticket_line,
    parse_time_line,
    sheet_name,
    sheet_title,
    ticket_line,
    time_line,
)
from app.excel.parsing import is_badge_line
from app.models import BookingStatus, PeriodType, Room, RoomType, VideoStatus
from app.schemas import BookingOut
from app.services import bookings as bookings_service
from app.services import rooms as rooms_service
from app.timeutil import now_local, week_monday

__all__ = ["export_day", "export_week"]

TITLE_ROW, GROUP_ROW, HEADER_ROW, FIRST_SLOT_ROW = 1, 2, 3, 4
TIME_COL = 1

# Cores (RGB). Blocos: vídeo azul, normal verde-acinzentado; estados com cores próprias.
_VIDEO_GROUP = "1F4E79"
_NORMAL_GROUP = "3A5A40"
_VIDEO_HEADER = "DDEBF7"
_NORMAL_HEADER = "E2EFDA"
_MAINTENANCE_HEADER = "FFF2CC"
_VIDEO_EMPTY = "F5F9FD"
_TIME_FILL = "F2F2F2"
_VIDEO_BOOKING = "BDD7EE"
_NORMAL_BOOKING = "C6E0B4"
_RELOCATABLE = "FCE4D6"
_CHECKED_IN = "A9D08E"
_NO_SHOW = "D9D9D9"
_VIDEO_ALERT = "F8CBAD"

_THIN = Side(style="thin", color="BFBFBF")
_BLOCK_EDGE = Side(style="medium", color="7F7F7F")
_SLOT_HEIGHT = 30.0
_LINE_HEIGHT = 12.0
_MAX_ROW_HEIGHT = 120.0
_ROOM_WIDTH = 24
_CHARS_PER_LINE = 26  # aproximação para estimar quebras de linha numa coluna de largura 24


@dataclass(frozen=True)
class _RoomColumn:
    id: int
    name: str
    capacity: int
    is_video: bool  # bloco Videoconferência (vídeo operacional)
    in_maintenance: bool
    maintenance_ticket: str | None
    is_active: bool

    @property
    def header(self) -> str:
        lines = [self.name]
        if self.capacity:
            lines.append(f"{self.capacity} {'lugar' if self.capacity == 1 else 'lugares'}")
        if self.in_maintenance:
            note = MAINTENANCE_NOTE
            if self.maintenance_ticket:
                note = f"{MAINTENANCE_NOTE[:-1]} — chamado {self.maintenance_ticket})"
            lines.append(note)
        if not self.is_active:
            lines.append(INACTIVE_NOTE)
        return "\n".join(lines)


@dataclass(frozen=True)
class _DayData:
    day: date
    rooms: list[_RoomColumn]  # ordem da grade: bloco vídeo, depois normais
    bookings: list[BookingOut]


async def export_day(session: AsyncSession, d: date) -> bytes:
    """Planilha de um dia (uma aba)."""
    rooms = await rooms_service.list_rooms(session, include_inactive=True)
    bookings = await bookings_service.list_bookings(session, d, d)
    data = _day_data(d, rooms, bookings)
    return await asyncio.to_thread(_render, [data], f"{TITLE_PREFIX} — {d:%d/%m/%Y}")


async def export_week(session: AsyncSession, monday: date) -> bytes:
    """Uma aba por dia útil configurado (work_days) da semana de `monday` (qualquer dia da
    semana é normalizado para a segunda-feira). Sábado/domingo fora de work_days só entram se
    tiverem reservas, para o arquivo nunca omitir uma reserva da semana."""
    monday = week_monday(monday)
    rooms = await rooms_service.list_rooms(session, include_inactive=True)
    bookings = await bookings_service.list_bookings(session, monday, monday + timedelta(days=6))
    by_day: dict[date, list[BookingOut]] = defaultdict(list)
    for booking in bookings:
        by_day[booking.date].append(booking)
    days = sorted({monday + timedelta(days=wd) for wd in settings.work_days} | set(by_day))
    data = [_day_data(d, rooms, by_day.get(d, [])) for d in days]
    title = f"{TITLE_PREFIX} — semana de {monday:%d/%m/%Y}"
    return await asyncio.to_thread(_render, data, title)


def _day_data(d: date, rooms: list[Room], bookings: list[BookingOut]) -> _DayData:
    """Salas ativas na ordem da grade (+ desativadas que ainda têm reservas no dia)."""
    booked = {b.room_id for b in bookings}
    columns = [
        _RoomColumn(
            id=room.id,
            name=room.name,
            capacity=room.capacity,
            is_video=room.effective_type == RoomType.VIDEO,
            in_maintenance=room.room_type == RoomType.VIDEO
            and room.video_status == VideoStatus.MAINTENANCE,
            maintenance_ticket=room.maintenance_ticket_id,
            is_active=room.is_active,
        )
        for room in rooms
        if room.is_active or room.id in booked
    ]
    columns.sort(key=lambda c: not c.is_video)  # estável: mantém a ordem dentro de cada bloco
    return _DayData(d, columns, list(bookings))


# ---------------------------------------------------------------- renderização (síncrona)


def _render(days: list[_DayData], title: str) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for data in days:
        _DaySheet(wb.create_sheet(sheet_name(data.day)), data).render()
    wb.properties.creator = EXPORT_CREATOR
    wb.properties.title = title
    wb.properties.keywords = EXPORT_MARKER
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class _DaySheet:
    def __init__(self, ws: Worksheet, data: _DayData) -> None:
        self.ws = ws
        self.data = data
        self.step = settings.slot_minutes
        self.video = [r for r in data.rooms if r.is_video]
        self.normal = [r for r in data.rooms if not r.is_video]
        self.columns = self.video + self.normal
        self.last_col = TIME_COL + len(self.columns)
        self.first_normal_col = TIME_COL + len(self.video) + 1 if self.normal else None

        day_start = _minutes(settings.day_start.hour, settings.day_start.minute)
        day_end = _minutes(settings.day_end.hour, settings.day_end.minute)
        starts = [_minutes(b.start.hour, b.start.minute) for b in data.bookings]
        ends = [_minutes(b.end.hour, b.end.minute) for b in data.bookings]
        # Reservas importadas fora do horário de funcionamento também aparecem (linhas extras).
        first = min([day_start, *starts]) // self.step * self.step
        last = math.ceil(max([day_end, *ends]) / self.step) * self.step
        self.slots = list(range(first, last, self.step))
        self.row_heights: dict[int, float] = {}

    def row_of(self, minutes: int) -> int:
        return FIRST_SLOT_ROW + (minutes - self.slots[0]) // self.step

    def render(self) -> None:
        self._title()
        self._headers()
        self._grid()
        self._bookings()
        self._layout()

    # ------------------------------------------------------------ cabeçalhos

    def _title(self) -> None:
        text = sheet_title(self.data.day)
        if not self.columns:
            text += " (nenhuma sala ativa cadastrada)"
        cell = self.ws.cell(TITLE_ROW, TIME_COL, text)
        cell.font = Font(bold=True, size=14)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        if self.last_col > TIME_COL:
            self.ws.merge_cells(
                start_row=TITLE_ROW,
                start_column=TIME_COL,
                end_row=TITLE_ROW,
                end_column=self.last_col,
            )
        self.ws.row_dimensions[TITLE_ROW].height = 26

    def _headers(self) -> None:
        ws = self.ws
        cell = ws.cell(GROUP_ROW, TIME_COL, TIME_HEADER)
        self._style_header(cell, _TIME_FILL, bold=True)
        cell.border = self._border(TIME_COL)
        ws.cell(HEADER_ROW, TIME_COL).border = self._border(TIME_COL)
        ws.merge_cells(
            start_row=GROUP_ROW, start_column=TIME_COL, end_row=HEADER_ROW, end_column=TIME_COL
        )

        first = TIME_COL + 1
        for label, rooms, fill in (
            (GROUP_VIDEO, self.video, _VIDEO_GROUP),
            (GROUP_NORMAL, self.normal, _NORMAL_GROUP),
        ):
            if not rooms:
                continue
            last = first + len(rooms) - 1
            cell = ws.cell(GROUP_ROW, first, label)
            cell.font = Font(bold=True, color="FFFFFF", size=11)
            cell.fill = _fill(fill)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = self._border(first)
            if last > first:
                ws.merge_cells(
                    start_row=GROUP_ROW, start_column=first, end_row=GROUP_ROW, end_column=last
                )
            first = last + 1
        ws.row_dimensions[GROUP_ROW].height = 20

        header_lines = 1
        for i, room in enumerate(self.columns):
            col = TIME_COL + 1 + i
            if room.in_maintenance:
                fill = _MAINTENANCE_HEADER
            else:
                fill = _VIDEO_HEADER if room.is_video else _NORMAL_HEADER
            cell = _text_cell(ws, HEADER_ROW, col, room.header)
            self._style_header(cell, fill, bold=True)
            cell.border = self._border(col)
            header_lines = max(header_lines, _estimate_lines(room.header))
        ws.row_dimensions[HEADER_ROW].height = max(30.0, header_lines * _LINE_HEIGHT + 6)

    def _style_header(self, cell, fill: str, *, bold: bool) -> None:
        cell.font = Font(bold=bold, size=10)
        cell.fill = _fill(fill)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # ------------------------------------------------------------ grade vazia

    def _grid(self) -> None:
        ws = self.ws
        for minutes in self.slots:
            row = self.row_of(minutes)
            cell = ws.cell(row, TIME_COL, f"{minutes // 60:02d}:{minutes % 60:02d}")
            cell.font = Font(bold=True, size=9, color="404040")
            cell.fill = _fill(_TIME_FILL)
            cell.alignment = Alignment(horizontal="center", vertical="top")
            cell.border = self._border(TIME_COL)
            for i, room in enumerate(self.columns):
                col = TIME_COL + 1 + i
                empty = ws.cell(row, col)
                empty.border = self._border(col)
                if room.is_video:
                    empty.fill = _fill(_VIDEO_EMPTY)
            self.row_heights[row] = _SLOT_HEIGHT

    # ------------------------------------------------------------ reservas

    def _bookings(self) -> None:
        by_room: dict[int, list[BookingOut]] = defaultdict(list)
        for booking in self.data.bookings:
            by_room[booking.room_id].append(booking)
        for i, room in enumerate(self.columns):
            col = TIME_COL + 1 + i
            for group in self._groups(sorted(by_room.get(room.id, []), key=lambda b: b.start)):
                self._place(col, room, group)

    def _rows(self, booking: BookingOut) -> tuple[int, int]:
        start = _minutes(booking.start.hour, booking.start.minute)
        end = _minutes(booking.end.hour, booking.end.minute)
        first = self.row_of(start // self.step * self.step)
        last = self.row_of(math.ceil(end / self.step) * self.step - self.step)
        return first, last

    def _groups(self, bookings: list[BookingOut]) -> list[list[BookingOut]]:
        """Reservas que dividem uma linha (horários fora dos slots) ficam na mesma célula."""
        groups: list[list[BookingOut]] = []
        last_row = 0
        for booking in bookings:
            first, last = self._rows(booking)
            if groups and first <= last_row:
                groups[-1].append(booking)
            else:
                groups.append([booking])
            last_row = max(last_row, last)
        return groups

    def _place(self, col: int, room: _RoomColumn, group: list[BookingOut]) -> None:
        first = self._rows(group[0])[0]
        last = max(self._rows(b)[1] for b in group)
        shared = len(group) > 1
        text = f"\n{PART_SEPARATOR}\n".join(
            _booking_text(b, room, with_time=shared or not self._aligned(b)) for b in group
        )
        cell = _text_cell(self.ws, first, col, text)
        cell.font = Font(size=9, italic=group[0].status == BookingStatus.NO_SHOW)
        cell.fill = _fill(_booking_fill(group[0], room))
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        cell.border = self._border(col)
        if last > first:
            self.ws.merge_cells(start_row=first, start_column=col, end_row=last, end_column=col)

        rows = last - first + 1
        needed = (_estimate_lines(text) * _LINE_HEIGHT + 6) / rows
        for row in range(first, last + 1):
            self.row_heights[row] = min(_MAX_ROW_HEIGHT, max(self.row_heights.get(row, 0), needed))

    def _aligned(self, booking: BookingOut) -> bool:
        return booking.start.minute % self.step == 0 and booking.end.minute % self.step == 0

    # ------------------------------------------------------------ layout e impressão

    def _layout(self) -> None:
        ws = self.ws
        ws.column_dimensions[get_column_letter(TIME_COL)].width = 9
        for i in range(len(self.columns)):
            ws.column_dimensions[get_column_letter(TIME_COL + 1 + i)].width = _ROOM_WIDTH
        for row, height in self.row_heights.items():
            ws.row_dimensions[row].height = round(height, 1)
        ws.freeze_panes = ws.cell(FIRST_SLOT_ROW, TIME_COL + 1)
        ws.sheet_view.showGridLines = False

        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.print_title_rows = f"{TITLE_ROW}:{HEADER_ROW}"
        ws.print_options.horizontalCentered = True
        ws.page_margins = PageMargins(left=0.3, right=0.3, top=0.5, bottom=0.5)
        ws.oddFooter.left.text = f"Exportado em {now_local():%d/%m/%Y %H:%M}"
        ws.oddFooter.right.text = "Página &P de &N"

    def _border(self, col: int) -> Border:
        left = _BLOCK_EDGE if col == self.first_normal_col or col == TIME_COL + 1 else _THIN
        return Border(left=left, right=_THIN, top=_THIN, bottom=_THIN)


# ---------------------------------------------------------------- texto e estilos


def _booking_text(b: BookingOut, room: _RoomColumn, *, with_time: bool) -> str:
    """Linhas fixas que a importação lê por posição (parsing.parse_export_text):
    [Chamado <nº>], solicitante, [assunto], [HH:MM–HH:MM], [selos].

    As linhas opcionais de chamado e de horário também entram quando o texto do usuário
    poderia ser confundido com elas, para a ida e volta ser exata."""
    requester = _safe(_one_line(b.requester_name))
    title = _safe(_one_line(b.title)) if b.title else None
    lines: list[str] = []
    if b.ticket_number or is_ticket_line(requester):
        lines.append(ticket_line(_safe(_one_line(b.ticket_number or "")) or None))
    lines.append(requester)
    if title:
        lines.append(title)
    badges = _badges(b)
    last = lines[-1]
    if with_time or parse_time_line(last) is not None or (not badges and is_badge_line(last)):
        lines.append(time_line(b.start, b.end))
    if badges:
        lines.append(" ".join(badges))
    return "\n".join(lines)


def _badges(b: BookingOut) -> list[str]:
    badges = []
    if b.period_type == PeriodType.FULL_DAY:
        badges.append(BADGE_FULL_DAY)
    elif b.period_type == PeriodType.MORNING:
        badges.append(BADGE_MORNING)
    elif b.period_type == PeriodType.AFTERNOON:
        badges.append(BADGE_AFTERNOON)
    if b.video_unavailable:
        badges.append(BADGE_VIDEO_UNAVAILABLE)
    elif b.requires_video:
        badges.append(BADGE_VIDEO)
    if b.is_relocatable:
        badges.append(BADGE_RELOCATABLE)
    if b.attendees:
        badges.append(attendees_badge(b.attendees))
    if b.status == BookingStatus.CHECKED_IN:
        badges.append(BADGE_CHECKED_IN)
    elif b.status == BookingStatus.NO_SHOW:
        badges.append(BADGE_NO_SHOW)
    return badges


def _booking_fill(b: BookingOut, room: _RoomColumn) -> str:
    if b.status == BookingStatus.CHECKED_IN:
        return _CHECKED_IN
    if b.status == BookingStatus.NO_SHOW:
        return _NO_SHOW
    if b.video_unavailable:
        return _VIDEO_ALERT
    if b.is_relocatable:
        return _RELOCATABLE
    return _VIDEO_BOOKING if room.is_video else _NORMAL_BOOKING


def _fill(rgb: str) -> PatternFill:
    return PatternFill(fill_type="solid", start_color=rgb, end_color=rgb)


def _minutes(hour: int, minute: int) -> int:
    return hour * 60 + minute


def _one_line(text: str) -> str:
    """Quebras de linha viram espaço (cada campo ocupa uma linha da célula); o resto do texto
    fica como foi digitado."""
    return " ".join(part.strip() for part in text.splitlines() if part.strip())


# Tudo que não é caractere válido em XML 1.0: controles (exceto tab e quebras de linha),
# metades de pares substitutos e U+FFFE/U+FFFF. Um único caractere desses tornava o arquivo
# inteiro ilegível (no Excel e na importação).
_XML_ILLEGAL_RE = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
# Começos que o Excel (ou quem converter para CSV) interpretaria como fórmula.
_FORMULA_START = ("=", "+", "-", "@")


def _safe(text: str) -> str:
    """Remove caracteres que o formato .xlsx não aceita (ex.: colados de outro sistema)."""
    return _XML_ILLEGAL_RE.sub("", text)


def _text_cell(ws: Worksheet, row: int, col: int, text: str) -> Cell:
    """Célula com texto digitado por usuários (reservas, nomes de salas): sempre gravada como
    TEXTO. O openpyxl grava qualquer valor que comece com "=" como fórmula: um solicitante
    "=1+1" (ou =HYPERLINK(...)) virava fórmula executada ao abrir o arquivo, ou deixava o
    arquivo inteiro ilegível. `quotePrefix` faz o Excel manter o texto mesmo se a célula for
    editada."""
    cell = ws.cell(row, col)
    cell.value = _safe(text)
    cell.data_type = "s"
    if cell.value.startswith(_FORMULA_START):
        cell.quotePrefix = True
    return cell


def _estimate_lines(text: str) -> int:
    return sum(max(1, math.ceil(len(line) / _CHARS_PER_LINE)) for line in text.split("\n"))
