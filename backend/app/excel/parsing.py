"""Heurísticas puras (sem banco) para ler a planilha legada: horários, datas das abas, nomes de
salas e o texto de cada reserva. Cada regra está descrita em docs/IMPORTACAO.md.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.excel.common import (
    PART_SEPARATOR,
    WEEKDAY_NAMES,
    fold,
    is_ticket_line,
    parse_time_line,
    ticket_of_line,
)
from app.models import PeriodType

# ---------------------------------------------------------------- horários

# Uma hora: "08:00", "8:00:00", "8h", "8h30", "08H30", "8hs", "8.30", "8:00h", "8h30min".
_TIME = (
    r"(?P<{p}h>[01]?\d|2[0-3])"
    r"(?:\s*(?::|h(?:rs?|s)?)\s*(?P<{p}m>[0-5]\d)?(?::[0-5]\d)?|\.(?P<{p}d>[0-5]\d))"
    r"(?:\s*(?:h(?:rs?|s)?|min))?"
)
_RANGE_SEP = r"\s*(?:-|–|—|/|~|a|à|às|as|ao|até|ate)\s*"
# "das 8h às 9h", "de 14h15 às 15h45": "de/da/das/des" como palavra própria e na mesma linha
# (sem o \b, o fim de "Tarde", "Qualidade" ou "Unidade" era engolido pela faixa de horário).
_PREFIX = r"(?:\bd[ae]s?[ \t]+)?"

_TIME_CELL_RE = re.compile(
    rf"^\s*{_PREFIX}{_TIME.format(p='a')}(?:{_RANGE_SEP}{_TIME.format(p='b')})?\s*$",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    rf"(?<![\d:.]){_PREFIX}{_TIME.format(p='a')}{_RANGE_SEP}{_TIME.format(p='b')}(?![\d:])",
    re.IGNORECASE,
)

# Datas que o Excel usa como "dia zero" quando a célula guarda só um horário.
_EXCEL_EPOCHS = {date(1899, 12, 30), date(1899, 12, 31), date(1900, 1, 1)}


def minutes_of(t: time) -> int:
    return t.hour * 60 + t.minute


def time_of(minutes: int) -> time | None:
    if 0 <= minutes < 24 * 60:
        return time(minutes // 60, minutes % 60)
    return None


def _round_to_minute(t: time) -> time | None:
    seconds = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1_000_000
    return time_of(round(seconds / 60))


def _match_time(m: re.Match, p: str) -> time | None:
    minutes = m.group(f"{p}m") or m.group(f"{p}d") or "0"
    return time(int(m.group(f"{p}h")), int(minutes))


def parse_time_value(value: object) -> tuple[time, time | None] | None:
    """Valor de uma célula da coluna de horários -> (início, fim explícito ou None).

    Aceita time/datetime/timedelta do Excel, número fracionário do dia (0.5 = 12:00) e textos
    como "08:00", "8h", "8h30", "08:00 - 08:30" ou "08:00 às 08:30".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        if value.time() == time(0) and value.date() not in _EXCEL_EPOCHS:
            return None  # é uma data, não um horário
        t = _round_to_minute(value.time())
        return (t, None) if t else None
    if isinstance(value, time):
        t = _round_to_minute(value)
        return (t, None) if t else None
    if isinstance(value, date):
        return None
    if isinstance(value, timedelta):
        t = time_of(round(value.total_seconds() / 60))
        return (t, None) if t and value.total_seconds() > 0 else None
    if isinstance(value, (int, float)):
        # Só frações do dia (horário sem formatação). Inteiros soltos costumam ser numeração.
        if isinstance(value, float) and math.isfinite(value) and 0 < value < 1:
            t = time_of(round(value * 24 * 60))
            return (t, None) if t else None
        return None
    if isinstance(value, str):
        m = _TIME_CELL_RE.match(value)
        if m is None:
            return None
        start = _match_time(m, "a")
        end = _match_time(m, "b") if m.group("bh") else None
        if end is not None and minutes_of(end) <= minutes_of(start):
            end = None
        return start, end
    return None


