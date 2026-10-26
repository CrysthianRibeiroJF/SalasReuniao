"""Leitura enxuta e em streaming do .xlsx enviado para importação.

O openpyxl no modo normal cria um objeto Python por célula (inclusive células vazias só com
formatação) e um `MergedCell` para cada célula de cada intervalo mesclado: um arquivo de 1,5 KB
com `<mergeCell ref="A1:XFD1048576"/>`, ou uma planilha com bordas aplicadas a faixas grandes,
esgotava a memória do servidor. Aqui cada parte XML do pacote é lida com `iterparse`, os
elementos são descartados assim que lidos e só ficam guardados os valores não vazios, as
coordenadas das mesclagens e as linhas/colunas ocultas — com limites de tamanho conferidos
ANTES de ler cada parte (tamanho descompactado declarado, que o zipfile respeita na leitura) e
durante a leitura (células preenchidas e mesclagens por aba).

A conversão dos valores segue o openpyxl com `data_only=True`: fórmulas valem pelo último valor
calculado salvo no arquivo; números com formato de data/hora viram date/datetime/time/timedelta.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from xml.etree.ElementTree import ParseError, iterparse

from openpyxl.styles.numbers import builtin_format_code, is_date_format, is_timedelta_format
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries
from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, from_excel, from_ISO8601
from openpyxl.worksheet.cell_range import CellRange

__all__ = [
    "InvalidWorkbookError",
    "SheetData",
    "XlsxFile",
    "open_xlsx",
]

# ---------------------------------------------------------------- limites

# Soma dos tamanhos descompactados de todas as partes do pacote. Uma agenda de um ano inteiro
# (260 abas de ~40 linhas x 25 salas) fica perto de 10 MB; acima disso é quase sempre
# formatação aplicada a faixas enormes ou um arquivo forjado.
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
# Partes grandes com taxa de compressão absurda são "zip bombs" (ex.: 60 MB de espaços).
MAX_COMPRESSION_RATIO = 200
RATIO_CHECK_MIN_BYTES = 4 * 1024 * 1024
MAX_SHEETS = 1000
# Por aba: uma agenda diária tem poucas centenas de células preenchidas e de mesclagens.
MAX_VALUES_PER_SHEET = 20_000
MAX_MERGES_PER_SHEET = 10_000
MAX_SHARED_STRINGS = 1_000_000
MAX_STYLES = 200_000
MAX_TEXT = 32_767  # limite de caracteres de uma célula no Excel

_MAX_ROW = 1_048_576
_MAX_COL = 16_384

INVALID_FILE = (
    "O arquivo não é uma planilha .xlsx válida. Se for um .xls antigo, abra no Excel e salve "
    "como .xlsx."
)
TOO_LARGE = (
    "A planilha é grande demais para importar (mais de {mb} MB de dados descompactados). "
    "Remova abas antigas ou a formatação de linhas e colunas vazias e tente de novo."
)
SUSPICIOUS = (
    'A parte "{name}" da planilha tem compactação fora do normal (arquivo inflado ou '
    "corrompido). Abra no Excel, salve de novo como .xlsx e tente outra vez."
)


class InvalidWorkbookError(Exception):
    """O arquivo não pôde ser aberto como planilha (mensagem pt-BR para o usuário)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------- resultado


@dataclass
class SheetData:
    """Conteúdo útil de uma aba (só células preenchidas)."""

    name: str
    state: str = "visible"  # visible | hidden | veryHidden
    values: dict[tuple[int, int], object] = field(default_factory=dict)  # (linha, coluna)
    merges: list[CellRange] = field(default_factory=list)  # ordenadas por (linha, coluna)
    hidden_rows: set[int] = field(default_factory=set)  # só linhas ocultas com valores
    hidden_cols: set[int] = field(default_factory=set)
    formulas_without_value: list[str] = field(default_factory=list)  # referências
    problem: str | None = None  # aba ignorada por passar dos limites (mensagem pt-BR)

    @property
    def hidden(self) -> bool:
        return self.state != "visible"


