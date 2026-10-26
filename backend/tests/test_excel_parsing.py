"""Heurísticas puras da importação: horários, datas das abas, salas e texto das células."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from app.excel.parsing import (
    DateGuess,
    RoomMatcher,
    RoomRef,
    date_from_cell,
    find_time_range,
    is_badge_line,
    is_free_slot_marker,
    looks_like_room_header,
    parse_cell_text,
    parse_date_text,
    parse_time_value,
    resolve_date,
    room_key,
    split_cell_parts,
)
from app.models import PeriodType

TODAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def _clean_state():
    """Testes puros: substitui (só neste módulo) a limpeza do banco feita antes de cada teste."""
    yield


# ---------------------------------------------------------------- horários


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("08:00", (time(8), None)),
        ("8:00", (time(8), None)),
        ("8h", (time(8), None)),
        ("8H", (time(8), None)),
        ("8h30", (time(8, 30), None)),
        ("08h30", (time(8, 30), None)),
        ("8hs", (time(8), None)),
        ("8h30min", (time(8, 30), None)),
        ("8.30", (time(8, 30), None)),
        ("08:00:00", (time(8), None)),
        (" 13:00h ", (time(13), None)),
        ("08:00 - 08:30", (time(8), time(8, 30))),
        ("8:00-8:30", (time(8), time(8, 30))),
        ("08:00 às 08:30", (time(8), time(8, 30))),
        ("08:00 as 08:30", (time(8), time(8, 30))),
        ("das 8h às 9h", (time(8), time(9))),
        ("8h – 8h30", (time(8), time(8, 30))),
        (time(14, 30), (time(14, 30), None)),
        (time(8, 29, 59, 999_000), (time(8, 30), None)),  # arredondamento de float do Excel
        (datetime(1899, 12, 30, 9, 0), (time(9), None)),
        (datetime(2026, 10, 26, 9, 0), (time(9), None)),
        (0.375, (time(9), None)),
        (1 / 3, (time(8), None)),
        (timedelta(hours=10, minutes=30), (time(10, 30), None)),
    ],
)
def test_parse_time_value_accepts_legacy_formats(value, expected):
    assert parse_time_value(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "Almoço",
        "12",
        "Sala 08",
        "26/10",
        "8",
        8,
        0,
        1.5,
        True,
        datetime(2026, 10, 26),
        date(2026, 10, 26),
        "25:00",
        "08:00 Reunião",
    ],
)
def test_parse_time_value_rejects_non_times(value):
    assert parse_time_value(value) is None


def test_range_with_end_before_start_keeps_only_start():
    assert parse_time_value("09:00 - 08:00") == (time(9), None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Carla 14h15 às 15h45 - Diretoria", (time(14, 15), time(15, 45))),
        ("14:15–15:45", (time(14, 15), time(15, 45))),
        ("das 9h às 10h30 Treinamento", (time(9), time(10, 30))),
        ("Reunião 26/10 - 27/10", None),
        ("Ramal 3333-4444", None),
        ("Somente 14h", None),
    ],
)
def test_find_time_range(text, expected):
    found = find_time_range(text)
    assert (found[0] if found else None) == expected


@pytest.mark.parametrize(
    "word", ["Tarde", "Qualidade", "Saúde", "Vendas", "Sede", "Unidade", "Cidade", "Grande"]
)
def test_time_range_does_not_eat_words_ending_in_de_da(word):
    """O prefixo "de/da/das" da faixa ("das 8h às 9h") precisa ser uma palavra própria."""
    parsed = parse_cell_text(f"Ana - {word} 14h às 15h")
    assert parsed.explicit == (time(14), time(15))
    assert (parsed.requester, parsed.title) == ("Ana", word)
    text = f"Reunião {word}\n14:15–15:45"
    (span, (start, _end)) = find_time_range(text)
    assert span == (time(14, 15), time(15, 45))
    assert text[:start].rstrip() == f"Reunião {word}"  # a faixa não atravessa a quebra de linha


def test_time_range_prefix_as_its_own_word_is_still_absorbed():
    text = "Reunião de 14h15 às 15h45"
    span, (start, _end) = find_time_range(text)
    assert span == (time(14, 15), time(15, 45)) and text[:start] == "Reunião "
    assert parse_cell_text("Ana das 9h às 10h - RH").explicit == (time(9), time(10))


# ---------------------------------------------------------------- datas


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("26-10", date(2026, 10, 26)),
        ("26.10", date(2026, 10, 26)),
        ("26/10", date(2026, 10, 26)),
        ("26_10", date(2026, 10, 26)),
        ("26-10-2026", date(2026, 10, 26)),
        ("26.10.26", date(2026, 10, 26)),
        ("Seg 26-10", date(2026, 10, 26)),
        ("SEG - 26.10", date(2026, 10, 26)),
        ("segunda 26.10.26", date(2026, 10, 26)),
        ("Segunda-feira 26-10", date(2026, 10, 26)),
        ("Terça 27/10/2026", date(2026, 10, 27)),
        ("2026-10-28", date(2026, 10, 28)),
        ("29 out", date(2026, 10, 29)),
        ("30 de outubro de 2026", date(2026, 10, 30)),
        ("Sex 30-out", date(2026, 10, 30)),
        ("02 nov", date(2026, 11, 2)),
        ("Qui 29.10 (2)", date(2026, 10, 29)),
        ("1-9", date(2026, 9, 1)),
    ],
)
def test_sheet_name_dates(name, expected):
    guess = parse_date_text(name)
    assert guess is not None
    assert resolve_date(guess, TODAY) == (expected, None)


@pytest.mark.parametrize("name", ["Resumo", "Plan1", "Semana 43", "LEIA-ME", "31-02", "Salas"])
def test_names_without_dates(name):
    assert parse_date_text(name) is None


def test_missing_year_picks_the_closest_date_to_today():
    december = date(2026, 12, 28)
    assert resolve_date(parse_date_text("04-01"), december)[0] == date(2027, 1, 4)
    assert resolve_date(parse_date_text("20-12"), december)[0] == date(2026, 12, 20)
    january = date(2027, 1, 3)
    assert resolve_date(parse_date_text("28-12"), january)[0] == date(2026, 12, 28)


def test_missing_year_prefers_the_year_matching_the_weekday():
    # 05/07: 2026 é domingo (182 dias antes), 2027 é segunda (183 dias depois).
    today = date(2027, 1, 3)
    assert resolve_date(parse_date_text("05-07"), today)[0] == date(2026, 7, 5)
    assert resolve_date(parse_date_text("Seg 05-07"), today)[0] == date(2027, 7, 5)
    # longe demais de hoje o dia da semana não puxa a data para outro ano (vira aviso)
    resolved, warning = resolve_date(parse_date_text("Ter 26-10"), TODAY)
    assert resolved == date(2026, 10, 26) and "não confere" in warning


def test_feb_29_without_year_outside_leap_years_is_not_a_date():
    resolved, warning = resolve_date(parse_date_text("29-02"), date(2026, 10, 26))
    assert resolved is None
    assert warning.startswith("29/02 sem ano não existe em 2025, 2026 nem 2027")
    assert resolve_date(parse_date_text("29-02"), date(2027, 6, 1)) == (date(2028, 2, 29), None)


def test_weekday_mismatch_is_reported():
    resolved, warning = resolve_date(parse_date_text("Ter 26-10-2026"), TODAY)
    assert resolved == date(2026, 10, 26)
    assert "terça-feira" in warning and "segunda-feira" in warning


def test_date_from_top_cell():
    assert date_from_cell(datetime(2026, 10, 27)) == DateGuess(27, 10, 2026, has_context=True)
    assert date_from_cell(date(2026, 10, 27)).year == 2026
    assert date_from_cell("AGENDA DE SALAS - SEGUNDA-FEIRA 26/10/2026").year == 2026
    assert date_from_cell("Segunda, 26/10").weekday == 0
    assert date_from_cell("26 de outubro").month == 10
    # sem contexto (ano, mês por extenso ou dia da semana) um "9.10" pode ser horário
    assert date_from_cell("9.10") is None
    assert date_from_cell(datetime(1899, 12, 30, 8)) is None
    assert date_from_cell(42) is None


# ---------------------------------------------------------------- salas

ROOMS = [
    RoomRef(1, "Sala 01 - Executiva"),
    RoomRef(2, "Sala 02 - Diretoria"),
    RoomRef(3, "Sala 05 - Reunião"),
    RoomRef(4, "Sala 06 - Reunião"),
    RoomRef(5, "Auditório"),
    RoomRef(6, "Sala 10"),
]


def test_room_key_ignores_case_accents_spacing_prefix_and_leading_zeros():
    assert room_key("Sala 01 - Executiva") == "1 executiva"
    assert room_key("  SALA 1 –  EXECUTIVA ") == "1 executiva"
    assert room_key("01 - Executiva") == "1 executiva"
    assert room_key("Auditório") == room_key("AUDITORIO") == "auditorio"
    assert room_key("Sala") == "sala"


@pytest.mark.parametrize(
    ("header", "room_id", "fuzzy"),
    [
        ("Sala 01 - Executiva", 1, False),
        ("SALA 01 - EXECUTIVA", 1, False),
        ("sala 1 – executiva", 1, False),
        ("01 - Executiva", 1, False),
        ("Sala 01 -   Executiva ", 1, False),
        ("Sala 01 - Executiva\n12 lugares", 1, False),
        ("Sala 02 - Diretoria (10 pessoas)", 2, False),
        ("Sala 02 - Diretoria\n(vídeo em manutenção — chamado 55)", 2, False),
        ("Sala 05 - Reuniao", 3, False),
        ("auditorio", 5, False),
        ("Sala 01", 1, True),
        ("Diretoria", 2, True),
        ("Sala 10 - Entrevistas", 6, True),
        ("Reunião", None, False),  # ambíguo: duas salas "Reunião"
        ("Sala 1 - Copa", None, False),  # mesmo número, nome diferente: não associa
        ("Sala 99", None, False),
        ("Horário", None, False),
    ],
)
def test_room_matcher(header, room_id, fuzzy):
    room, is_fuzzy = RoomMatcher(ROOMS).match(header)
    assert (room.id if room else None, is_fuzzy) == (room_id, fuzzy)


def test_room_matcher_prefers_exact_name_then_active_rooms():
    """Sala desativada com o mesmo nome normalizado não "rouba" o cabeçalho da ativa."""
    old = RoomRef(10, "Sala 5 - Reunião", is_active=False)
    new = RoomRef(11, "Sala 05 - Reunião")
    for rooms in ([old, new], [new, old]):
        matcher = RoomMatcher(rooms)
        assert matcher.match("Sala 05 - Reunião") == (new, False)
        assert matcher.match("SALA 05 – REUNIAO") == (new, False)
        assert matcher.match("05 - Reunião") == (new, False)
        assert matcher.match("Sala 05") == (new, True)
        assert matcher.match("Sala 5 - Reunião") == (old, False)  # nome exato da antiga
        assert matcher.ambiguous(new) == []
    twins = RoomMatcher([RoomRef(1, "Sala 5 - Reunião"), RoomRef(2, "Sala 05 - Reunião")])
    room, fuzzy = twins.match("05 - Reunião")
    assert not fuzzy and [r.id for r in twins.ambiguous(room)] == [2 if room.id == 1 else 1]
    assert twins.is_exact_name("SALA 05 - REUNIÃO\n6 lugares", RoomRef(2, "Sala 05 - Reunião"))


def test_free_slot_markers():
    for text in ("LIVRE", "Livre!", "vago", "Disponível", "-", "—", "...", "***", "--"):
        assert is_free_slot_marker(text), text
    for text in ("Carla", "X", "Livre docência - Prof. Ana", "48211"):
        assert not is_free_slot_marker(text), text


def test_looks_like_room_header():
    assert looks_like_room_header("Sala 99 - Nova")
    assert looks_like_room_header("Copa")
    assert looks_like_room_header("x" * 61)  # cabeçalho longo: listado (cortado), não some
    for text in (
        "Observações",
        "OBS:",
        "Horário",
        "HORA",
        "Videoconferência",
        "Salas Normais",
        "08:00",
        "Seg 26/10/2026",
    ):
        assert not looks_like_room_header(text), text


# ---------------------------------------------------------------- texto das células


@pytest.mark.parametrize(
    ("text", "ticket"),
    [
        ("#48211 Carla", "48211"),
        ("# 48211 - Carla", "48211"),
        ("Chamado 48211 - Carla", "48211"),
        ("Chamado: 48211 Carla", "48211"),
        ("Chamado nº 48211 Carla", "48211"),
        ("chamado #48211 Carla", "48211"),
        ("CH-48211 Carla", "48211"),
        ("Ch. 48211 Carla", "48211"),
        ("Ticket 48211 Carla", "48211"),
        ("INC0012345 Carla", "INC0012345"),
        ("inc0012345 Carla", "INC0012345"),
        ("RITM0045678 - Carla", "RITM0045678"),
        ("Carla (Chamado 48211)", "48211"),
        ("Carla 48211", "48211"),
        ("48211", "48211"),
        ("Chamado ABC-1234\nCarla", "ABC-1234"),
        ("Carla 2026", None),  # parece ano
        ("Carla - 26/10/2026", None),
        ("Carla Ramal 4321", None),
        ("Carla Tel 99999-1234", None),
        ("Carla 123", None),  # curto demais sem marcador
        ("Carla #123", "123"),
    ],
)
def test_ticket_extraction(text, ticket):
    assert parse_cell_text(text).ticket == ticket


@pytest.mark.parametrize(
    ("text", "requester", "title", "notes"),
    [
        ("Carla Mendes", "Carla Mendes", None, None),
        ("Carla Mendes - Diretoria Financeira", "Carla Mendes", "Diretoria Financeira", None),
        ("Carla Mendes - RH - Entrevista", "Carla Mendes", "RH - Entrevista", None),
        ("Carla Mendes | Diretoria", "Carla Mendes", "Diretoria", None),
        ("Carla Mendes / Diretoria", "Carla Mendes", "Diretoria", None),
        ("Carla Mendes–Diretoria", "Carla Mendes", "Diretoria", None),
        ("Ana-Lúcia Prado", "Ana-Lúcia Prado", None, None),  # hífen sem espaço não separa
        ("Carla Mendes\nDiretoria\nCafé para 8", "Carla Mendes", "Diretoria", "Café para 8"),
        ("#48211 - Carla Mendes - Diretoria", "Carla Mendes", "Diretoria", None),
        ("Chamado 48211\n\nCarla Mendes\nDiretoria", "Carla Mendes", "Diretoria", None),
        ("48211", None, None, None),
    ],
)
def test_requester_title_and_notes(text, requester, title, notes):
    parsed = parse_cell_text(text)
    assert (parsed.requester, parsed.title, parsed.notes) == (requester, title, notes)


@pytest.mark.parametrize(
    ("text", "video"),
    [
        ("Carla - Diretoria (vídeo)", True),
        ("Carla - Diretoria video", True),
        ("Carla - Videoconferência", True),
        ("Carla - Teams", True),
        ("Carla - ZOOM", True),
        ("Carla - Google Meet", True),
        ("Carla - VC", True),
        ("Carla - vc vem?", False),  # "vc" minúsculo = "você"
        ("Carla - Meeting", False),
        ("Carla - Diretoria", False),
        ("Carla [Videoconferência]", True),
        ("Carla Teams [Presencial — remanejável]", False),  # selo explícito vence o indício
    ],
)
def test_video_hints(text, video):
    assert parse_cell_text(text).requires_video is video


def test_attendees_from_text_and_badges():
    parsed = parse_cell_text("Carla - Diretoria (8 pessoas)")
    assert (parsed.attendees, parsed.title) == (8, "Diretoria")
    assert parse_cell_text("Carla\n[12 participantes]").attendees == 12
    assert parse_cell_text("Carla 20 pax").attendees == 20
    assert parse_cell_text("Carla").attendees is None


def test_badges_are_removed_and_unknown_brackets_kept():
    parsed = parse_cell_text("Carla\nDiretoria\n[Dia inteiro] [Check-in] [Não compareceu]")
    assert (parsed.requester, parsed.title, parsed.notes) == ("Carla", "Diretoria", None)
    assert parse_cell_text("[RH] Carla").requester == "[RH] Carla"


def test_explicit_time_range_is_extracted():
    parsed = parse_cell_text("Carla 14h15 às 15h45 - Diretoria")
    assert parsed.explicit == (time(14, 15), time(15, 45))
    assert (parsed.requester, parsed.title) == ("Carla", "Diretoria")


def test_export_mode_uses_fixed_lines_and_badges_only():
    text = "Chamado 48211\nRH - Recrutamento\nEntrevista Teams\n[Presencial — remanejável]"
    parsed = parse_cell_text(text, export_mode=True)
    assert parsed.ticket == "48211"
    assert (parsed.requester, parsed.title) == ("RH - Recrutamento", "Entrevista Teams")
    assert parsed.requires_video is False
    # sem selo de vídeo, "Teams" no assunto não liga o vídeo; e números soltos não viram chamado
    no_badge = parse_cell_text("Carla\nTreinamento Teams 48211", export_mode=True)
    assert (no_badge.requires_video, no_badge.ticket) == (False, None)
    with_badge = parse_cell_text("Carla\nDiretoria\n[Videoconferência]", export_mode=True)
    assert with_badge.requires_video is True
    assert parse_cell_text("Carla\n[⚠ Precisa de vídeo]", export_mode=True).requires_video


def test_export_mode_reads_lines_by_position():
    """Exportação lida por posição: nada de heurística da planilha legada nos textos livres."""

    def parse(text):
        p = parse_cell_text(text, export_mode=True)
        return p.ticket, p.requester, p.title, p.notes, p.attendees, p.explicit, p.period

    assert parse("Chamado a definir\nAna\nRevisão 14h às 15h (8 pessoas) #4567") == (
        "a definir",
        "Ana",
        "Revisão 14h às 15h (8 pessoas) #4567",
        None,
        None,
        None,
        PeriodType.CUSTOM,
    )
    assert parse("Chamado INC 0012345\n-") == ("INC 0012345", "-", None, None, None, None, "CUSTOM")
    assert parse("Chamado —\nChamado Fulano\n09:05–09:50")[:3] == (None, "Chamado Fulano", None)
    assert parse("Ana\n14:00–15:00\n10:00–11:00")[2:6] == (
        "14:00–15:00",
        None,
        None,
        (time(10), time(11)),
    )
    assert parse("Ana\n[Check-in]\n09:00–10:00")[2] == "[Check-in]"
    assert parse("Ana  Lima\nRH\nnota\n[10000 participantes]")[1:5] == (
        "Ana  Lima",
        "RH",
        "nota",
        10_000,
    )
    full = parse_cell_text("Ana\n[Manhã toda] [Videoconferência]", export_mode=True)
    assert (full.period, full.requires_video, full.title) == (PeriodType.MORNING, True, None)
    assert parse_cell_text("Ana\n[Dia inteiro]", export_mode=True).period == PeriodType.FULL_DAY


def test_badge_lines():
    assert is_badge_line("[Dia inteiro] [Videoconferência] [3 participantes] [Check-in]")
    assert is_badge_line("[Presencial — remanejável]")
    for line in ("[RH]", "[Dia inteiro] extra", "Ana", "[Check-in] [RH]"):
        assert not is_badge_line(line), line


def test_split_cell_parts():
    assert split_cell_parts("A\n14:00–14:15\n———\nB\n14:15–14:30") == [
        "A\n14:00–14:15",
        "B\n14:15–14:30",
    ]
    assert split_cell_parts("Carla - Diretoria") == ["Carla - Diretoria"]
    assert split_cell_parts("A\n---\nB") == ["A", "B"]
    # na exportação só a linha separadora exata divide (um assunto "---" é texto)
    assert split_cell_parts("A\n---\nB", export_mode=True) == ["A\n---\nB"]
    assert split_cell_parts("A\n———\nB", export_mode=True) == ["A", "B"]