def find_time_range(text: str) -> tuple[tuple[time, time], tuple[int, int]] | None:
    """Primeira faixa "14:15–15:45" / "14h às 15h30" escrita no meio de um texto.

    Retorna ((início, fim), (pos_inicial, pos_final)) ou None.
    """
    for m in _TIME_RANGE_RE.finditer(text):
        start, end = _match_time(m, "a"), _match_time(m, "b")
        if minutes_of(end) > minutes_of(start):
            return (start, end), m.span()
    return None


def is_time_header(text: str) -> bool:
    return fold(text).rstrip(":") in {"horario", "horarios", "hora", "horas", "hr", "hrs", "h"}


# ---------------------------------------------------------------- datas das abas


@dataclass(frozen=True)
class DateGuess:
    day: int
    month: int
    year: int | None = None
    weekday: int | None = None  # 0 = segunda
    has_context: bool = False  # ano, mês por extenso ou dia da semana presentes


_WEEKDAY_WORDS = {
    "segunda": 0, "seg": 0, "terca": 1, "ter": 1, "quarta": 2, "qua": 2, "quinta": 3, "qui": 3,
    "sexta": 4, "sex": 4, "sabado": 5, "sab": 5, "domingo": 6, "dom": 6,
}  # fmt: skip
_MONTHS = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}  # fmt: skip
_WEEKDAY_RE = re.compile(
    r"\b(" + "|".join(sorted(_WEEKDAY_WORDS, key=len, reverse=True)) + r")(?:-?feira)?\b"
)
_WEEKDAY_WINDOW_DAYS = 200
_ISO_RE = re.compile(r"(?<!\d)(?P<y>\d{4})[-./_](?P<m>\d{1,2})[-./_](?P<d>\d{1,2})(?!\d)")
_NUMERIC_RE = re.compile(
    r"(?<!\d)(?P<d>\d{1,2})\s*[-./_]\s*(?P<m>\d{1,2})(?:\s*[-./_]\s*(?P<y>\d{4}|\d{2}))?(?![\d:])"
)
_MONTH_NAME_RE = re.compile(
    r"(?<!\d)(?P<d>\d{1,2})\s*(?:de\s+)?[-./_]?\s*"
    r"(?P<mon>jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*\.?"
    r"(?:\s*(?:de\s+)?[-./_]?\s*(?P<y>\d{4}|\d{2})(?!\d))?"
)


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    year = int(raw)
    return 2000 + year if year < 100 else year


def _valid(d: int, m: int, y: int | None) -> bool:
    try:
        date(y or 2000, m, d)  # 2000 é bissexto: 29/02 sem ano é aceito
    except ValueError:
        return False
    return True


def parse_date_text(text: str) -> DateGuess | None:
    """Data em nomes de aba e títulos: "26-10", "26.10", "26/10", "26-10-2026", "Seg 26-10",
    "segunda 26.10.26", "26 de outubro", "2026-10-26"..."""
    folded = fold(text)
    wd_match = _WEEKDAY_RE.search(folded)
    weekday = _WEEKDAY_WORDS[wd_match.group(1)] if wd_match else None

    for m in _ISO_RE.finditer(folded):
        d, mo, y = int(m["d"]), int(m["m"]), int(m["y"])
        if _valid(d, mo, y):
            return DateGuess(d, mo, y, weekday, has_context=True)
    for m in _MONTH_NAME_RE.finditer(folded):
        d, mo, y = int(m["d"]), _MONTHS[m["mon"]], _year(m["y"])
        if _valid(d, mo, y):
            return DateGuess(d, mo, y, weekday, has_context=True)
    for m in _NUMERIC_RE.finditer(folded):
        d, mo, y = int(m["d"]), int(m["m"]), _year(m["y"])
        if _valid(d, mo, y):
            return DateGuess(d, mo, y, weekday, has_context=y is not None or weekday is not None)
    return None


