"""Leitura da planilha legada: cada aba vira uma lista de reservas candidatas, com avisos.

Síncrono e sem banco: o importador chama `read_workbook` em uma thread (a leitura é CPU-bound e
não pode travar o loop que atende REST e WebSocket). O arquivo é lido em streaming e com
limites (app/excel/xlsx.py). As heurísticas estão em docs/IMPORTACAO.md.
"""

from __future__ import annotations

import logging
import threading
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, time
from itertools import pairwise

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange

from app.config import settings
from app.excel.common import EXPORT_MARKER, fold
from app.excel.parsing import (
    CellText,
    DateGuess,
    RoomMatcher,
    RoomRef,
    date_from_cell,
    is_auxiliary_header,
    is_free_slot_marker,
    is_time_header,
    looks_like_room_header,
    minutes_of,
    parse_cell_text,
    parse_date_text,
    parse_time_value,
    resolve_date,
    split_cell_parts,
    time_of,
)
from app.excel.xlsx import InvalidWorkbookError, SheetData, XlsxFile, open_xlsx
from app.models import PeriodType

log = logging.getLogger(__name__)

PLACEHOLDER_REQUESTER = "Não informado"
# Reservas candidatas num arquivo só (cada uma custa algumas consultas ao banco).
MAX_CANDIDATES = 50_000
_MAX_REFS = 15  # células listadas num aviso agregado
_MAX_HEADER = 60  # nome de sala desconhecida mostrado no relatório
# Linhas sem rótulo entre dois horários que ainda contam como parte da grade.
_MAX_UNLABELED_GAP = 100
# Uma leitura de planilha por vez: limita o pico de memória e CPU do processo.
_PARSE_SLOTS = threading.BoundedSemaphore(1)


@dataclass
class Candidate:
    """Reserva encontrada numa aba, pronta para virar um BookingCreate."""

    sheet: str
    ref: str  # célula ou intervalo ("C5", "C5:C8")
    room: RoomRef
    day: date
    start: time
    end: time
    period: PeriodType
    requester: str
    title: str | None
    notes: str | None
    ticket: str | None
    attendees: int | None
    requires_video: bool
    warnings: list[str] = field(default_factory=list)
    error: str | None = None  # motivo para pular (ex.: horário desalinhado)
    order: tuple[int, int] = (0, 0)
    source_text: str = ""  # texto da célula (ou da parte dela) de onde a reserva saiu

    @property
    def where(self) -> str:
        return (
            f'Aba "{self.sheet}", {self.ref} ({self.room.name}, '
            f"{self.start:%H:%M}–{self.end:%H:%M})"
        )


@dataclass
class SheetPlan:
    name: str
    status: str  # READY | SKIPPED_PAST | SKIPPED_UNRECOGNIZED | ERROR
    day: date | None = None
    candidates: list[Candidate] = field(default_factory=list)
    ignored: int = 0  # reservas em colunas de salas desconhecidas, desativadas ou sem cabeçalho
    warnings: list[str] = field(default_factory=list)
    unknown_rooms: list[str] = field(default_factory=list)


@dataclass
class WorkbookPlan:
    sheets: list[SheetPlan]
    export_mode: bool
    errors: list[str] = field(default_factory=list)


def open_workbook(source: bytes) -> XlsxFile:
    """Abre o .xlsx (validação de tamanho e estrutura; as abas são lidas sob demanda)."""
    return open_xlsx(source)


def read_workbook(
    source: bytes, rooms: list[RoomRef], *, today: date, include_past: bool
) -> WorkbookPlan:
    with _PARSE_SLOTS:
        book = open_workbook(source)
        try:
            return _read(book, rooms, today=today, include_past=include_past)
        finally:
            book.close()


def _read(book: XlsxFile, rooms: list[RoomRef], *, today: date, include_past: bool) -> WorkbookPlan:
    export_mode = EXPORT_MARKER in book.keywords
    matcher = RoomMatcher(rooms)
    plan = WorkbookPlan(sheets=[], export_mode=export_mode)
    seen_days: dict[date, str] = {}
    total = 0
    for index, name in enumerate(book.sheetnames):
        try:
            data = book.read_sheet(index)
        except InvalidWorkbookError:
            plan.sheets.append(
                SheetPlan(
                    name=name,
                    status="ERROR",
                    warnings=["O conteúdo desta aba está corrompido; ela foi ignorada."],
                )
            )
            plan.errors.append(f'Aba "{name}": conteúdo corrompido, não foi possível ler.')
            continue
        try:
            sheet = _SheetReader(data, matcher, export_mode=export_mode).read(
                today=today, include_past=include_past
            )
        except Exception:
            log.exception("Erro ao ler a aba %r", name)
            sheet = SheetPlan(
                name=name,
                status="ERROR",
                warnings=["Erro inesperado ao ler esta aba; ela foi ignorada."],
            )
            plan.errors.append(f'Aba "{name}": erro inesperado ao ler a aba.')
        del data
        total += len(sheet.candidates)
        if total > MAX_CANDIDATES:
            raise InvalidWorkbookError(
                f"A planilha tem mais de {MAX_CANDIDATES:,} reservas para importar de uma vez. "
                "Divida o arquivo (por exemplo, um por mês) e importe cada parte.".replace(",", ".")
            )
        if sheet.day is not None and sheet.status == "READY":
            if sheet.day in seen_days:
                sheet.warnings.insert(
                    0,
                    f'A data {sheet.day:%d/%m/%Y} também aparece na aba "{seen_days[sheet.day]}"; '
                    "reservas repetidas ou conflitantes entre as duas são puladas.",
                )
            else:
                seen_days[sheet.day] = name
        plan.sheets.append(sheet)
    return plan


