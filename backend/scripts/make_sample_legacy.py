"""Gera docs/exemplos/planilha-legada-exemplo.xlsx, uma planilha no estilo da legada (uma aba
por dia), para treinar a importação (simulação) antes da virada.

Uso, de dentro de backend/:
    .venv/Scripts/python scripts/make_sample_legacy.py [--monday 2026-10-26] [-o arquivo.xlsx]

Sem --monday usa a próxima segunda-feira. As salas são as do `seed-demo`, escritas com
variações de caixa, acento e espaços de propósito, mais uma sala inexistente ("Sala 11 - Copa").
Cada aba usa um formato de horário diferente e o arquivo exercita as principais heurísticas
descritas em docs/IMPORTACAO.md (mesclagens, cópias de texto, dia inteiro, manhã/tarde, aba
passada, aba sem data, aba duplicada com conflito).

`LegacySheet` também é usado pelos testes para montar planilhas legadas sob medida.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BACKEND_DIR.parent / "docs" / "exemplos" / "planilha-legada-exemplo.xlsx"

TIME_FORMATS = ("hhmm", "h", "time", "float", "datetime", "range", "range_as")


def _as_time(value: str | time) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class LegacySheet:
    """Uma aba no formato da planilha legada: horários numa coluna, salas no cabeçalho.

    `time_format`: "hhmm" ("08:00"), "h" ("8h", "8h30"), "time" (datetime.time), "float"
    (fração do dia), "datetime" (1899-12-30 08:00), "range" ("08:00 - 08:30") ou "range_as"
    ("08:00 às 08:30").
    """

    def __init__(
        self,
        ws: Worksheet,
        rooms: Sequence[str],
        *,
        title: str | None = None,
        header_row: int = 2,
        time_col: int = 1,
        first_room_col: int = 2,
        start: str | time = "08:00",
        end: str | time = "19:00",
        step: int = 30,
        time_format: str = "hhmm",
        time_header: str | None = "Horário",
    ) -> None:
        if time_format not in TIME_FORMATS:
            raise ValueError(f"time_format inválido: {time_format}")
        self.ws = ws
        self.rooms = list(rooms)
        self.header_row = header_row
        self.time_col = time_col
        self.first_room_col = first_room_col
        self.step = step
        self.start = _minutes(_as_time(start))
        self.end = _minutes(_as_time(end))
        if title:
            ws.cell(1, 1, title).font = Font(bold=True, size=13)
        if time_header:
            ws.cell(header_row, time_col, time_header).font = Font(bold=True)
        for i, name in enumerate(self.rooms):
            cell = ws.cell(header_row, first_room_col + i, name)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True, horizontal="center")
            ws.column_dimensions[get_column_letter(first_room_col + i)].width = 22
        for minutes in range(self.start, self.end, step):
            self._write_time(self.row(minutes), minutes, time_format)

    def _write_time(self, row: int, minutes: int, time_format: str) -> None:
        h, m = divmod(minutes, 60)
        e_h, e_m = divmod(minutes + self.step, 60)
        cell = self.ws.cell(row, self.time_col)
        match time_format:
            case "hhmm":
                cell.value = f"{h:02d}:{m:02d}"
            case "h":
                cell.value = f"{h}h" if m == 0 else f"{h}h{m:02d}"
            case "time":
                cell.value = time(h, m)
                cell.number_format = "hh:mm"
            case "float":
                cell.value = minutes / (24 * 60)
            case "datetime":
                cell.value = datetime(1899, 12, 30, h, m)
                cell.number_format = "hh:mm"
            case "range":
                cell.value = f"{h:02d}:{m:02d} - {e_h:02d}:{e_m:02d}"
            case "range_as":
                cell.value = f"{h:02d}:{m:02d} às {e_h:02d}:{e_m:02d}"

    def row(self, value: str | time | int) -> int:
        """Linha do slot que começa em `value` ("HH:MM", time ou minutos)."""
        minutes = value if isinstance(value, int) else _minutes(_as_time(value))
        return self.header_row + 1 + (minutes - self.start) // self.step

    def col(self, room: str) -> int:
        return self.first_room_col + self.rooms.index(room)

    def ref(self, room: str, start: str, end: str | None = None) -> str:
        letter = get_column_letter(self.col(room))
        first = self.row(start)
        last = self.row(end) - 1 if end else first
        return f"{letter}{first}" if last == first else f"{letter}{first}:{letter}{last}"

    def put(
        self,
        room: str,
        start: str,
        end: str | None,
        text: str,
        *,
        merge: bool = True,
        fill: str | None = "FFF2CC",
    ) -> str:
        """Escreve uma reserva de [start, end). merge=False copia o texto em cada linha."""
        col = self.col(room)
        first = self.row(start)
        last = self.row(end) - 1 if end else first
        rows = [first] if merge else list(range(first, last + 1))
        for row in rows:
            cell = self.ws.cell(row, col, text)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if fill:
                cell.fill = PatternFill("solid", start_color=fill, end_color=fill)
        if merge and last > first:
            self.ws.merge_cells(start_row=first, start_column=col, end_row=last, end_column=col)
        return self.ref(room, start, end)

    def paint(self, room: str, start: str, end: str, color: str = "D9D9D9") -> None:
        """Só cor de fundo, sem texto (não é reserva)."""
        for row in range(self.row(start), self.row(end)):
            self.ws.cell(row, self.col(room)).fill = PatternFill(
                "solid", start_color=color, end_color=color
            )


# ---------------------------------------------------------------- planilha de exemplo

SAMPLE_ROOMS = (
    "SALA 01 - EXECUTIVA",
    "Sala 02 – Diretoria",
    "sala 03 -  treinamento",
    "Sala 04 - Inovacao",
    "Sala 05 - Reunião",
    "Sala 06 - Reunião",
    "Sala 07 - Atendimento",
    "Sala 08 - Projetos",
    "Sala 09 - Auditório",
    "Sala 10 - Entrevistas",
    "Sala 11 - Copa",  # não existe no cadastro: aparece em "salas não encontradas"
)


def next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def build_sample_workbook(monday: date) -> Workbook:
    """Planilha de exemplo da semana de `monday` (+ uma quinta passada e uma aba sem data)."""
    wb = Workbook()
    readme = wb.active
    readme.title = "LEIA-ME"
    readme["A1"] = "Planilha de exemplo da agenda de salas (formato legado)."
    readme["A2"] = "Uma aba por dia; salas nas colunas; horários na coluna A."
    readme["A3"] = "Esta aba não tem data e é ignorada pela importação."

    days = [monday + timedelta(days=i) for i in range(5)]
    past = monday - timedelta(days=4)

    ws = wb.create_sheet(f"Qui {past:%d-%m}")
    sheet = LegacySheet(ws, SAMPLE_ROOMS, title=f"AGENDA - QUINTA {past:%d/%m}")
    sheet.put("Sala 05 - Reunião", "09:00", "10:00", "Chamado 48100 - Reunião antiga")

    # Segunda: horários "08:00", linha de grupos acima do cabeçalho, cópias e preenchimentos.
    ws = wb.create_sheet(f"Seg {days[0]:%d-%m}")
    ws["B2"], ws["F2"] = "VIDEOCONFERÊNCIA", "SALAS NORMAIS"
    ws.merge_cells("B2:E2")
    ws.merge_cells("F2:L2")
    sheet = LegacySheet(
        ws,
        SAMPLE_ROOMS,
        title=f"AGENDA DE SALAS - SEGUNDA-FEIRA {days[0]:%d/%m/%Y}",
        header_row=3,
    )
    sheet.put(
        "SALA 01 - EXECUTIVA",
        "09:00",
        "10:30",
        "Chamado 48211\nCarla Mendes\nDiretoria Financeira (Teams)",
    )
    sheet.put("Sala 09 - Auditório", "08:00", "19:00", "#48190 - Marcos Silva - Integração")
    sheet.put(
        "Sala 05 - Reunião", "14:00", "15:00", "48230 João Pereira - RH Entrevista", merge=False
    )
    sheet.put("sala 03 -  treinamento", "08:00", "12:00", "CH-48255 Ricardo Alves / Treinamento")
    sheet.put("Sala 11 - Copa", "10:00", "11:00", "Reunião da copa")
    sheet.paint("Sala 07 - Atendimento", "08:00", "19:00")

    # Terça: horários "8h"/"8h30" e nomes de aba com ponto.
    ws = wb.create_sheet(f"Ter {days[1]:%d.%m}")
    sheet = LegacySheet(ws, SAMPLE_ROOMS, time_format="h")
    sheet.put(
        "Sala 02 – Diretoria",
        "08:00",
        "12:00",
        "INC0048250 Fernanda Costa | Planejamento Estratégico [VC]",
    )
    sheet.put("Sala 06 - Reunião", "10:00", "11:00", "Equipe de TI - Reunião interna")
    sheet.put(
        "sala 03 -  treinamento",
        "13:00",
        "15:00",
        "Chamado 48255 - Ricardo Alves - Treinamento Comercial",
    )

    # Quarta: horários como valores de hora do Excel; nome da aba por extenso com ano curto.
    ws = wb.create_sheet(f"quarta {days[2]:%d-%m-%y}")
    sheet = LegacySheet(ws, SAMPLE_ROOMS, time_format="time")
    sheet.put(
        "Sala 04 - Inovacao",
        "08:30",
        "09:30",
        "Patrícia Rocha\nSquad Produtos\nChamado 48262 - Zoom",
    )
    sheet.put(
        "SALA 01 - EXECUTIVA",
        "14:00",
        "16:00",
        "Chamado 48270 Luís Fernandes - Comitê de Investimentos (9 pessoas) vídeo",
    )
    sheet.put(
        "Sala 07 - Atendimento",
        "15:00",
        "16:30",
        "48275 - Sônia Martins - Atendimento a fornecedor",
    )

    # Quinta: horários como frações do dia (célula sem formatação), ano completo no nome.
    ws = wb.create_sheet(f"Qui {days[3]:%d.%m.%Y}")
    sheet = LegacySheet(ws, SAMPLE_ROOMS, time_format="float")
    sheet.put(
        "Sala 02 – Diretoria", "13:00", "19:00", "Chamado 48281\nGustavo Nogueira\nConselho - Teams"
    )
    sheet.put("Sala 08 - Projetos", "09:00", "12:00", "48284 Juliana Prado - Workshop de Processos")
    sheet.put("Sala 05 - Reunião", "16:00", "17:00", "Diretoria Jurídica #48290")

    # Sexta: horários como faixa ("08:00 - 08:30") e uma cópia da aba com uma alteração.
    for suffix, first_ticket, first_start, first_end in (
        ("", "48301", "10:00", "11:00"),
        (" (cópia)", "48399", "10:30", "11:30"),
    ):
        ws = wb.create_sheet(f"Sex {days[4]:%d-%m}{suffix}")
        sheet = LegacySheet(ws, SAMPLE_ROOMS, time_format="range")
        sheet.put(
            "SALA 01 - EXECUTIVA",
            first_start,
            first_end,
            f"Chamado {first_ticket} - Rafael Duarte - Alinhamento semanal",
        )
        sheet.put("Sala 10 - Entrevistas", "11:00", None, "Camila Torres - Entrevista Estágio")
        sheet.put(
            "sala 03 -  treinamento", "14:00", "18:00", "48310 Eduardo Ramos - Treinamento Teams"
        )
    return wb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera a planilha legada de exemplo.")
    parser.add_argument("--monday", type=date.fromisoformat, help="segunda-feira (AAAA-MM-DD)")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="arquivo .xlsx")
    args = parser.parse_args(argv)
    monday = args.monday or next_monday(date.today())
    if monday.weekday() != 0:
        parser.error(f"{monday:%d/%m/%Y} não é uma segunda-feira")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_sample_workbook(monday).save(args.output)
    if hasattr(sys.stdout, "reconfigure"):  # console do Windows em cp1252: caminho com "ã"
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"Planilha de exemplo gerada: {args.output} (semana de {monday:%d/%m/%Y})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