def resolve_date(guess: DateGuess, today: date) -> tuple[date | None, str | None]:
    """Data concreta + aviso opcional. Sem ano: o ano que deixa a data mais perto de hoje
    (preferindo o que bate com o dia da semana escrito, se houver).

    (None, motivo) quando a data não existe em nenhum ano próximo (29/02 sem ano fora dos anos
    bissextos): melhor ignorar a aba com aviso do que importar para um ano distante."""
    if guess.year is not None:
        try:
            resolved = date(guess.year, guess.month, guess.day)
        except ValueError:
            return None, f"{guess.day:02d}/{guess.month:02d}/{guess.year} não existe"
    else:
        years = (today.year - 1, today.year, today.year + 1)
        candidates = []
        for y in years:
            try:
                candidates.append(date(y, guess.month, guess.day))
            except ValueError:  # 29/02 em ano não bissexto
                continue
        if not candidates:
            return None, (
                f"{guess.day:02d}/{guess.month:02d} sem ano não existe em "
                f"{years[0]}, {years[1]} nem {years[2]}; escreva o ano no nome da aba "
                f"(ex.: {guess.day:02d}-{guess.month:02d}-AAAA)"
            )
        pool = candidates
        if guess.weekday is not None:
            # O dia da semana só desempata datas a até ~6 meses de hoje; mais longe que isso,
            # é mais provável o dia da semana estar errado do que a aba ser de outro ano.
            pool = [
                c
                for c in candidates
                if c.weekday() == guess.weekday and abs((c - today).days) <= _WEEKDAY_WINDOW_DAYS
            ] or candidates
        resolved = min(pool, key=lambda c: (abs((c - today).days), c))
    warning = None
    if guess.weekday is not None and resolved.weekday() != guess.weekday:
        warning = (
            f"o dia da semana escrito ({WEEKDAY_NAMES[guess.weekday].lower()}) não confere com "
            f"{resolved:%d/%m/%Y} ({WEEKDAY_NAMES[resolved.weekday()].lower()}); "
            f"usada a data {resolved:%d/%m/%Y}"
        )
    return resolved, warning


def date_from_cell(value: object) -> DateGuess | None:
    """Data numa célula do topo da aba (valor de data ou texto com ano/mês/dia da semana)."""
    if isinstance(value, datetime):
        if value.date() in _EXCEL_EPOCHS:
            return None
        return DateGuess(value.day, value.month, value.year, has_context=True)
    if isinstance(value, date):
        return DateGuess(value.day, value.month, value.year, has_context=True)
    if isinstance(value, str) and len(value) <= 120:
        guess = parse_date_text(value)
        if guess is not None and guess.has_context:
            return guess
    return None


# ---------------------------------------------------------------- nomes de salas


@dataclass(frozen=True)
class RoomRef:
    """Sala cadastrada (dados simples: a leitura da planilha roda fora do loop async)."""

    id: int
    name: str
    is_active: bool = True
    is_video: bool = False  # effective_type == VIDEO
    capacity: int = 0


def room_key(text: str) -> str:
    """Chave de comparação: sem acento/caixa/pontuação, sem o prefixo "Sala" e sem zeros à
    esquerda ("SALA 01 – Executiva" == "sala 1 executiva" == "01 - Executiva")."""
    tokens = re.sub(r"[^a-z0-9]+", " ", fold(text)).split()
    if len(tokens) > 1 and tokens[0] == "sala":
        tokens = tokens[1:]
    return " ".join(t.lstrip("0") or "0" if t.isdigit() else t for t in tokens)


_CAPACITY_RE = re.compile(r"\b\d+\s*(?:lugares|lugar|pessoas|pax|cadeiras|lug\.?)", re.IGNORECASE)