# ---------------------------------------------------------------- uma aba


@dataclass
class _Column:
    """Coluna de sala definida por uma célula do cabeçalho (pode estar mesclada na horizontal)."""

    first: int
    last: int
    header: str
    room: RoomRef | None
    fuzzy: bool = False
    aux: bool = False  # coluna auxiliar ("Observações", "Total"): conteúdo ignorado sem aviso
    source: str = ""  # texto completo da célula do cabeçalho

    @property
    def letters(self) -> str:
        a, b = get_column_letter(self.first), get_column_letter(self.last)
        return a if a == b else f"{a}:{b}"

    def covers(self, col: int) -> bool:
        return self.first <= col <= self.last

    def overlaps(self, first: int, last: int) -> bool:
        return self.first <= last and first <= self.last


@dataclass
class _Band:
    """Linhas de cabeçalho seguidas (sem horários entre elas), ex.: grupos + salas."""

    rows: list[int]
    columns: list[_Column] = field(default_factory=list)

    @property
    def first(self) -> int:
        return self.rows[0]

    @property
    def last(self) -> int:
        return self.rows[-1]


@dataclass
class _Block:
    """Célula (ou intervalo mesclado) com texto numa coluna de sala."""

    rows: list[int]
    text: str
    min_row: int
    max_row: int
    min_col: int
    max_col: int
    has_plain: bool  # contém célula não mesclada (cópia de texto em vez de mesclagem)

    @property
    def ref(self) -> str:
        a = f"{get_column_letter(self.min_col)}{self.min_row}"
        b = f"{get_column_letter(self.max_col)}{self.max_row}"
        return a if a == b else f"{a}:{b}"


class _Timeline:
    """Horário de início de cada linha de um bloco da grade (minutos desde 00:00)."""

    def __init__(self, starts: dict[int, int], end: int) -> None:
        self.rows = sorted(starts)
        self.starts = starts
        self.end = end

    def __contains__(self, row: int) -> bool:
        return row in self.starts

    def next_row(self, row: int) -> int | None:
        i = bisect_right(self.rows, row)
        return self.rows[i] if i < len(self.rows) else None

    def span(self, first_row: int, last_row: int) -> tuple[int, int]:
        """Do início da primeira linha ao início da linha seguinte à última."""
        last_start = self.starts[last_row]
        for r in self.rows[bisect_right(self.rows, last_row) :]:
            if self.starts[r] > last_start:
                return self.starts[first_row], self.starts[r]
        return self.starts[first_row], self.end


