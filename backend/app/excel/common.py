"""Constantes compartilhadas entre exportação e importação.

O texto que a exportação escreve (cabeçalhos, selos, "Chamado <n>") é o mesmo que a importação
reconhece: uma planilha exportada pode ser importada de volta sem perda (round-trip).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, time

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Gravado em workbook.properties.keywords pela exportação. Planilhas com essa marca são lidas
# no "modo exportação" (texto das células em formato fixo, ver parsing.parse_cell_text).
EXPORT_MARKER = "agenda-salas:export:v1"
EXPORT_CREATOR = "Agenda de Salas"

WEEKDAY_NAMES = (
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
)
WEEKDAY_ABBR = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")

TITLE_PREFIX = "Agenda de Salas"
TIME_HEADER = "Horário"
GROUP_VIDEO = "Videoconferência"
GROUP_NORMAL = "Salas Normais"
MAINTENANCE_NOTE = "(vídeo em manutenção)"
INACTIVE_NOTE = "(sala desativada)"

TICKET_LABEL = "Chamado"
# "Chamado —": reserva sem chamado cujo solicitante começa com "Chamado" (sem essa linha a
# importação leria o solicitante como chamado).
TICKET_NONE = "—"
# Linha que separa duas reservas na mesma célula (só acontece com horários fora dos slots).
PART_SEPARATOR = "———"
_TIME_LINE_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\s*[–-]\s*([01]\d|2[0-3]):([0-5]\d)$")

BADGE_FULL_DAY = "[Dia inteiro]"
BADGE_MORNING = "[Manhã toda]"
BADGE_AFTERNOON = "[Tarde toda]"
BADGE_VIDEO = "[Videoconferência]"
BADGE_VIDEO_UNAVAILABLE = "[⚠ Precisa de vídeo]"
BADGE_RELOCATABLE = "[Presencial — remanejável]"
BADGE_CHECKED_IN = "[Check-in]"
BADGE_NO_SHOW = "[Não compareceu]"


def attendees_badge(count: int) -> str:
    return f"[{count} participante{'' if count == 1 else 's'}]"


def ticket_line(ticket: str | None) -> str:
    return f"{TICKET_LABEL} {ticket or TICKET_NONE}"


def is_ticket_line(line: str) -> bool:
    """Linha "Chamado <nº>" escrita pela exportação (a primeira da reserva)."""
    folded = fold(line)
    label = fold(TICKET_LABEL)
    return folded == label or folded.startswith(label + " ")


def ticket_of_line(line: str) -> str | None:
    ticket = line.strip()[len(TICKET_LABEL) :].strip()
    return None if ticket in ("", TICKET_NONE) else ticket


def time_line(start: time, end: time) -> str:
    return f"{start:%H:%M}–{end:%H:%M}"


def parse_time_line(line: str) -> tuple[time, time] | None:
    """Linha "HH:MM–HH:MM" da exportação (só ela, sem outro texto) -> (início, fim)."""
    m = _TIME_LINE_RE.match(line.strip())
    if m is None:
        return None
    start, end = time(int(m[1]), int(m[2])), time(int(m[3]), int(m[4]))
    return (start, end) if end > start else None


def sheet_name(d: date) -> str:
    """Nome da aba de um dia: "Seg 26-10" (Excel não aceita "/" em nomes de aba)."""
    return f"{WEEKDAY_ABBR[d.weekday()]} {d:%d-%m}"


def sheet_title(d: date) -> str:
    return f"{TITLE_PREFIX} — {WEEKDAY_NAMES[d.weekday()]}, {d:%d/%m/%Y}"


def fold(text: str) -> str:
    """Minúsculas, sem acentos e com espaços simples (comparações tolerantes)."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())