@dataclass(frozen=True)
class _SheetRef:
    name: str
    state: str
    part: str | None  # caminho da parte XML no zip (None: alvo ausente)


class XlsxFile:
    """Pacote .xlsx aberto: propriedades e abas lidas sob demanda (uma por vez)."""

    def __init__(self, source: bytes) -> None:
        if not source:
            raise InvalidWorkbookError("O arquivo está vazio.")
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(source))
            infos = self._zip.infolist()
        except (zipfile.BadZipFile, ValueError, OSError):
            raise InvalidWorkbookError(INVALID_FILE) from None
        _check_sizes(infos)
        self._names = {info.filename for info in infos}
        try:
            self._load_package()
        except _READ_ERRORS:
            raise InvalidWorkbookError(INVALID_FILE) from None

    # ------------------------------------------------------------ API

    @property
    def sheetnames(self) -> list[str]:
        return [ref.name for ref in self._sheets]

    def read_sheet(self, index: int) -> SheetData:
        """Lê uma aba (InvalidWorkbookError se o XML dela estiver corrompido)."""
        return self._read_sheet(self._sheets[index])

    def sheets(self) -> Iterator[SheetData]:
        """Lê as abas na ordem do arquivo; cada uma só fica em memória enquanto é usada."""
        for ref in self._sheets:
            yield self._read_sheet(ref)

    def close(self) -> None:
        self._zip.close()

    # ------------------------------------------------------------ pacote

    def _load_package(self) -> None:
        root_rels = self._rels("_rels/.rels", base="")
        workbook = _first(root_rels, "officeDocument") or "xl/workbook.xml"
        if workbook not in self._names:
            raise InvalidWorkbookError(INVALID_FILE)
        self.keywords = ""
        core = _first(root_rels, "core-properties") or "docProps/core.xml"
        if core in self._names:
            self.keywords = self._core_keywords(core)

        rels = self._rels(_rels_path(workbook), base=posixpath.dirname(workbook))
        sheets, self.epoch = self._workbook(workbook, rels)
        self._sheets = sheets
        strings = _first(rels, "sharedStrings")
        self._strings = self._shared_strings(strings) if strings in self._names else []
        styles = _first(rels, "styles")
        self._dates, self._timedeltas = (
            self._number_formats(styles) if styles in self._names else (set(), set())
        )

    def _open(self, name: str):
        return self._zip.open(name)

    def _rels(self, name: str, *, base: str) -> dict[str, tuple[str, str]]:
        """Id -> (tipo curto, caminho da parte)."""
        result: dict[str, tuple[str, str]] = {}
        if name not in self._names:
            return result
        with self._open(name) as src:
            for _, el in _walk(src):
                if _local(el.tag) != "Relationship":
                    continue
                if el.get("TargetMode") == "External":
                    continue
                target = el.get("Target") or ""
                if target.startswith("/"):
                    path = target.lstrip("/")
                else:
                    path = posixpath.normpath(posixpath.join(base, target))
                kind = (el.get("Type") or "").rsplit("/", 1)[-1]
                result[el.get("Id") or ""] = (kind, path)
        return result

    def _workbook(
        self, name: str, rels: dict[str, tuple[str, str]]
    ) -> tuple[list[_SheetRef], object]:
        sheets: list[_SheetRef] = []
        epoch = WINDOWS_EPOCH
        with self._open(name) as src:
            for _, el in _walk(src):
                tag = _local(el.tag)
                if tag == "workbookPr" and el.get("date1904") in ("1", "true"):
                    epoch = MAC_EPOCH
                elif tag == "sheet":
                    rel_id = next((v for k, v in el.attrib.items() if _local(k) == "id"), "")
                    kind, path = rels.get(rel_id, ("", None))
                    if kind != "worksheet":
                        continue  # gráficos e diálogos não têm células
                    sheets.append(
                        _SheetRef(el.get("name") or "", el.get("state") or "visible", path)
                    )
                    if len(sheets) > MAX_SHEETS:
                        raise InvalidWorkbookError(
                            f"A planilha tem mais de {MAX_SHEETS} abas. Remova abas antigas e "
                            "tente de novo."
                        )
        return sheets, epoch

    def _core_keywords(self, name: str) -> str:
        with self._open(name) as src:
            for _, el in _walk(src):
                if _local(el.tag) == "keywords":
                    return (el.text or "")[:1000]
        return ""

    def _shared_strings(self, name: str) -> list[str]:
        strings: list[str] = []
        with self._open(name) as src:
            texts = _TextCollector()
            for event, el in _walk(src, starts=True):
                tag = _local(el.tag)
                if event == "start":
                    if tag == "si":
                        texts.reset()
                    else:
                        texts.start(tag)
                    continue
                if tag == "si":
                    strings.append(texts.value().replace("x005F_", ""))
                    if len(strings) > MAX_SHARED_STRINGS:
                        raise InvalidWorkbookError(TOO_LARGE.format(mb=_mb()))
                else:
                    texts.end(tag, el)
        return strings

    def _number_formats(self, name: str) -> tuple[set[int], set[int]]:
        """Índices de estilo (cellXfs) com formato de data/hora e de duração."""
        custom: dict[int, str] = {}
        xf_formats: list[int] = []
        in_cell_xfs = in_num_fmts = False
        with self._open(name) as src:
            for event, el in _walk(src, starts=True):
                tag = _local(el.tag)
                if tag == "cellXfs":
                    in_cell_xfs = event == "start"
                    continue
                if tag == "numFmts":
                    in_num_fmts = event == "start"
                    continue
                if event != "end":
                    continue
                if tag == "numFmt" and in_num_fmts:
                    try:
                        custom[int(el.get("numFmtId", ""))] = el.get("formatCode") or ""
                    except ValueError:
                        continue
                elif tag == "xf" and in_cell_xfs:
                    try:
                        xf_formats.append(int(el.get("numFmtId") or 0))
                    except ValueError:
                        xf_formats.append(0)
                    if len(xf_formats) > MAX_STYLES:
                        raise InvalidWorkbookError(TOO_LARGE.format(mb=_mb()))
        dates: set[int] = set()
        timedeltas: set[int] = set()
        for index, fmt_id in enumerate(xf_formats):
            code = custom.get(fmt_id) or builtin_format_code(fmt_id)
            if is_date_format(code):
                dates.add(index)
            if is_timedelta_format(code):
                timedeltas.add(index)
        return dates, timedeltas

    # ------------------------------------------------------------ uma aba

    def _read_sheet(self, ref: _SheetRef) -> SheetData:
        sheet = SheetData(name=ref.name, state=ref.state)
        if ref.part is None or ref.part not in self._names:
            return sheet  # aba sem conteúdo (alvo ausente): fica vazia
        try:
            with self._open(ref.part) as src:
                _SheetParser(self, sheet).parse(src)
        except _READ_ERRORS:
            raise InvalidWorkbookError(INVALID_FILE) from None
        sheet.values = dict(sorted(sheet.values.items()))
        sheet.merges.sort(key=lambda r: (r.min_row, r.min_col))
        return sheet