class _SheetReader:
    def __init__(self, sheet: SheetData, matcher: RoomMatcher, *, export_mode: bool) -> None:
        self.sheet = sheet
        self.name = sheet.name
        self.matcher = matcher
        self.export_mode = export_mode
        # Só as células preenchidas, ordenadas por (linha, coluna) para que avisos e reservas
        # saiam sempre na mesma ordem.
        self.values: dict[tuple[int, int], object] = dict(sorted(sheet.values.items()))
        self.merges: dict[tuple[int, int], CellRange] = {}
        for rng in sorted(sheet.merges, key=lambda r: (r.min_row, r.min_col)):
            self.merges.setdefault((rng.min_row, rng.min_col), rng)
        _drop_covered_values(self.values, list(self.merges.values()))

    # ------------------------------------------------------------ orquestração

    def read(self, *, today: date, include_past: bool) -> SheetPlan:
        plan = SheetPlan(name=self.name, status="READY")
        if self.sheet.hidden:
            plan.status = "SKIPPED_UNRECOGNIZED"
            plan.warnings.append(
                "Aba oculta na planilha: ignorada. Se as reservas dela valem, reexiba a aba no "
                "Excel e importe de novo."
            )
            return plan
        if self.sheet.problem:
            plan.status = "SKIPPED_UNRECOGNIZED"
            plan.warnings.append(self.sheet.problem)
            return plan

        time_col, time_rows = self._time_column()
        first_time_row = min(time_rows) if time_rows else None

        day, warning = self._sheet_date(today, first_time_row)
        if day is None:
            plan.status = "SKIPPED_UNRECOGNIZED"
            plan.warnings.append(
                f"Data inválida: {warning}."
                if warning
                else 'Data não identificada: use o nome da aba (ex.: "Seg 26-10", "26.10.2026") '
                "ou uma célula com a data no topo."
            )
            return plan
        plan.day = day
        if warning:
            plan.warnings.append(f"Data da aba: {warning}.")
        if day < today and not include_past:
            plan.status = "SKIPPED_PAST"
            return plan
        if time_col is None:
            plan.status = "SKIPPED_UNRECOGNIZED"
            plan.warnings.append(
                "Não encontrei a coluna de horários (valores como 08:00, 8h, 8h30 ou "
                "08:00 - 08:30)."
            )
            return plan

        hidden = self._drop_hidden(time_col)
        hidden_warning = (
            f"{len(hidden)} célula(s) com texto em linhas ou colunas ocultas foram ignoradas: "
            f"{format_refs([ref for *_, ref in sorted(hidden)])}. Se valem, reexiba-as no "
            "Excel e importe de novo."
            if hidden
            else None
        )
        bands = self._header_bands(time_col, time_rows, first_time_row, plan)
        if not bands:
            plan.status = "SKIPPED_UNRECOGNIZED"
            plan.warnings.append("Não encontrei a linha de cabeçalho com os nomes das salas.")
            if hidden_warning:
                plan.warnings.append(hidden_warning)
            return plan

        stray: list[tuple[int, int, str]] = []  # (linha, coluna, referência)
        free: list[tuple[int, int, str]] = []
        unheaded: list[tuple[int, int, str]] = []
        for i, band in enumerate(bands):
            last_row = bands[i + 1].first - 1 if i + 1 < len(bands) else None
            labeled = {
                r: v
                for r, v in time_rows.items()
                if r > band.last and (last_row is None or r <= last_row)
            }
            if not labeled:
                continue
            timeline = self._timeline(labeled, time_col, last_row, plan)
            if timeline is None:
                continue
            blocks = self._blocks(
                band, timeline, time_col, last_row, plan, stray, free, unheaded
            )
            for column, column_blocks in blocks:
                self._add_column(plan, column, column_blocks, timeline, day)
        # Horários acima do primeiro cabeçalho não pertencem a nenhum bloco da grade.
        for (row, col), value in self.values.items():
            if row >= bands[0].first:
                break
            if row in time_rows and col != time_col and _as_text(value) is not None:
                stray.append((row, col, f"{get_column_letter(col)}{row}"))

        if free:
            plan.warnings.append(
                f'{len(free)} célula(s) só com marcação de horário vago ("LIVRE", "-") '
                f"ignorada(s): {format_refs([ref for *_, ref in sorted(free)])}."
            )
        if unheaded:
            plan.ignored += len(unheaded)
            plan.warnings.append(
                f"{len(unheaded)} reserva(s) em colunas sem nome de sala no cabeçalho foram "
                f"ignoradas: {format_refs([ref for *_, ref in sorted(unheaded)])}. Escreva o "
                "nome da sala no cabeçalho dessas colunas."
            )
        if stray:
            plan.warnings.append(
                f"{len(stray)} célula(s) com texto fora da grade de horários foram ignoradas: "
                f"{format_refs([ref for *_, ref in sorted(stray)])}."
            )
        if hidden_warning:
            plan.warnings.append(hidden_warning)
        formulas = self.sheet.formulas_without_value
        if formulas:
            plan.warnings.append(
                f"{len(formulas)} célula(s) com fórmula sem valor calculado foram ignoradas: "
                f"{format_refs(formulas)}. Abra a planilha no Excel e salve de novo para gravar "
                "os valores."
            )
        plan.candidates.sort(key=lambda c: (c.start, c.order))
        return plan

    # ------------------------------------------------------------ data

    def _sheet_date(
        self, today: date, first_time_row: int | None
    ) -> tuple[date | None, str | None]:
        top = min(first_time_row - 1, 10) if first_time_row else 6
        cell_guesses: list[tuple[str, DateGuess]] = []
        for (row, col), value in self.values.items():
            if row > top:
                break
            guess = date_from_cell(value)
            if guess is not None:
                cell_guesses.append((f"{get_column_letter(col)}{row}", guess))

        name_guess = parse_date_text(self.name)
        if name_guess is None:
            with_year = [g for _, g in cell_guesses if g.year is not None]
            pool = with_year or [g for _, g in cell_guesses]
            return resolve_date(pool[0], today) if pool else (None, None)

        notes: list[str] = []
        if name_guess.year is None:
            for _, guess in cell_guesses:
                if guess.year and (guess.day, guess.month) == (name_guess.day, name_guess.month):
                    name_guess = replace(name_guess, year=guess.year)
                    break
        resolved, warning = resolve_date(name_guess, today)
        if resolved is None:
            return None, warning
        if warning:
            notes.append(warning)
        for ref, guess in cell_guesses:
            if (guess.day, guess.month) != (resolved.day, resolved.month) or (
                guess.year is not None and guess.year != resolved.year
            ):
                other = f"{guess.day:02d}/{guess.month:02d}" + (
                    f"/{guess.year}" if guess.year else ""
                )
                notes.append(
                    f"a célula {ref} mostra {other}, diferente do nome da aba; "
                    f"usada a data do nome ({resolved:%d/%m/%Y})"
                )
                break
        return resolved, "; ".join(notes) or None

    # ------------------------------------------------------------ coluna de horários

    def _time_column(self) -> tuple[int | None, dict[int, tuple[time, time | None]]]:
        hidden_cols = self.sheet.hidden_cols
        by_col: dict[int, dict[int, tuple[time, time | None]]] = defaultdict(dict)
        for (row, col), value in self.values.items():
            if col in hidden_cols:
                continue
            parsed = parse_time_value(value)
            if parsed is not None:
                by_col[col][row] = parsed
        if not by_col:
            return None, {}
        col, rows = max(by_col.items(), key=lambda kv: (len(kv[1]), -kv[0]))
        if len(rows) < 2:
            return None, {}
        return col, rows

    # ------------------------------------------------------------ linhas e colunas ocultas

    def _drop_hidden(self, time_col: int) -> list[tuple[int, int, str]]:
        """Tira da leitura o que está em linhas/colunas ocultas (cópias antigas escondidas não
        podem virar reservas nem ganhar conflitos das visíveis). Os rótulos da coluna de
        horários continuam valendo, para as linhas visíveis manterem seus horários."""
        rows, cols = self.sheet.hidden_rows, self.sheet.hidden_cols
        if not rows and not cols:
            return []
        dropped: list[tuple[int, int, str]] = []
        for row, col in list(self.values):
            if col == time_col or (row not in rows and col not in cols):
                continue
            value = self.values.pop((row, col))
            self.merges.pop((row, col), None)
            if _as_text(value) is not None:
                dropped.append((row, col, f"{get_column_letter(col)}{row}"))
        return dropped

    # ------------------------------------------------------------ cabeçalho

    def _header_bands(
        self,
        time_col: int,
        time_rows: dict[int, tuple[time, time | None]],
        first_time_row: int,
        plan: SheetPlan,
    ) -> list[_Band]:
        texts: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for (row, col), value in self.values.items():
            if col == time_col or row in time_rows:
                continue
            text = _as_text(value)
            if text is not None:
                texts[row].append((col, text))

        # Conta só correspondências exatas: um texto de reserva como "Diretoria" pode casar por
        # semelhança com "Sala 02 - Diretoria" e não deve transformar a linha em cabeçalho.
        exact: dict[int, int] = {}
        loose: dict[int, int] = {}
        for row, cells in texts.items():
            found = [self.matcher.match(text) for _, text in cells]
            exact[row] = sum(1 for room, fuzzy in found if room is not None and not fuzzy)
            loose[row] = sum(1 for room, _ in found if room is not None)
        matches = exact if max(exact.values(), default=0) > 0 else loose
        best = max(matches.values(), default=0)
        if best > 0:
            # Linha com bem menos salas que a melhor ainda é cabeçalho quando começa uma tabela
            # (rótulo "Horário" ou logo depois de uma linha que não é da grade): bloco menor
            # embaixo (só a tarde, só 2 salas) ou linha de grupos com uma sala mesclada.
            threshold = max(1, (best + 1) // 2)
            grid_rows = self._time_like_rows(time_col, time_rows)
            rows = [
                row
                for row, n in matches.items()
                if n >= threshold or (n > 0 and self._starts_table(row, time_col, grid_rows))
            ]
        else:
            rows = self._fallback_header(texts, time_col, first_time_row)

        sorted_time_rows = sorted(time_rows)
        bands: list[_Band] = []
        for row in sorted(rows):
            if bands and not _any_between(sorted_time_rows, bands[-1].last, row):
                bands[-1].rows.append(row)  # sem horários entre as duas: mesmo cabeçalho
            else:
                bands.append(_Band([row]))
        for band in bands:
            band.columns = self._band_columns(band, texts, plan)
        return [band for band in bands if any(not c.aux for c in band.columns)]

    def _time_like_rows(
        self, time_col: int, time_rows: dict[int, tuple[time, time | None]]
    ) -> set[int]:
        rows = set(time_rows)
        for (row, col), rng in self.merges.items():
            if col == time_col and row in time_rows and rng.min_col == rng.max_col:
                rows.update(range(rng.min_row, min(rng.max_row, row + 48) + 1))
        return rows

    def _starts_table(self, row: int, time_col: int, grid_rows: set[int]) -> bool:
        label = self.values.get((row, time_col))
        if isinstance(label, str) and is_time_header(label):
            return True
        return row - 1 not in grid_rows

    def _fallback_header(
        self, texts: dict[int, list[tuple[int, str]]], time_col: int, first_time_row: int
    ) -> list[int]:
        """Nenhuma sala reconhecida: usa a linha do rótulo "Horário" ou a linha logo acima da
        grade, para que os nomes apareçam em unknown_rooms."""
        for row in range(first_time_row - 1, 0, -1):
            label = self.values.get((row, time_col))
            if isinstance(label, str) and is_time_header(label) and texts.get(row):
                return [row]
        above = [row for row in texts if row < first_time_row]
        return [max(above)] if above else []

    def _band_columns(
        self, band: _Band, texts: dict[int, list[tuple[int, str]]], plan: SheetPlan
    ) -> list[_Column]:
        """Colunas do cabeçalho: a linha mais perto da grade manda; das linhas de cima entram
        só salas reconhecidas e células mescladas até a última linha do cabeçalho (ex.: "Sala
        11" em K1:K2 numa linha de grupos)."""
        columns: list[_Column] = []
        for row in reversed(band.rows):
            for col, text in texts.get(row, []):
                if any(c.covers(col) for c in columns):
                    continue
                rng = self.merges.get((row, col))
                last = rng.max_col if rng else col
                room, fuzzy = self.matcher.match(text)
                if row != band.last:
                    reaches = rng is not None and rng.max_row >= band.last
                    if not reaches and (room is None or fuzzy):
                        continue
                header = text.strip().splitlines()[0].strip()
                if room is None:
                    if is_auxiliary_header(text):
                        columns.append(_Column(col, last, header, None, aux=True, source=text))
                        continue
                    if not looks_like_room_header(text):
                        continue
                    if len(header) > _MAX_HEADER:
                        header = header[: _MAX_HEADER - 1] + "…"
                columns.append(_Column(col, last, header, room, fuzzy=fuzzy, source=text))
        columns.sort(key=lambda c: c.first)

        rooms_seen: dict[int, _Column] = {}
        for column in columns:
            room = column.room
            if column.aux:
                continue
            if room is None:
                if column.header not in plan.unknown_rooms:
                    plan.unknown_rooms.append(column.header)
                continue
            if column.fuzzy:
                plan.warnings.append(
                    f'Coluna {column.letters} ("{column.header}") associada à sala "{room.name}" '
                    "por semelhança — confira."
                )
            else:
                others = self.matcher.ambiguous(room)
                if others and not self.matcher.is_exact_name(column.source, room):
                    names = ", ".join(f'"{r.name}"' for r in [room, *others])
                    plan.warnings.append(
                        f'Coluna {column.letters} ("{column.header}"): mais de uma sala ativa tem '
                        f'nome equivalente ({names}); usada "{room.name}" — confira.'
                    )
            if room.id in rooms_seen:
                plan.warnings.append(
                    f'A sala "{room.name}" aparece em mais de uma coluna '
                    f"({rooms_seen[room.id].letters} e {column.letters})."
                )
            else:
                rooms_seen[room.id] = column
        return columns

    # ------------------------------------------------------------ linhas -> horários

    def _timeline(
        self,
        labeled: dict[int, tuple[time, time | None]],
        time_col: int,
        last_row: int | None,
        plan: SheetPlan,
    ) -> _Timeline | None:
        rows = sorted(labeled)
        if not rows:
            return None
        starts_min = [minutes_of(labeled[r][0]) for r in rows]
        keep = _increasing(starts_min)
        kept = set(keep)
        valid: list[tuple[int, int, int | None]] = [
            (rows[i], starts_min[i], _end_minutes(labeled[rows[i]][1])) for i in keep
        ]
        per_row = self._steps(valid, time_col)[1]

        # Rótulos fora da sequência: um erro de digitação (19:00 no lugar de 09:00) não pode
        # descartar o resto do dia. Se o horário esperado pela posição cabe entre os vizinhos,
        # ele é usado; senão a linha é ignorada.
        ignored: set[int] = set()
        fixed: list[tuple[int, int, int | None]] = []
        for i, row in enumerate(rows):
            if i in kept:
                continue
            label = labeled[row][0]
            expected = _expected_start(row, valid, per_row)
            if expected is not None:
                plan.warnings.append(
                    f"Linha {row}: horário {label:%H:%M} parece digitado errado (esperado "
                    f"{_fmt(expected)}); usado {_fmt(expected)}."
                )
                fixed.append((row, expected, None))
            else:
                plan.warnings.append(
                    f"Linha {row}: horário {label:%H:%M} fora de ordem na coluna de horários; "
                    "linha ignorada."
                )
                ignored.add(row)
        if fixed:
            valid = sorted(valid + fixed)

        label_step, per_row = self._steps(valid, time_col)
        last_row_labeled, last_start, last_end = valid[-1]
        end = last_end if last_end else last_start + label_step

        starts: dict[int, int] = {}
        for (ra, ta, _), (rb, tb, _) in pairwise(valid):
            starts[ra] = previous = ta
            if rb - ra > _MAX_UNLABELED_GAP:
                continue  # rótulos muito distantes: as linhas entre eles não são da grade
            for k in range(1, rb - ra):
                if ra + k in ignored:
                    continue
                candidate = ta + per_row * k
                # linha sem rótulo entre dois horários: segue o passo da grade (ex.: 2 linhas
                # por hora) ou continua o slot anterior se o passo não couber
                previous = candidate if candidate < tb else previous
                starts[ra + k] = previous
        starts[last_row_labeled] = last_start
        k = 1
        while per_row > 0:
            row, candidate = last_row_labeled + k, last_start + per_row * k
            if candidate >= end or (last_row is not None and row > last_row) or row in ignored:
                break
            starts[row] = candidate
            k += 1
        return _Timeline(starts, end)

    def _steps(self, valid: list[tuple[int, int, int | None]], time_col: int) -> tuple[int, int]:
        """(passo entre rótulos, passo por linha) em minutos."""
        per_row_steps = []
        label_steps = []
        for (ra, ta, _), (rb, tb, _) in pairwise(valid):
            label_steps.append(tb - ta)
            if (tb - ta) % (rb - ra) == 0:
                per_row_steps.append((tb - ta) // (rb - ra))
        if label_steps:
            label_step = _mode(label_steps)
            return label_step, (_mode(per_row_steps) if per_row_steps else label_step)
        last_row_labeled, last_start, last_end = valid[-1]
        label_step = (last_end - last_start) if last_end else settings.slot_minutes
        rng = self.merges.get((last_row_labeled, time_col))
        height = rng.max_row - rng.min_row + 1 if rng else 1
        return label_step, (label_step // height if label_step % height == 0 else label_step)

    # ------------------------------------------------------------ células -> blocos

    def _blocks(
        self,
        band: _Band,
        timeline: _Timeline,
        time_col: int,
        last_row: int | None,
        plan: SheetPlan,
        stray: list[tuple[int, int, str]],
        free: list[tuple[int, int, str]],
        unheaded: list[tuple[int, int, str]],
    ) -> list[tuple[_Column, list[_Block]]]:
        """Blocos de texto por coluna de sala. `stray` recebe células fora da grade, `free` as
        que só marcam horário vago e `unheaded` as da grade em colunas sem sala no cabeçalho."""
        columns = [c for c in band.columns if not c.aux]
        aux = [c for c in band.columns if c.aux]
        per_column: dict[int, list[_Block]] = defaultdict(list)

        def column_index(col: int) -> int | None:
            for i, column in enumerate(columns):
                if column.covers(col):
                    return i
            return None

        def auxiliary(first: int, last: int) -> bool:
            return any(c.overlaps(first, last) for c in aux)

        def is_free(text: str) -> bool:
            # Na exportação o texto é sempre uma reserva (um solicitante "-" é válido).
            return not self.export_mode and is_free_slot_marker(text)

        for (row, col), rng in self.merges.items():
            if row not in timeline or rng.min_col == rng.max_col == time_col:
                continue  # rótulo de horário mesclado (ex.: "8h" sobre duas linhas)
            text = _as_text(self.values.get((row, col)))
            if text is None:
                continue  # mesclagem só com preenchimento (cor) não é reserva
            if is_free(text):
                free.append((row, col, rng.coord))
                continue
            if rng.min_col <= time_col <= rng.max_col:
                plan.warnings.append(
                    f'{rng.coord}: faixa "{_short(text)}" atravessa a coluna de horários; '
                    "tratada como aviso (não é reserva)."
                )
                continue
            covered = [
                i for i, column in enumerate(columns) if column.overlaps(rng.min_col, rng.max_col)
            ]
            if not covered:
                if not auxiliary(rng.min_col, rng.max_col) and parse_time_value(text) is None:
                    unheaded.append((row, col, rng.coord))
                continue
            rows = [r for r in timeline.rows if rng.min_row <= r <= rng.max_row]
            if len(covered) > 1:
                names = ", ".join(
                    columns[i].room.name if columns[i].room else columns[i].header for i in covered
                )
                plan.warnings.append(
                    f"{rng.coord}: célula mesclada sobre {len(covered)} salas ({names}); "
                    "uma reserva para cada sala."
                )
            for i in covered:
                per_column[i].append(
                    _Block(rows, text, rng.min_row, rng.max_row, rng.min_col, rng.max_col, False)
                )

        for (row, col), value in self.values.items():
            if row <= band.last or (last_row is not None and row > last_row):
                continue
            if col == time_col or (row, col) in self.merges:
                continue  # células cobertas por mesclagem não têm valor
            ref = f"{get_column_letter(col)}{row}"
            i = column_index(col)
            if i is None:
                # Texto na grade numa coluna sem sala no cabeçalho: avisa em vez de sumir.
                # Uma segunda coluna de horários (à direita, para leitura) não conta.
                text = _as_text(value)
                if (
                    row in timeline
                    and text is not None
                    and not is_free(text)
                    and parse_time_value(value) is None
                    and not auxiliary(col, col)
                ):
                    unheaded.append((row, col, ref))
                continue
            text = _as_text(value)
            if text is None:
                plan.warnings.append(f"{ref}: valor que não é texto ignorado.")
                continue
            if row not in timeline:
                stray.append((row, col, ref))
                continue
            if is_free(text):
                free.append((row, col, ref))
                continue
            per_column[i].append(_Block([row], text, row, row, col, col, True))

        result = []
        for i, column in enumerate(columns):
            blocks = self._column_blocks(column, per_column.get(i, []), timeline, plan)
            if blocks:
                result.append((column, blocks))
        return result

    def _column_blocks(
        self, column: _Column, blocks: list[_Block], timeline: _Timeline, plan: SheetPlan
    ) -> list[_Block]:
        blocks = sorted(blocks, key=lambda b: (b.min_row, b.min_col))
        if column.first == column.last:
            return blocks if self.export_mode else self._join_copies(blocks, timeline)

        # Cabeçalho mesclado sobre várias colunas (ex.: "Responsável | Assunto" sob a mesma
        # sala): textos lado a lado nas mesmas linhas são uma reserva só, não um conflito.
        by_sub: dict[int, list[_Block]] = defaultdict(list)
        for block in blocks:
            by_sub[block.min_col].append(block)
        joined: list[_Block] = []
        for sub in sorted(by_sub):
            part = by_sub[sub]
            joined.extend(part if self.export_mode else self._join_copies(part, timeline))
        joined.sort(key=lambda b: (b.min_row, b.min_col))
        clusters: list[list[_Block]] = []
        for block in joined:
            if clusters and block.min_row <= max(b.max_row for b in clusters[-1]):
                clusters[-1].append(block)
            else:
                clusters.append([block])
        result: list[_Block] = []
        refs: list[str] = []
        for cluster in clusters:
            if len(cluster) == 1:
                result.append(cluster[0])
                continue
            ordered = sorted(cluster, key=lambda b: (b.min_col, b.min_row))
            result.append(
                _Block(
                    sorted({r for b in cluster for r in b.rows}),
                    "\n".join(b.text for b in ordered),
                    min(b.min_row for b in cluster),
                    max(b.max_row for b in cluster),
                    min(b.min_col for b in cluster),
                    max(b.max_col for b in cluster),
                    any(b.has_plain for b in cluster),
                )
            )
            refs.append(" + ".join(b.ref for b in ordered))
        if refs:
            plan.warnings.append(
                f'Coluna {column.letters} ("{column.header}"): o cabeçalho cobre '
                f"{column.last - column.first + 1} colunas; textos lado a lado foram unidos numa "
                f"reserva só: {format_refs(refs)}."
            )
        return result

    def _join_copies(self, blocks: list[_Block], timeline: _Timeline) -> list[_Block]:
        """Células seguidas com o MESMO texto na mesma sala viram uma reserva só (quem não
        mescla costuma copiar o texto). Dois intervalos mesclados distintos não são unidos."""
        joined: list[_Block] = []
        for block in blocks:
            prev = joined[-1] if joined else None
            if (
                prev is not None
                and (prev.has_plain or block.has_plain)
                and fold(prev.text) == fold(block.text)
                and block.rows
                and prev.rows
                and timeline.next_row(prev.rows[-1]) == block.rows[0]
            ):
                prev.rows.extend(block.rows)
                prev.max_row = max(prev.max_row, block.max_row)
                prev.min_col, prev.max_col = (
                    min(prev.min_col, block.min_col),
                    max(prev.max_col, block.max_col),
                )
                prev.has_plain = True
                continue
            joined.append(block)
        return joined

    # ------------------------------------------------------------ blocos -> candidatas

    def _add_column(
        self,
        plan: SheetPlan,
        column: _Column,
        blocks: list[_Block],
        timeline: _Timeline,
        day: date,
    ) -> None:
        blocks = [b for b in blocks if b.rows]
        if not blocks:
            return
        room = column.room
        if room is None or not room.is_active:
            plan.ignored += len(blocks)
            reason = "sala não cadastrada" if room is None else "sala desativada"
            plan.warnings.append(
                f'Coluna {column.letters} ("{column.header}"): {reason} — {len(blocks)} '
                f"reserva(s) ignorada(s): {format_refs([b.ref for b in blocks])}."
            )
            return
        for block in blocks:
            plan.candidates.extend(self._candidates(plan.name, block, room, timeline, day))

    def _candidates(
        self, sheet: str, block: _Block, room: RoomRef, timeline: _Timeline, day: date
    ) -> list[Candidate]:
        span = timeline.span(block.rows[0], block.rows[-1])
        parts = split_cell_parts(block.text, export_mode=self.export_mode)
        texts = [(p, parse_cell_text(p, export_mode=self.export_mode)) for p in parts]
        if len(texts) < 2 or not all(
            t.explicit and _overlaps(_minutes(t.explicit), span) for _, t in texts
        ):
            texts = [(block.text, parse_cell_text(block.text, export_mode=self.export_mode))]
        return [self._candidate(sheet, block, room, span, day, t, src) for src, t in texts]

    def _candidate(
        self,
        sheet: str,
        block: _Block,
        room: RoomRef,
        span: tuple[int, int],
        day: date,
        text: CellText,
        source: str,
    ) -> Candidate:
        found: list[str] = []
        start, end = span
        notes = text.notes
        if text.explicit is not None:
            written = _minutes(text.explicit)
            written_label = _fmt_span(written)
            if _overlaps(written, span):
                if not (span[0] <= written[0] and written[1] <= span[1]):
                    found.append(
                        f"horário escrito na célula ({written_label}) vai além das linhas ocupadas "
                        f"({_fmt_span(span)}); usado o horário escrito"
                    )
                start, end = written
            else:
                found.append(
                    f"horário escrito na célula ({written_label}) não corresponde às linhas "
                    f"ocupadas ({_fmt_span(span)}); usado o das linhas"
                )
                notes = _join_notes(notes, f"Horário escrito na planilha: {written_label}")

        period, start, end = self._period(start, end, found, text.period)
        error = None
        if start % 5 or end % 5:
            error = "horário fora do padrão de 5 minutos"
        start_t, end_t = time_of(start), time_of(end)
        if start_t is None or end_t is None:
            error = "a reserva passa da meia-noite"
            start_t, end_t = time_of(min(start, 23 * 60 + 59)), time(23, 59)

        requester = text.requester
        title = text.title
        if not requester:
            requester = PLACEHOLDER_REQUESTER
            found.append(f'solicitante não identificado; registrado como "{PLACEHOLDER_REQUESTER}"')
        if len(requester) > 150:
            requester = requester[:149] + "…"
            found.append("nome do solicitante cortado em 150 caracteres")
        if title and len(title) > 200:
            notes = _join_notes(notes, title)
            title = title[:199] + "…"
            found.append("assunto cortado em 200 caracteres (texto completo nas observações)")
        if notes and len(notes) > 2000:
            notes = notes[:1999] + "…"
        if text.requires_video and not room.is_video:
            found.append("indica videoconferência, mas a sala não tem vídeo operacional")
        if text.attendees and room.capacity and text.attendees > room.capacity:
            found.append(f"{text.attendees} participantes para uma sala de {room.capacity} lugares")

        candidate = Candidate(
            sheet=sheet,
            ref=block.ref,
            room=room,
            day=day,
            start=start_t,
            end=end_t,
            period=period,
            requester=requester,
            title=title,
            notes=notes,
            ticket=text.ticket,
            attendees=text.attendees,
            requires_video=text.requires_video,
            error=error,
            order=(block.min_col, block.min_row),
            source_text=source,
        )
        candidate.warnings = [f"{candidate.where}: {msg}." for msg in found]
        return candidate

    @staticmethod
    def _period(
        start: int, end: int, found: list[str], hint: PeriodType | None = None
    ) -> tuple[PeriodType, int, int]:
        """Tipo de período pelas linhas ocupadas (planilha legada) ou pelo selo `hint`
        (exportação: um Horário Específico de 08:00 às 12:00 continua específico)."""
        day_start, day_end = minutes_of(settings.day_start), minutes_of(settings.day_end)
        morning_end = minutes_of(settings.morning_end)
        afternoon_start = minutes_of(settings.afternoon_start)
        presets = {
            PeriodType.FULL_DAY: (day_start, day_end),
            PeriodType.MORNING: (day_start, morning_end),
            PeriodType.AFTERNOON: (afternoon_start, day_end),
        }
        if hint is not None and hint in presets:
            preset = presets[hint]
            if (start, end) != preset:
                found.append(
                    f"o selo de período não confere com as linhas ({_fmt_span((start, end))}); "
                    f"usado o período do selo ({_fmt_span(preset)})"
                )
            return hint, *preset
        if hint is None:
            if start <= day_start and end >= day_end:
                if (start, end) != (day_start, day_end):
                    found.append(
                        f"bloco de {_fmt_span((start, end))} importado como Dia Inteiro "
                        f"({_fmt_span((day_start, day_end))})"
                    )
                return PeriodType.FULL_DAY, day_start, day_end
            if (start, end) == (day_start, morning_end):
                return PeriodType.MORNING, start, end
            if (start, end) == (afternoon_start, day_end):
                return PeriodType.AFTERNOON, start, end
        if start < day_start or end > day_end:
            found.append(
                f"fora do horário de funcionamento ({_fmt_span((day_start, day_end))}); "
                "importada mesmo assim"
            )
        return PeriodType.CUSTOM, start, end


# ---------------------------------------------------------------- utilitários


def _drop_covered_values(
    values: dict[tuple[int, int], object], merges: list[CellRange], budget: int = 500_000
) -> None:
    """Valores "escondidos" sob uma mesclagem (fora da célula do canto) não contam, como no
    Excel (o LibreOffice pode gravá-los). Trabalho limitado para mesclagens absurdas."""
    if not merges or not values:
        return
    rows = sorted({row for row, _ in values})
    by_row: dict[int, list[CellRange]] = defaultdict(list)
    for rng in merges:
        lo, hi = bisect_left(rows, rng.min_row), bisect_right(rows, rng.max_row)
        budget -= hi - lo
        if budget < 0:
            return
        for row in rows[lo:hi]:
            by_row[row].append(rng)
    for row, col in list(values):
        for rng in by_row.get(row, ()):
            budget -= 1
            if budget < 0:
                return
            if rng.min_col <= col <= rng.max_col and (row, col) != (rng.min_row, rng.min_col):
                del values[(row, col)]
                break


def _increasing(values: list[int]) -> list[int]:
    """Índices da maior subsequência estritamente crescente (O(n log n)). Num valor repetido,
    fica a primeira ocorrência."""
    tails: list[int] = []
    tail_values: list[int] = []
    previous = [-1] * len(values)
    for i, value in enumerate(values):
        pos = bisect_left(tail_values, value)
        if pos < len(tail_values) and tail_values[pos] == value:
            continue
        previous[i] = tails[pos - 1] if pos else -1
        if pos == len(tails):
            tails.append(i)
            tail_values.append(value)
        else:
            tails[pos] = i
            tail_values[pos] = value
    result: list[int] = []
    i = tails[-1] if tails else -1
    while i != -1:
        result.append(i)
        i = previous[i]
    return result[::-1]


def _expected_start(row: int, valid: list[tuple[int, int, int | None]], per_row: int) -> int | None:
    """Horário que a linha teria pelo passo da grade, se couber entre os rótulos vizinhos."""
    if per_row <= 0:
        return None
    before = [(r, t) for r, t, _ in valid if r < row]
    after = [(r, t) for r, t, _ in valid if r > row]
    if before and after:
        (rp, tp), (_, tn) = before[-1], after[0]
        expected = tp + per_row * (row - rp)
        return expected if tp < expected < tn else None
    if after:
        rn, tn = after[0]
        expected = tn - per_row * (rn - row)
        return expected if 0 <= expected < tn else None
    return None


def _any_between(sorted_rows: list[int], low: int, high: int) -> bool:
    i = bisect_right(sorted_rows, low)
    return i < len(sorted_rows) and sorted_rows[i] < high


def _end_minutes(value: time | None) -> int | None:
    return minutes_of(value) if value else None


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return None


def _mode(values: list[int]) -> int:
    counts = Counter(v for v in values if v > 0)
    if not counts:
        return settings.slot_minutes
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def _minutes(span: tuple[time, time]) -> tuple[int, int]:
    return minutes_of(span[0]), minutes_of(span[1])


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _fmt_span(span: tuple[int, int]) -> str:
    return f"{_fmt(span[0])}–{_fmt(span[1])}"


def _join_notes(notes: str | None, extra: str) -> str:
    return f"{notes}\n{extra}" if notes else extra


def _short(text: str, limit: int = 40) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def format_refs(refs: list[str]) -> str:
    shown = ", ".join(refs[:_MAX_REFS])
    return shown if len(refs) <= _MAX_REFS else f"{shown} e mais {len(refs) - _MAX_REFS}"


__all__ = [
    "Candidate",
    "format_refs",
    "InvalidWorkbookError",
    "MAX_CANDIDATES",
    "PLACEHOLDER_REQUESTER",
    "SheetPlan",
    "WorkbookPlan",
    "open_workbook",
    "read_workbook",
]
