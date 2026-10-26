"""Leitura da planilha (sem banco): limites contra arquivos inflados e heurísticas de cabeçalho,
linhas/colunas ocultas, horários digitados errado e cabeçalhos mesclados."""

from __future__ import annotations

import io
import tracemalloc
import zipfile
from datetime import date

import pytest

from app.excel import reader, xlsx
from app.excel.parsing import RoomRef
from app.excel.reader import InvalidWorkbookError, read_workbook
from tests.excel_builders import MONDAY, TODAY, legacy, new_workbook, to_bytes

S01, S02, S05, S07 = (
    "Sala 01 - Executiva",
    "Sala 02 - Diretoria",
    "Sala 05 - Reunião",
    "Sala 07 - Atendimento",
)
ROOMS = [
    RoomRef(1, S01, is_video=True, capacity=12),
    RoomRef(2, S02, is_video=True, capacity=10),
    RoomRef(3, S05, capacity=6),
    RoomRef(4, S07, capacity=4),
]
ROOM_NAMES = {room.id: room.name for room in ROOMS}
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_state():
    """Testes puros: substitui (só neste módulo) a limpeza do banco feita antes de cada teste."""
    yield


def read(content: bytes, rooms=ROOMS, *, today: date = TODAY, include_past: bool = False):
    return read_workbook(content, rooms, today=today, include_past=include_past)


def sheet_plan(plan, name):
    return next(s for s in plan.sheets if s.name == name)


def bookings(sheet) -> list[tuple[str, str, str, str]]:
    return [
        (ROOM_NAMES[c.room.id], f"{c.start:%H:%M}", f"{c.end:%H:%M}", c.requester)
        for c in sheet.candidates
    ]


def rewrite(content: bytes, part: str, change) -> bytes:
    """Cópia do .xlsx com a parte XML `part` alterada por change(texto) -> texto."""
    src = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == part:
                data = change(data.decode("utf-8")).encode("utf-8")
            dst.writestr(info.filename, data)
    return out.getvalue()


def simple_day(**kw) -> bytes:
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", **kw)
    sheet.put(S01, "09:00", "10:00", "Carla - Diretoria", fill=None)
    sheet.put(S05, "14:00", None, "Pedro - RH", fill=None)
    return to_bytes(wb)


def peak_memory(fn) -> tuple[object, int]:
    tracemalloc.start()
    try:
        result = fn()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


# ---------------------------------------------------------------- limites (memória / zip bomb)


def test_huge_merged_ranges_are_never_expanded():
    """Cada célula de um intervalo mesclado virava um objeto (openpyxl): 40 bytes de XML como
    <mergeCell ref="AA1:XFD1048576"/> são 17 bilhões de células."""

    def with_merge(ref: str) -> bytes:
        # dentro do <mergeCells> que a aba já tem (a reserva das 09:00 é mesclada)
        return rewrite(
            simple_day(),
            "xl/worksheets/sheet1.xml",
            lambda xml: xml.replace("</mergeCells>", f'<mergeCell ref="{ref}"/></mergeCells>', 1),
        )

    moderate = with_merge("Z100:CV10099")  # 750 mil células
    plan, peak = peak_memory(lambda: read(moderate))
    assert peak < 16 * MB, f"pico de {peak / MB:.0f} MB"
    assert bookings(plan.sheets[0]) == [
        (S01, "09:00", "10:00", "Carla"),
        (S05, "14:00", "14:30", "Pedro"),
    ]
    # O ataque de verdade (só roda se a mesclagem acima não foi expandida).
    absurd = with_merge("AA1:XFD1048576")
    plan, peak = peak_memory(lambda: read(absurd))
    assert peak < 16 * MB
    assert len(plan.sheets[0].candidates) == 2


def test_formatted_empty_cells_do_not_cost_memory():
    """Bordas/preenchimento em faixas grandes geram um <c> por célula vazia; o openpyxl criava
    um objeto Cell (~400 bytes) para cada um. 200 mil células vazias aqui."""
    rows = "".join(
        f'<row r="{r}">' + "".join(f'<c r="{_col(c)}{r}" s="0"/>' for c in range(1, 101)) + "</row>"
        for r in range(40, 2040)
    )
    content = rewrite(
        simple_day(),
        "xl/worksheets/sheet1.xml",
        lambda xml: xml.replace("</sheetData>", rows + "</sheetData>", 1),
    )
    plan, peak = peak_memory(lambda: read(content))
    assert peak < 16 * MB, f"pico de {peak / MB:.0f} MB"
    assert len(plan.sheets[0].candidates) == 2