def _header_candidates(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    base = [text, lines[0]]
    variants = []
    for item in base:
        variants.append(item)
        no_parens = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", item)
        variants.append(no_parens)
        variants.append(_CAPACITY_RE.sub(" ", no_parens))
    return list(dict.fromkeys(v for v in variants if v.strip()))


class RoomMatcher:
    """Associa textos de cabeçalho às salas cadastradas: exato pela chave; aproximado só quando
    há um único candidato (cabeçalho só com o número, só com o nome, ou cadastro só com o número).
    Nunca associa pelo número quando os nomes divergem ("Sala 1 - Copa" não vira "Sala 01 -
    Executiva")."""

    def __init__(self, rooms: list[RoomRef]) -> None:
        self._names: dict[str, RoomRef] = {}  # nome exato (sem caixa/acento/espaços extras)
        self._exact: dict[str, list[RoomRef]] = defaultdict(list)
        self._by_number: dict[str, list[RoomRef]] = defaultdict(list)
        self._by_rest: dict[str, list[RoomRef]] = defaultdict(list)
        # Ativas primeiro: uma sala desativada nunca "rouba" o cabeçalho de uma ativa com o
        # mesmo nome normalizado (ex.: "Sala 5 - Reunião" antiga x "Sala 05 - Reunião").
        for room in sorted(rooms, key=lambda r: not r.is_active):
            self._names.setdefault(fold(room.name), room)
            key = room_key(room.name)
            self._exact[key].append(room)
            number, rest = _split_number(key)
            if number is not None:
                self._by_number[number].append(room)
            if rest and number is not None:
                self._by_rest[rest].append(room)

    def match(self, text: str) -> tuple[RoomRef | None, bool]:
        """(sala, aproximado?) ou (None, False)."""
        candidates = _header_candidates(text)
        for candidate in candidates:
            room = self._names.get(fold(candidate))
            if room is not None:
                return room, False
        keys = [k for k in (room_key(c) for c in candidates) if k]
        for key in keys:
            if key in self._exact:
                return self._exact[key][0], False
        for key in keys:
            number, _rest = _split_number(key)
            rules = (
                self._by_number.get(key, []),  # "Sala 01" -> "Sala 01 - Executiva"
                self._by_rest.get(key, []),  # "Executiva" -> "Sala 01 - Executiva"
                self._exact.get(number, []) if number else [],  # cadastro só "Sala 01"
            )
            for found in rules:
                unique = _prefer_active(found)
                if len(unique) == 1:
                    return unique[0], True
                if len(unique) > 1:
                    return None, False
        return None, False

    @staticmethod
    def is_exact_name(text: str, room: RoomRef) -> bool:
        """O cabeçalho traz exatamente o nome cadastrado (sem caixa/acentos/espaços extras)."""
        name = fold(room.name)
        return any(fold(candidate) == name for candidate in _header_candidates(text))

    def ambiguous(self, room: RoomRef) -> list[RoomRef]:
        """Outras salas ATIVAS com o mesmo nome normalizado (o cabeçalho pode ser de qualquer
        uma delas)."""
        if not room.is_active:
            return []
        return [r for r in self._exact.get(room_key(room.name), []) if r.is_active and r != room]


def _prefer_active(rooms: list[RoomRef]) -> list[RoomRef]:
    unique = list({r.id: r for r in rooms}.values())
    return [r for r in unique if r.is_active] or unique


def _split_number(key: str) -> tuple[str | None, str]:
    tokens = key.split()
    if tokens and tokens[0].isdigit():
        return tokens[0], " ".join(tokens[1:])
    return None, key


# Rótulos de cabeçalho que não são salas: grupos de colunas e colunas auxiliares.
_NON_ROOM_LABELS = {
    "videoconferencia", "salas de videoconferencia", "salas videoconferencia", "video",
    "salas normais", "normais", "salas", "sala normal", "salas comuns",
    "obs", "observacao", "observacoes", "notas", "anotacoes", "legenda", "total", "totais",
}  # fmt: skip
# Textos que a recepção escreve em horários vagos: não são reservas.
_FREE_SLOT_WORDS = {"livre", "vago", "vaga", "disponivel", "free"}


def looks_like_room_header(text: str) -> bool:
    """Texto do cabeçalho que provavelmente é uma sala (para listar em unknown_rooms).

    Não há limite de tamanho: um cabeçalho longo ("Sala 12 - ... descrição comprida") também
    é listado (o nome mostrado é cortado), em vez de a coluna sumir sem aviso."""
    folded = fold(text).rstrip(":.")
    if not folded or is_time_header(text) or folded in _NON_ROOM_LABELS:
        return False
    return parse_time_value(text) is None and date_from_cell(text) is None


def is_auxiliary_header(text: str) -> bool:
    """Coluna auxiliar ou rótulo de grupo ("Observações", "Total", "Salas Normais"): o que
    estiver nela é ignorado sem aviso."""
    return fold(text).rstrip(":.") in _NON_ROOM_LABELS


def is_free_slot_marker(text: str) -> bool:
    """Célula que só marca horário vago ("LIVRE", "-", "..."): não é reserva."""
    folded = fold(text).strip(" .!-–—_*/")
    return not any(ch.isalnum() for ch in folded) or folded in _FREE_SLOT_WORDS


# ---------------------------------------------------------------- texto da reserva


@dataclass(frozen=True)
class CellText:
    requester: str | None
    title: str | None
    notes: str | None
    ticket: str | None
    attendees: int | None
    requires_video: bool
    explicit: tuple[time, time] | None  # faixa de horário escrita dentro da célula
    period: PeriodType | None = None  # só no modo exportação (vem dos selos)


_BADGE_RE = re.compile(r"\[([^\[\]\n]{1,60})\]")
_ATTENDEES_RE = re.compile(
    r"\(?\b(?P<n>\d{1,5})\s*(?:participantes?|pessoas|pax|convidados)\b\)?", re.IGNORECASE
)
_TICKET_ID = r"#?\s*(?P<id>[A-Za-z]{0,8}-?\d{3,}[A-Za-z0-9/.-]*)"
_TICKET_LINE_RE = re.compile(
    r"^\s*(?:chamado|ticket|ch)\b\.?\s*(?:n[º°o]\.?\s*)?[:#\-]?\s*(?P<id>#?\S+)\s*$", re.IGNORECASE
)
_TICKET_PATTERNS = (
    # "Chamado 48211", "Chamado nº 48211", "CH-48211", "Ticket: #48211", "Chamado INC0012345"
    re.compile(
        r"\b(?:chamado|ticket|ch)\b\.?\s*(?:n[º°o]\.?\s*)?[:#\-]?\s*" + _TICKET_ID, re.IGNORECASE
    ),
    # ServiceNow e afins: INC0012345, RITM0012345, REQ..., SCTASK..., CHG...
    re.compile(r"\b(?P<id>(?:INC|RITM|REQ|SCTASK|TASK|CHG|PRB|SR|WO)\d{4,})\b", re.IGNORECASE),
    re.compile(r"#\s*(?P<id>\d{3,})\b"),
)
_BARE_TICKET_RE = re.compile(r"(?<![\w/.:,#-])(?P<id>\d{4,})(?![\w/:,]|[.-]\d)")
_NOT_TICKET_WORDS = {"ramal", "tel", "telefone", "fone", "cel", "celular", "sala", "andar", "cep"}

_VIDEO_HINT_RE = re.compile(
    r"\b(?:videos?|videoconferencias?|videochamadas?|teams|zoom|meet|webex|skype)\b"
)
_VC_RE = re.compile(r"\bVC\b")  # só maiúsculo: "vc" minúsculo costuma ser "você"
_PART_SEPARATOR_RE = re.compile(r"^\s*[-–—_=]{3,}\s*$", re.MULTILINE)
_EDGE_SEPARATORS = "-–—/|:;,\\ \t"


def split_cell_parts(text: str, *, export_mode: bool = False) -> list[str]:
    """Partes de uma célula separadas por uma linha só de traços (várias reservas na célula).
    No modo exportação, só a linha separadora exata que a exportação escreve divide."""
    if export_mode:
        parts, current = [], []
        for line in _lines(text):
            if line.strip() == PART_SEPARATOR:
                parts.append("\n".join(current))
                current = []
            else:
                current.append(line)
        parts.append("\n".join(current))
    else:
        parts = _PART_SEPARATOR_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def parse_cell_text(raw: str, *, export_mode: bool = False) -> CellText:
    """Extrai chamado, solicitante, assunto, participantes e indício de vídeo de uma célula.

    Planilha legada: solicitante e assunto vêm de linhas diferentes ou de partes separadas por
    " - ", "|" ou "/". Planilha exportada pelo sistema (`export_mode`): ver parse_export_text.
    """
    if export_mode:
        return parse_export_text(raw)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    video_flag: bool | None = None
    attendees: int | None = None

    def badge(m: re.Match) -> str:
        nonlocal video_flag, attendees
        kind, value = _classify_badge(m.group(1))
        if kind is None:
            return m.group(0)
        # na planilha legada o período vem das linhas ocupadas, não do selo
        if kind == "video":
            video_flag = bool(value)
        elif kind == "attendees":
            attendees = int(str(value))
        return " "

    text = _BADGE_RE.sub(badge, text)

    explicit = None
    found = find_time_range(text)
    if found is not None:
        explicit, (a, b) = found
        text = text[:a] + " " + text[b:]

    lines = [cleaned for line in text.split("\n") if (cleaned := _clean(line))]
    ticket, lines = _extract_ticket(lines, allow_bare=True)

    if attendees is None:
        for i, line in enumerate(lines):
            m = _ATTENDEES_RE.search(line)
            if m and 0 < int(m["n"]) <= 10_000:
                attendees = int(m["n"])
                lines[i] = _clean(line[: m.start()] + " " + line[m.end() :])
                break
        lines = [line for line in lines if line]

    if video_flag is None:
        video_flag = _has_video_hint(" ".join(lines))

    requester = title = notes = None
    if len(lines) == 1:
        parts = _split_single_line(lines[0])
        requester = parts[0]
        title = " - ".join(parts[1:]) or None
    elif lines:
        requester = lines[0]
        title = lines[1] if len(lines) > 1 else None
        notes = "\n".join(lines[2:]) or None
    return CellText(
        requester=requester,
        title=title,
        notes=notes,
        ticket=ticket,
        attendees=attendees,
        requires_video=video_flag,
        explicit=explicit,
    )


def parse_export_text(raw: str) -> CellText:
    """Texto de uma reserva escrita pela exportação, lido POR POSIÇÃO (sem as heurísticas da
    planilha legada, para a ida e volta ser exata):

        [Chamado <nº>]          opcional; "Chamado —" = sem chamado
        <solicitante>           exatamente como gravado
        [<assunto>]             opcional (linhas a mais viram observações)
        [HH:MM–HH:MM]           opcional: a última linha antes dos selos
        [<selos>]               opcional: a última linha (vídeo, participantes, período)

    A exportação acrescenta a linha de chamado ou de horário sempre que o solicitante ou o
    assunto poderiam ser confundidos com elas (ver exporter._booking_text).
    """
    lines = [line.strip() for line in _lines(raw)]
    lines = [line for line in lines if line]
    video = False
    attendees: int | None = None
    period = PeriodType.CUSTOM
    if lines and is_badge_line(lines[-1]):
        for inner in _BADGE_RE.findall(lines.pop()):
            kind, value = _classify_badge(inner)
            if kind == "video":
                video = bool(value)
            elif kind == "attendees":
                attendees = int(str(value))
            elif kind == "period":
                period = value  # type: ignore[assignment]
    explicit = parse_time_line(lines[-1]) if lines else None
    if explicit is not None:
        lines.pop()
    ticket = None
    if lines and is_ticket_line(lines[0]):
        ticket = ticket_of_line(lines.pop(0))
    return CellText(
        requester=lines[0] if lines else None,
        title=lines[1] if len(lines) > 1 else None,
        notes="\n".join(lines[2:]) or None,
        ticket=ticket,
        attendees=attendees,
        requires_video=video,
        explicit=explicit,
        period=period,
    )


def is_badge_line(line: str) -> bool:
    """Linha só com selos conhecidos ("[Dia inteiro] [Videoconferência]")."""
    stripped = line.strip()
    if not stripped.startswith("[") or _BADGE_RE.sub("", stripped).strip():
        return False
    return all(_classify_badge(inner)[0] is not None for inner in _BADGE_RE.findall(stripped))


def _classify_badge(inner: str) -> tuple[str | None, object]:
    """Selo entre colchetes -> (tipo, valor); (None, None) = colchete comum, fica no texto."""
    key = " ".join(re.sub(r"[^a-z0-9]+", " ", fold(inner)).split())
    if key in _PERIOD_BADGES:
        return "period", _PERIOD_BADGES[key]
    if key in _STATUS_BADGES:
        return "ignored", None  # status não é importado
    if key.startswith("presencial") or key == "sem video":
        return "video", False
    if key in _VIDEO_BADGES:
        return "video", True
    m = re.fullmatch(r"(\d{1,5}) (?:participantes?|pessoas|pax)", key)
    if m and 0 < int(m.group(1)) <= 10_000:
        return "attendees", m.group(1)
    return None, None


_PERIOD_BADGES = {
    "dia inteiro": PeriodType.FULL_DAY,
    "manha toda": PeriodType.MORNING,
    "tarde toda": PeriodType.AFTERNOON,
    "manha": PeriodType.MORNING,
    "tarde": PeriodType.AFTERNOON,
}
_STATUS_BADGES = {"check in", "checkin", "nao compareceu", "no show"}
_VIDEO_BADGES = {
    "videoconferencia", "video", "com video", "precisa de video", "vc", "teams", "zoom", "meet",
}  # fmt: skip


def _clean(text: str) -> str:
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)
    text = " ".join(text.split())
    return text.strip(_EDGE_SEPARATORS)