def open_xlsx(source: bytes) -> XlsxFile:
    return XlsxFile(source)


# ---------------------------------------------------------------- parser de uma aba


class _SheetParser:
    def __init__(self, book: XlsxFile, sheet: SheetData) -> None:
        self.book = book
        self.sheet = sheet
        self.row = 0
        self.col: int | None = 0  # None: coluna da última célula ainda não calculada
        self.last_ref = ""
        self.row_hidden = False

    def parse(self, src) -> None:
        # Mesmo descarte de _walk, mas sem gerador: este laço passa por cada <c> da aba
        # (inclusive as vazias só com formatação), então cada operação conta.
        sheet = self.sheet
        cell: _Cell | None = None
        texts = _TextCollector()
        stack: list = []
        local = _local
        for event, el in iterparse(src, events=("start", "end")):
            if event == "start":
                stack.append(el)
                tag = local(el.tag)
                if tag == "c":
                    cell = self._start_cell(el)
                    texts.reset()
                elif tag == "row":
                    self._start_row(el)
                elif cell is not None:
                    texts.start(tag)
                continue
            stack.pop()
            tag = local(el.tag)
            if cell is not None:
                if tag == "c":
                    self._store(cell, texts)
                    cell = None
                elif tag == "v":
                    cell.v = (el.text or "")[:MAX_TEXT]
                elif tag == "f":
                    cell.formula = True
                else:
                    texts.end(tag, el)
            elif tag == "col":
                self._column(el)
            elif tag == "mergeCell":
                self._merge(el.get("ref") or "")
            if stack:
                parent = stack[-1]
                if len(parent) and parent[-1] is el:
                    del parent[-1]
            el.clear()  # sem isto o TreeBuilder em C ainda segura o elemento
            if sheet.problem is not None:
                return

    def _start_row(self, el) -> None:
        raw = el.get("r")
        try:
            self.row = int(float(raw)) if raw else self.row + 1
        except ValueError:
            self.row += 1
        self.col = 0
        self.row_hidden = el.get("hidden") in ("1", "true")

    def _start_cell(self, el) -> _Cell:
        # A coordenada só é calculada para células com valor (a maioria das <c> de uma aba
        # formatada é vazia).
        ref = el.get("r")
        if ref:
            self.col = None
            self.last_ref = ref
            return _Cell(ref, 0, 0, el.get("t") or "n", el.get("s"))
        if self.col is None:
            self.col = _coordinate(self.last_ref)[1]
        self.col += 1
        return _Cell(None, self.row, self.col, el.get("t") or "n", el.get("s"))

    def _store(self, cell: _Cell, texts: _TextCollector) -> None:
        value = self._value(cell, texts)
        if value is None and not cell.formula:
            return
        if cell.ref is not None:
            cell.row, cell.col = _coordinate(cell.ref)
        if not (1 <= cell.row <= _MAX_ROW and 1 <= cell.col <= _MAX_COL):
            return
        if cell.formula and value is None:
            refs = self.sheet.formulas_without_value
            if len(refs) < 1000:
                refs.append(f"{_letter(cell.col)}{cell.row}")
        if value is None or (isinstance(value, str) and not value.strip()):
            return
        sheet = self.sheet
        sheet.values[(cell.row, cell.col)] = value
        if self.row_hidden:
            sheet.hidden_rows.add(cell.row)
        if len(sheet.values) > MAX_VALUES_PER_SHEET:
            sheet.problem = (
                f"A aba tem mais de {MAX_VALUES_PER_SHEET:,} células preenchidas e não parece "
                "uma agenda diária; ela foi ignorada."
            ).replace(",", ".")

    def _value(self, cell: _Cell, texts: _TextCollector) -> object:
        kind, raw = cell.kind, cell.v
        if kind == "inlineStr":
            return texts.value() if texts.seen else None
        if raw is None or raw == "":
            return None
        try:
            if kind == "s":
                strings = self.book._strings
                index = int(raw)
                return strings[index] if 0 <= index < len(strings) else None
            if kind == "str":
                return raw
            if kind == "b":
                return bool(int(raw))
            if kind == "e":
                return None  # #N/D, #REF!... não são texto de reserva
            if kind == "d":
                return from_ISO8601(raw)
            number = float(raw) if any(ch in raw for ch in ".eE") else int(raw)
            style = int(cell.style) if cell.style else 0
            if style in self.book._dates:
                return from_excel(
                    number, self.book.epoch, timedelta=style in self.book._timedeltas
                )
            return number
        except (ValueError, OverflowError, TypeError):
            return None

    def _column(self, el) -> None:
        if el.get("hidden") not in ("1", "true"):
            return
        try:
            first, last = int(el.get("min", "0")), int(el.get("max", "0"))
        except ValueError:
            return
        first, last = max(1, first), min(_MAX_COL, last)
        self.sheet.hidden_cols.update(range(first, last + 1))

    def _merge(self, ref: str) -> None:
        try:
            min_col, min_row, max_col, max_row = range_boundaries(ref)
        except (ValueError, TypeError):
            return
        if None in (min_col, min_row, max_col, max_row):
            return
        if not (1 <= min_row <= max_row <= _MAX_ROW and 1 <= min_col <= max_col <= _MAX_COL):
            return
        if (min_row, min_col) == (max_row, max_col):
            return
        merges = self.sheet.merges
        merges.append(
            CellRange(min_col=min_col, min_row=min_row, max_col=max_col, max_row=max_row)
        )
        if len(merges) > MAX_MERGES_PER_SHEET:
            self.sheet.problem = (
                f"A aba tem mais de {MAX_MERGES_PER_SHEET:,} células mescladas e não parece "
                "uma agenda diária; ela foi ignorada."
            ).replace(",", ".")