def test_zip_bomb_part_is_rejected_before_parsing():
    padding = " " * (8 * MB)  # 8 MB de espaços: alguns KB compactados
    content = rewrite(
        simple_day(),
        "xl/worksheets/sheet1.xml",
        lambda xml: xml.replace("<sheetData>", "<sheetData>" + padding, 1),
    )
    assert len(content) < 1 * MB
    with pytest.raises(InvalidWorkbookError, match="compactação fora do normal"):
        read(content)


def test_uncompressed_size_limit(monkeypatch):
    monkeypatch.setattr(xlsx, "MAX_UNCOMPRESSED_BYTES", 64 * 1024)
    content = rewrite(
        simple_day(),
        "xl/worksheets/sheet1.xml",
        lambda xml: xml.replace("<sheetData>", "<sheetData>" + " " * 100_000, 1),
    )
    with pytest.raises(InvalidWorkbookError, match="grande demais para importar"):
        read(content)


def test_sheets_over_the_cell_and_merge_limits_are_skipped(monkeypatch):
    monkeypatch.setattr(xlsx, "MAX_VALUES_PER_SHEET", 60)
    monkeypatch.setattr(xlsx, "MAX_MERGES_PER_SHEET", 3)
    wb = new_workbook()
    full = wb.create_sheet("Dados")
    for row in range(1, 62):
        full.cell(row, 1, f"linha {row}")
    merged = legacy(wb, "Ter 27-10")
    for hour in (9, 11, 13, 15):
        merged.put(S01, f"{hour:02d}:00", f"{hour + 1:02d}:00", "Carla")
    legacy(wb, "Seg 26-10").put(S01, "09:00", None, "Pedro")
    plan = read(to_bytes(wb))
    assert sheet_plan(plan, "Dados").status == "SKIPPED_UNRECOGNIZED"
    assert "mais de 60 células preenchidas" in sheet_plan(plan, "Dados").warnings[0]
    assert sheet_plan(plan, "Ter 27-10").status == "SKIPPED_UNRECOGNIZED"
    assert "mais de 3 células mescladas" in sheet_plan(plan, "Ter 27-10").warnings[0]
    assert bookings(sheet_plan(plan, "Seg 26-10")) == [(S01, "09:00", "09:30", "Pedro")]
    assert plan.errors == []


def test_too_many_bookings_in_one_file(monkeypatch):
    monkeypatch.setattr(reader, "MAX_CANDIDATES", 1)
    with pytest.raises(InvalidWorkbookError, match="mais de 1 reservas"):
        read(simple_day())