def _extract_ticket(lines: list[str], *, allow_bare: bool) -> tuple[str | None, list[str]]:
    for i, line in enumerate(lines):
        m = _TICKET_LINE_RE.match(line)
        if m and any(ch.isdigit() for ch in m["id"]):
            ticket = _normalize_ticket(m["id"])
            if ticket:
                return ticket, lines[:i] + lines[i + 1 :]
    patterns: list[re.Pattern] = list(_TICKET_PATTERNS)
    if allow_bare:
        patterns.append(_BARE_TICKET_RE)
    for pattern in patterns:
        for i, line in enumerate(lines):
            for m in pattern.finditer(line):
                if pattern is _BARE_TICKET_RE and not _plausible_bare_ticket(line, m):
                    continue
                ticket = _normalize_ticket(m["id"])
                if not ticket:
                    continue
                rest = _clean(line[: m.start()] + " " + line[m.end() :])
                new_lines = lines[:i] + ([rest] if rest else []) + lines[i + 1 :]
                return ticket, new_lines
    return None, lines


def _normalize_ticket(raw: str) -> str | None:
    ticket = raw.strip().lstrip("#").strip().rstrip(".,;:)")
    if not ticket or len(ticket) > 50:
        return None
    if re.fullmatch(r"(?:INC|RITM|REQ|SCTASK|TASK|CHG|PRB|SR|WO)\d+", ticket, re.IGNORECASE):
        return ticket.upper()
    return ticket


def _plausible_bare_ticket(line: str, m: re.Match) -> bool:
    digits = m["id"]
    if len(digits) == 4 and 1990 <= int(digits) <= 2099:
        return False  # parece um ano
    previous = re.findall(r"[a-z]+", fold(line[: m.start()]))
    return not (previous and previous[-1] in _NOT_TICKET_WORDS)


def _has_video_hint(text: str) -> bool:
    return bool(_VIDEO_HINT_RE.search(fold(text)) or _VC_RE.search(text))


def _split_single_line(line: str) -> list[str]:
    for pattern in (r"\s*\|\s*", r"\s+[-–—]\s+", r"\s*[–—]\s*", r"\s*/\s*"):
        parts = [p for p in (_clean(x) for x in re.split(pattern, line)) if p]
        if len(parts) > 1:
            return parts
    return [line]