class _Cell:
    __slots__ = ("ref", "row", "col", "kind", "style", "v", "formula")

    def __init__(self, ref: str | None, row: int, col: int, kind: str, style: str | None) -> None:
        self.ref = ref
        self.row = row
        self.col = col
        self.kind = kind
        self.style = style
        self.v: str | None = None
        self.formula = False


def _coordinate(ref: str) -> tuple[int, int]:
    try:
        return coordinate_to_tuple(ref)
    except (ValueError, TypeError):
        return 0, 0


class _TextCollector:
    """Texto de <si>/<is>: junta os <t> (inclusive de trechos formatados <r>), sem a parte
    fonética (<rPh>), como o openpyxl."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.phonetic = 0
        self.seen = False

    def start(self, tag: str) -> None:
        if tag == "rPh":
            self.phonetic += 1
        elif tag == "is":
            self.seen = True

    def end(self, tag: str, el) -> None:
        if tag == "rPh":
            self.phonetic = max(0, self.phonetic - 1)
        elif tag == "t" and not self.phonetic and el.text and self.size < MAX_TEXT:
            text = el.text[: MAX_TEXT - self.size]
            self.parts.append(text)
            self.size += len(text)

    def value(self) -> str:
        return "".join(self.parts)


# ---------------------------------------------------------------- utilitários


def _check_sizes(infos: list[zipfile.ZipInfo]) -> None:
    total = 0
    for info in infos:
        total += info.file_size
        if (
            info.file_size > RATIO_CHECK_MIN_BYTES
            and info.file_size > MAX_COMPRESSION_RATIO * max(1, info.compress_size)
        ):
            raise InvalidWorkbookError(SUSPICIOUS.format(name=info.filename))
    if total > MAX_UNCOMPRESSED_BYTES:
        raise InvalidWorkbookError(TOO_LARGE.format(mb=_mb()))


def _mb() -> int:
    return MAX_UNCOMPRESSED_BYTES // (1024 * 1024)


_READ_ERRORS = (
    ParseError,
    KeyError,
    ValueError,
    IndexError,
    zipfile.BadZipFile,
    zlib.error,
    OSError,
    EOFError,
    RuntimeError,  # zip com senha
    NotImplementedError,  # método de compressão não suportado
)


def _walk(src, *, starts: bool = False):
    """`iterparse` que descarta cada elemento logo depois de entregá-lo no evento "end": só a
    trilha de elementos abertos (ex.: worksheet > sheetData > row > c) fica em memória, por
    maior que seja a parte XML."""
    stack: list = []
    for event, el in iterparse(src, events=("start", "end")):
        if event == "start":
            stack.append(el)
            if starts:
                yield event, el
            continue
        stack.pop()
        yield event, el
        if stack:
            parent = stack[-1]
            if len(parent) and parent[-1] is el:
                del parent[-1]
        el.clear()


_LOCAL_NAMES: dict[str, str] = {}


def _local(tag: str) -> str:
    """Nome sem namespace ("{...main}c" -> "c"; vale também para o OOXML "strict")."""
    name = _LOCAL_NAMES.get(tag)
    if name is None:
        name = tag.rsplit("}", 1)[-1] if "}" in tag else tag
        if len(_LOCAL_NAMES) < 1000:
            _LOCAL_NAMES[tag] = name
    return name


def _rels_path(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", f"{name}.rels")


def _first(rels: dict[str, tuple[str, str]], kind: str) -> str | None:
    return next((path for k, path in rels.values() if k == kind), None)


def _letter(col: int) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