def test_corrupted_sheet_is_isolated():
    wb = new_workbook()
    legacy(wb, "Seg 26-10").put(S01, "09:00", None, "Carla")
    legacy(wb, "Ter 27-10").put(S01, "09:00", None, "Pedro")
    content = rewrite(
        to_bytes(wb), "xl/worksheets/sheet1.xml", lambda xml: xml[: len(xml) // 2]
    )
    plan = read(content)
    assert sheet_plan(plan, "Seg 26-10").status == "ERROR"
    assert bookings(sheet_plan(plan, "Ter 27-10")) == [(S01, "09:00", "09:30", "Pedro")]
    assert plan.errors == ['Aba "Seg 26-10": conteúdo corrompido, não foi possível ler.']


def test_streaming_reader_matches_cell_types():
    """Valores de hora do Excel, frações do dia, números e datas continuam reconhecidos."""
    wb = new_workbook()
    for fmt in ("time", "float", "datetime", "range"):
        name = {"time": "Seg 26-10", "float": "Ter 27-10", "datetime": "Qua 28-10"}.get(
            fmt, "Qui 29-10"
        )
        legacy(wb, name, time_format=fmt).put(S07, "18:30", None, "48211")
    plan = read(to_bytes(wb))
    assert [bookings(s) for s in plan.sheets] == [[(S07, "18:30", "19:00", "Não informado")]] * 4
    assert {c.ticket for s in plan.sheets for c in s.candidates} == {"48211"}


# ---------------------------------------------------------------- conteúdo oculto


def test_hidden_sheets_rows_and_columns_are_ignored():
    wb = new_workbook()
    stale = legacy(wb, "Seg 26-10 (antiga)")  # cópia antiga escondida, ANTES da visível
    stale.put(S01, "09:00", "10:00", "Reunião cancelada")
    stale.ws.sheet_state = "hidden"
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Carla - Diretoria", fill=None)
    sheet.put(S01, "11:00", None, "Linha escondida", fill=None)
    sheet.ws.row_dimensions[sheet.row("11:00")].hidden = True
    sheet.put(S02, "09:00", None, "Coluna escondida", fill=None)
    sheet.ws.column_dimensions["C"].hidden = True
    sheet.put(S05, "11:30", None, "Pedro - RH", fill=None)  # logo abaixo da linha oculta

    plan = read(to_bytes(wb))
    hidden = sheet_plan(plan, "Seg 26-10 (antiga)")
    assert hidden.status == "SKIPPED_UNRECOGNIZED" and hidden.candidates == []
    assert hidden.warnings[0].startswith("Aba oculta na planilha: ignorada.")
    visible = sheet_plan(plan, "Seg 26-10")
    assert bookings(visible) == [
        (S01, "09:00", "10:00", "Carla"),
        (S05, "11:30", "12:00", "Pedro"),
    ]
    assert (
        "3 célula(s) com texto em linhas ou colunas ocultas foram ignoradas: C2, C5, B9. "
        "Se valem, reexiba-as no Excel e importe de novo." in visible.warnings
    )


# ---------------------------------------------------------------- cabeçalho


def test_group_row_with_vertically_merged_room_header():
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["B1"] = "VIDEOCONFERÊNCIA"
    ws.merge_cells("B1:C1")
    ws["D1"] = S05  # sala normal com o nome mesclado sobre as duas linhas do cabeçalho
    ws.merge_cells("D1:D2")
    ws["A2"], ws["B2"], ws["C2"] = "Horário", S01, S02
    for i, label in enumerate(["08:00", "08:30", "09:00", "09:30"]):
        ws.cell(3 + i, 1, label)
    ws["B5"], ws["D5"] = "Carla", "Pedro"
    plan = read(to_bytes(wb))
    assert bookings(plan.sheets[0]) == [
        (S01, "09:00", "09:30", "Carla"),
        (S05, "09:00", "09:30", "Pedro"),
    ]
    assert plan.sheets[0].warnings == []


def test_smaller_stacked_table_uses_its_own_header():
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["A1"] = "Horário"
    for i, name in enumerate([S01, S02, S05, S07]):
        ws.cell(1, 2 + i, name)
    for i in range(8):  # 08:00 .. 11:30
        ws.cell(2 + i, 1, f"{8 + i // 2:02d}:{30 * (i % 2):02d}")
    ws["B3"] = "Carla"  # 08:30, sala 01
    ws["A11"], ws["B11"] = "Horário", S07  # tabela da tarde, só uma sala
    for i in range(4):  # 13:00 .. 14:30
        ws.cell(12 + i, 1, f"{13 + i // 2:02d}:{30 * (i % 2):02d}")
    ws["B13"] = "Pedro"  # 13:30 na sala 07 (antes: lida como sala 01)
    plan = read(to_bytes(wb))
    assert bookings(plan.sheets[0]) == [
        (S01, "08:30", "09:00", "Carla"),
        (S07, "13:30", "14:00", "Pedro"),
    ]


def test_cells_in_columns_without_header_are_reported():
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", [S01, S02])
    sheet.put(S01, "09:00", None, "Carla", fill=None)
    sheet.ws.cell(sheet.row("10:00"), 4, "Pedro - sem sala")  # coluna D sem cabeçalho
    sheet.ws.cell(sheet.row("10:00"), 5, "18:00")  # 2ª coluna de horários: não é reserva
    plan = read(to_bytes(wb))
    rep = plan.sheets[0]
    assert bookings(rep) == [(S01, "09:00", "09:30", "Carla")]
    assert rep.ignored == 1
    assert (
        "1 reserva(s) em colunas sem nome de sala no cabeçalho foram ignoradas: D7. Escreva o "
        "nome da sala no cabeçalho dessas colunas." in rep.warnings
    )


def test_long_unknown_header_is_listed_not_dropped():
    long_name = "Sala 12 - Espaço de convivência e treinamento do terceiro andar (bloco B)"
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", [S01, long_name])
    sheet.put(long_name, "09:00", None, "Carla", fill=None)
    rep = read(to_bytes(wb)).sheets[0]
    shown = long_name[:59] + "…"
    assert rep.unknown_rooms == [shown]
    assert rep.ignored == 1
    assert any(f'("{shown}"): sala não cadastrada' in w for w in rep.warnings)


def test_header_merged_over_two_sub_columns_is_one_booking():
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["A1"], ws["B1"], ws["D1"] = "Horário", S01, S05
    ws.merge_cells("B1:C1")  # sala 01: colunas "Responsável | Assunto"
    for i in range(8):
        ws.cell(2 + i, 1, f"{8 + i // 2:02d}:{30 * (i % 2):02d}")
    ws["B4"], ws["C4"] = "Carla Mendes", "Planejamento 48211"  # 09:00
    ws["B6"] = "Pedro"  # 10:00, só a primeira subcoluna
    ws["D4"] = "Bia"
    rep = read(to_bytes(wb)).sheets[0]
    got = [
        (ROOM_NAMES[c.room.id], f"{c.start:%H:%M}", c.requester, c.title, c.ticket)
        for c in rep.candidates
    ]
    assert got == [
        (S01, "09:00", "Carla Mendes", "Planejamento", "48211"),
        (S05, "09:00", "Bia", None, None),
        (S01, "10:00", "Pedro", None, None),
    ]
    assert rep.warnings == [
        'Coluna B:C ("Sala 01 - Executiva"): o cabeçalho cobre 2 colunas; textos lado a lado '
        "foram unidos numa reserva só: B4 + C4."
    ]


def test_active_room_wins_over_deactivated_room_with_the_same_key():
    rooms = [RoomRef(9, "Sala 5 - Reunião", is_active=False), *ROOMS]
    plan = read(simple_day(), rooms)
    assert bookings(plan.sheets[0])[1] == (S05, "14:00", "14:30", "Pedro")
    assert plan.sheets[0].warnings == []


def test_two_active_rooms_with_equivalent_names_are_reported():
    rooms = [*ROOMS, RoomRef(9, "Sala 5 - Reunião", capacity=6)]
    wb = new_workbook()
    legacy(wb, "Seg 26-10", [S01, "05 - Reunião"]).put("05 - Reunião", "09:00", None, "Pedro")
    rep = read(to_bytes(wb), rooms).sheets[0]
    assert len(rep.candidates) == 1
    assert any("mais de uma sala ativa tem nome equivalente" in w for w in rep.warnings)


# ---------------------------------------------------------------- coluna de horários e datas


def test_mistyped_time_label_does_not_discard_the_day():
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    typo_row = sheet.row("09:00")
    sheet.ws.cell(typo_row, 1).value = "19:00"  # digitado errado
    sheet.put(S01, "09:00", None, "Carla", fill=None)
    sheet.put(S05, "12:00", "13:00", "Pedro", fill=None)
    rep = read(to_bytes(wb)).sheets[0]
    assert bookings(rep) == [
        (S01, "09:00", "09:30", "Carla"),
        (S05, "12:00", "13:00", "Pedro"),
    ]
    assert rep.warnings == [
        f"Linha {typo_row}: horário 19:00 parece digitado errado (esperado 09:00); usado 09:00."
    ]


def test_repeated_time_label_is_still_reported_and_ignored_at_the_end():
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", end="10:00")
    sheet.ws.cell(sheet.row("10:00"), 1).value = "09:30"  # repetida depois do último horário
    rep = read(to_bytes(wb)).sheets[0]
    assert rep.warnings == [
        f"Linha {sheet.row('10:00')}: horário 09:30 fora de ordem na coluna de horários; "
        "linha ignorada."
    ]


def test_feb_29_tab_without_year_is_skipped_with_a_warning():
    wb = new_workbook()
    legacy(wb, "29-02").put(S01, "09:00", None, "Carla")
    legacy(wb, "Seg 26-10").put(S01, "09:00", None, "Pedro")
    plan = read(to_bytes(wb), today=date(2026, 10, 26))
    feb = sheet_plan(plan, "29-02")
    assert feb.status == "SKIPPED_UNRECOGNIZED"
    assert feb.warnings[0].startswith("Data inválida: 29/02 sem ano não existe em 2025, 2026")
    assert plan.errors == []
    assert sheet_plan(plan, "Seg 26-10").day == MONDAY


def test_formulas_without_cached_value_are_reported():
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", None, "Carla", fill=None)
    sheet.ws.cell(sheet.row("10:00"), sheet.col(S01)).value = "=B5"  # sem valor calculado
    rep = read(to_bytes(wb)).sheets[0]
    assert bookings(rep) == [(S01, "09:00", "09:30", "Carla")]
    assert any(
        w.startswith("1 célula(s) com fórmula sem valor calculado") and "B7" in w
        for w in rep.warnings
    )


def test_plain_reader_api_keeps_sheet_names():
    wb = new_workbook()
    legacy(wb, "Seg 26-10")
    legacy(wb, "Ter 27-10")
    assert reader.open_workbook(to_bytes(wb)).sheetnames == ["Seg 26-10", "Ter 27-10"]


def _col(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
