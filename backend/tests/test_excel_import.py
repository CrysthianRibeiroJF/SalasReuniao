"""Importação da planilha legada (contrato seção 8) contra o banco de testes."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from app.excel.importer import InvalidWorkbookError, import_workbook, import_workbook_or_raise
from app.models import Booking, BookingLog, LogAction, PeriodType, VideoStatus
from app.realtime.memory import MemoryLockStore
from app.realtime.registry import NullLockStore
from app.schemas import BookingCancel, BookingCreate, BookingUpdate
from app.services import bookings as bookings_service
from app.timeutil import to_local
from tests.excel_builders import (
    MONDAY,
    TIME_FORMATS,
    TODAY,
    active_bookings,
    legacy,
    new_workbook,
    sheet_report,
    t,
    to_bytes,
)

pytest_plugins = ("tests.excel_fixtures",)

S01, S02, S05, S07 = (
    "Sala 01 - Executiva",
    "Sala 02 - Diretoria",
    "Sala 05 - Reunião",
    "Sala 07 - Atendimento",
)


async def run(session, user, wb, *, dry_run=True, include_past=False, today=TODAY):
    return await import_workbook(
        session, to_bytes(wb), user=user, dry_run=dry_run, include_past=include_past, today=today
    )


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


def summary(bookings):
    return [
        (
            b.room.name,
            _local(b.start_time),
            _local(b.end_time),
            b.period_type,
            b.ticket_number,
            b.requester_name,
            b.title,
            b.requires_video,
        )
        for b in bookings
    ]


def _local(dt: datetime) -> str:
    return to_local(dt).strftime("%H:%M")


def spans(bookings) -> list[tuple[str, str, str]]:
    return [(b.room.name, _local(b.start_time), _local(b.end_time)) for b in bookings]


# ---------------------------------------------------------------- reservas e períodos


async def test_merged_cells_presets_and_last_row(session, admin, rooms, recording_bus):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:30", "Chamado 48211\nCarla Mendes\nDiretoria Financeira")
    sheet.put(S02, "08:00", "19:00", "48190 - Marcos Silva - Integração")
    sheet.put(S05, "08:00", "12:00", "Fernanda Costa - Planejamento")
    sheet.put(S05, "13:00", "19:00", "Gustavo Nogueira - Conselho")
    sheet.put(S07, "18:30", None, "Camila Torres - Última reunião")  # última linha: + passo

    report = await run(session, admin, wb)
    sheet_rep = sheet_report(report, "Seg 26-10")
    assert (sheet_rep.status, sheet_rep.date) == ("DRY_RUN", MONDAY)
    assert (sheet_rep.bookings_found, sheet_rep.bookings_imported, sheet_rep.bookings_skipped) == (
        5,
        5,
        0,
    )
    assert sheet_rep.warnings == []
    assert (report.total_found, report.total_imported, report.total_skipped) == (5, 5, 0)
    assert await count(session, Booking) == 0

    report = await run(session, admin, wb, dry_run=False)
    assert sheet_report(report, "Seg 26-10").status == "IMPORTED"
    assert report.total_imported == 5 and report.errors == []
    bookings = await active_bookings(session)
    assert summary(bookings) == [
        (S02, "08:00", "19:00", PeriodType.FULL_DAY, "48190", "Marcos Silva", "Integração", False),
        (S05, "08:00", "12:00", PeriodType.MORNING, None, "Fernanda Costa", "Planejamento", False),
        (
            S01,
            "09:00",
            "10:30",
            PeriodType.CUSTOM,
            "48211",
            "Carla Mendes",
            "Diretoria Financeira",
            False,
        ),
        (S05, "13:00", "19:00", PeriodType.AFTERNOON, None, "Gustavo Nogueira", "Conselho", False),
        (S07, "18:30", "19:00", PeriodType.CUSTOM, None, "Camila Torres", "Última reunião", False),
    ]
    assert [b.is_full_day for b in bookings] == [True, False, False, False, False]
    assert all(b.created_by == admin.id for b in bookings)

    logs = (await session.scalars(select(BookingLog))).all()
    assert len(logs) == 5
    assert {log.action for log in logs} == {LogAction.IMPORTED}
    assert {log.user_id for log in logs} == {admin.id}
    assert recording_bus.of_type("BOOKINGS_IMPORTED") == [{"dates": ["2026-10-26"], "count": 5}]


@pytest.mark.parametrize("time_format", TIME_FORMATS)
async def test_time_column_formats(session, admin, rooms, time_format):
    wb = new_workbook()
    sheet = legacy(wb, "26-10", time_format=time_format)
    sheet.put(S01, "09:00", "10:30", "Carla - Diretoria")
    sheet.put(S05, "18:30", None, "Pedro - Fechamento")
    report = await run(session, admin, wb, dry_run=False)
    assert report.total_imported == 2, report
    assert spans(await active_bookings(session)) == [
        (S01, "09:00", "10:30"),
        (S05, "18:30", "19:00"),
    ]


async def test_dry_run_writes_nothing(session, admin, rooms, recording_bus):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Carla - Diretoria")
    sheet.put(S05, "10:00", "11:00", "Pedro - RH")
    report = await run(session, admin, wb, dry_run=True)
    assert report.dry_run is True
    assert (report.total_found, report.total_imported, report.total_skipped) == (2, 2, 0)
    assert await count(session, Booking) == 0
    assert await count(session, BookingLog) == 0
    assert recording_bus.envelopes == []


async def test_reimport_is_idempotent(session, admin, rooms, recording_bus):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Chamado 48211 - Carla - Diretoria")
    sheet.put(S05, "08:00", "19:00", "Treinamento geral")  # sem chamado: compara o solicitante
    sheet.put(S07, "10:00", None, "48300")  # sem solicitante: "Não informado"
    first = await run(session, admin, wb, dry_run=False)
    assert first.total_imported == 3

    for dry_run in (True, False):
        again = await run(session, admin, wb, dry_run=dry_run)
        sheet_rep = sheet_report(again, "Seg 26-10")
        assert (
            sheet_rep.bookings_found,
            sheet_rep.bookings_imported,
            sheet_rep.bookings_skipped,
        ) == (3, 0, 3)
        assert any("3 reserva(s) já cadastrada(s) no sistema" in w for w in sheet_rep.warnings)
    assert await count(session, Booking) == 3
    assert len(recording_bus.of_type("BOOKINGS_IMPORTED")) == 1  # a reimportação não publica


# ---------------------------------------------------------------- cabeçalho e abas


async def test_header_variants_unknown_and_fuzzy_rooms(session, admin, rooms):
    wb = new_workbook()
    headers = [
        "SALA 01 - EXECUTIVA",
        "sala 02 –  diretoria",
        "Sala 5 - Reuniao",
        "Atendimento",
        "Sala 99 - Nova",
    ]
    sheet = legacy(wb, "Seg 26-10", headers)
    for i, header in enumerate(headers):
        sheet.put(header, f"{9 + i:02d}:00", None, f"Pessoa {i} - Assunto")

    report = await run(session, admin, wb)
    sheet_rep = sheet_report(report, "Seg 26-10")
    assert report.unknown_rooms == ["Sala 99 - Nova"]
    assert (sheet_rep.bookings_found, sheet_rep.bookings_imported, sheet_rep.bookings_skipped) == (
        5,
        4,
        1,
    )
    assert any(
        'Coluna E ("Atendimento") associada à sala "Sala 07 - Atendimento" por semelhança' in w
        for w in sheet_rep.warnings
    )
    assert any(
        'Coluna F ("Sala 99 - Nova"): sala não cadastrada — 1 reserva(s) ignorada(s): F13.' in w
        for w in sheet_rep.warnings
    )

    await run(session, admin, wb, dry_run=False)
    assert [b.room.name for b in await active_bookings(session)] == [S01, S02, S05, S07]


async def test_all_rooms_unknown_are_listed(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", ["Sala Azul", "Sala Verde"])
    sheet.put("Sala Azul", "09:00", None, "Carla")
    report = await run(session, admin, wb)
    assert report.unknown_rooms == ["Sala Azul", "Sala Verde"]
    assert sheet_report(report, "Seg 26-10").bookings_skipped == 1


async def test_free_slot_markers_and_auxiliary_columns_are_not_bookings(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", [S01, S05, "Observações"])
    sheet.put(S01, "08:00", "12:00", "LIVRE")
    sheet.put(S01, "13:00", None, "-")
    sheet.put(S05, "09:00", "10:00", "Carla - Diretoria")
    sheet.put(S05, "10:00", None, "vago")
    sheet.put("Observações", "09:00", None, "Café para 8 pessoas")
    report = await run(session, admin, wb)
    rep = sheet_report(report, "Seg 26-10")
    assert (rep.bookings_found, rep.bookings_imported) == (1, 1)
    assert report.unknown_rooms == []
    assert rep.warnings == [
        '3 célula(s) só com marcação de horário vago ("LIVRE", "-") ignorada(s): B3:B10, C7, B13.'
    ]


async def test_sheet_dates_from_names_and_top_cells(session, admin, rooms):
    wb = new_workbook()
    names = ["Seg 26-10", "27.10", "quarta 28-10-26", "Qui 29.10.2026", "2026-10-30", "02 nov"]
    for name in names:
        legacy(wb, name).put(S01, "09:00", None, "Carla")
    no_name = legacy(wb, "Plan7", header_row=3)
    no_name.ws["A1"] = datetime(2026, 11, 3)
    no_name.put(S01, "09:00", None, "Carla")
    titled = legacy(wb, "Planilha", header_row=3, title="AGENDA - QUARTA-FEIRA 04/11/2026")
    titled.put(S01, "09:00", None, "Carla")
    year_from_title = legacy(wb, "26-10", header_row=3, title="Segunda, 26/10/2025")
    year_from_title.put(S01, "09:00", None, "Carla")
    wb.create_sheet("Resumo")["A1"] = "Totais da semana"

    report = await run(session, admin, wb, include_past=True)
    got = {s.sheet_name: (s.date, s.status) for s in report.sheets}
    assert got == {
        "Seg 26-10": (date(2026, 10, 26), "DRY_RUN"),
        "27.10": (date(2026, 10, 27), "DRY_RUN"),
        "quarta 28-10-26": (date(2026, 10, 28), "DRY_RUN"),
        "Qui 29.10.2026": (date(2026, 10, 29), "DRY_RUN"),
        "2026-10-30": (date(2026, 10, 30), "DRY_RUN"),
        "02 nov": (date(2026, 11, 2), "DRY_RUN"),
        "Plan7": (date(2026, 11, 3), "DRY_RUN"),
        "Planilha": (date(2026, 11, 4), "DRY_RUN"),
        "26-10": (date(2025, 10, 26), "DRY_RUN"),  # ano do título, não o mais próximo de hoje
        "Resumo": (None, "SKIPPED_UNRECOGNIZED"),
    }
    assert "Data não identificada" in sheet_report(report, "Resumo").warnings[0]
    assert report.total_found == 9


async def test_conflicting_title_date_and_weekday_are_reported(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Ter 26-10", header_row=3, title="AGENDA 27/10/2026")
    sheet.put(S01, "09:00", None, "Carla")
    report = await run(session, admin, wb)
    (warning,) = sheet_report(report, "Ter 26-10").warnings
    assert warning.startswith("Data da aba:")
    assert "terça-feira" in warning and "A1 mostra 27/10/2026" in warning
    assert sheet_report(report, "Ter 26-10").date == MONDAY


async def test_past_sheets_are_skipped_unless_include_past(session, admin, rooms):
    wb = new_workbook()
    legacy(wb, "Qui 22-10").put(S01, "09:00", None, "Carla")
    legacy(wb, "Sex 23-10").put(S01, "09:00", None, "Hoje conta como futuro")

    report = await run(session, admin, wb)
    assert sheet_report(report, "Qui 22-10").status == "SKIPPED_PAST"
    assert sheet_report(report, "Qui 22-10").bookings_found == 0
    assert sheet_report(report, "Sex 23-10").status == "DRY_RUN"
    assert report.total_found == 1

    report = await run(session, admin, wb, include_past=True, dry_run=False)
    assert sheet_report(report, "Qui 22-10").status == "IMPORTED"
    assert report.total_imported == 2


async def test_sheet_without_time_column_or_header(session, admin, rooms):
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["A1"], ws["B1"] = "Sala 01 - Executiva", "Carla"
    ws = wb.create_sheet("Ter 27-10")
    ws["A2"], ws["A3"] = "08:00", "08:30"
    report = await run(session, admin, wb)
    assert sheet_report(report, "Seg 26-10").status == "SKIPPED_UNRECOGNIZED"
    assert "coluna de horários" in sheet_report(report, "Seg 26-10").warnings[0]
    assert sheet_report(report, "Ter 27-10").status == "SKIPPED_UNRECOGNIZED"
    assert "cabeçalho" in sheet_report(report, "Ter 27-10").warnings[0]


# ---------------------------------------------------------------- texto das células


async def test_ticket_requester_title_and_video_extraction(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "#48211 - Carla Mendes - Diretoria (Teams)")
    sheet.put(S01, "10:00", "11:00", "INC0012345 | João | Treinamento")
    sheet.put(S02, "09:00", "10:00", "CH-777777 Ana Souza / Compras VC")
    sheet.put(S05, "09:00", "10:00", "Chamado: 48300\nPedro Lima\nAlinhamento\nTrazer projetor")
    sheet.put(S07, "09:00", None, "Equipe de TI - Reunião interna (3 pessoas)")
    sheet.put(S07, "10:00", None, "48301")
    report = await run(session, admin, wb, dry_run=False)
    assert report.total_imported == 6

    got = {
        (b.room.name, _local(b.start_time)): (
            b.ticket_number,
            b.requester_name,
            b.title,
            b.notes,
            b.requires_video,
            b.attendees,
        )
        for b in await active_bookings(session)
    }
    assert got == {
        (S01, "09:00"): ("48211", "Carla Mendes", "Diretoria (Teams)", None, True, None),
        (S01, "10:00"): ("INC0012345", "João", "Treinamento", None, False, None),
        (S02, "09:00"): ("777777", "Ana Souza", "Compras VC", None, True, None),
        (S05, "09:00"): ("48300", "Pedro Lima", "Alinhamento", "Trazer projetor", False, None),
        (S07, "09:00"): (None, "Equipe de TI", "Reunião interna", None, False, 3),
        (S07, "10:00"): ("48301", "Não informado", None, None, False, None),
    }
    assert sheet_report(report, "Seg 26-10").warnings == [
        'Aba "Seg 26-10", E7 (Sala 07 - Atendimento, 10:00–10:30): solicitante não identificado; '
        'registrado como "Não informado".'
    ]


async def test_identical_text_in_consecutive_cells_is_one_booking(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S05, "14:00", "15:30", "João Pereira - RH", merge=False)  # 3 cópias
    sheet.put(S05, "16:00", None, "João Pereira - RH")  # depois de uma linha vazia
    sheet.put(S07, "09:00", "10:00", "Bia - Treino")  # duas mesclagens iguais seguidas
    sheet.put(S07, "10:00", "11:00", "Bia - Treino")
    sheet.put(S01, "09:00", "10:00", "Ana - Comitê")  # mesclagem + cópia logo abaixo
    sheet.put(S01, "10:00", None, "ana -  comitê")
    sheet.paint(S02, "08:00", "12:00")  # só cor: não é reserva
    sheet.put(S02, "13:00", "14:00", "Carlos", fill=None)
    sheet.paint(S02, "14:00", "15:00")  # cor logo abaixo não estende a reserva

    report = await run(session, admin, wb, dry_run=False)
    assert report.total_found == 6 and report.total_imported == 6
    assert spans(await active_bookings(session)) == [
        (S01, "09:00", "10:30"),
        (S07, "09:00", "10:00"),
        (S07, "10:00", "11:00"),
        (S02, "13:00", "14:00"),
        (S05, "14:00", "15:30"),
        (S05, "16:00", "16:30"),
    ]


async def test_explicit_time_written_in_the_cell(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "14:00", "16:00", "Carla 14h15 às 15h45 - Diretoria")
    sheet.put(S05, "09:00", None, "Pedro 9h às 10h - RH")  # além da linha: usa o escrito
    sheet.put(S07, "09:00", None, "Bia 16h às 17h - Visita")  # não bate: usa as linhas
    sheet.put(S02, "11:00", None, "Lia 11:07 - 11:20")  # fora do padrão de 5 min: pulada
    report = await run(session, admin, wb, dry_run=False)
    warnings = sheet_report(report, "Seg 26-10").warnings
    assert report.total_imported == 3
    assert spans(await active_bookings(session)) == [
        (S05, "09:00", "10:00"),
        (S07, "09:00", "09:30"),
        (S01, "14:15", "15:45"),
    ]
    assert any(
        "D5 (Sala 05 - Reunião, 09:00–10:00): horário escrito na célula (09:00–10:00) vai além" in w
        and "usado o horário escrito" in w
        for w in warnings
    )
    assert any("E5" in w and "usado o das linhas" in w for w in warnings)
    assert any("C9" in w and "5 minutos — pulada" in w for w in warnings)
    visit = next(b for b in await active_bookings(session) if b.room.name == S07)
    assert visit.notes == "Horário escrito na planilha: 16:00–17:00"


# ---------------------------------------------------------------- conflitos


async def test_conflicts_inside_the_file_and_with_existing_bookings(session, admin, rooms):
    await bookings_service.create_booking(
        session,
        BookingCreate(
            room_id=rooms[S01].id,
            date=MONDAY,
            start=t("09:00"),
            end=t("10:00"),
            ticket_number="1111",
            requester_name="Carla Existente",
        ),
        admin,
        publish_events=False,
    )
    wb = new_workbook()
    first = legacy(wb, "Seg 26-10")
    first.put(S01, "09:30", "10:30", "Pedro - Conflita com o banco")
    first.put(S05, "14:00", "15:00", "Chamado 48230 - João - RH")
    first.put(S07, "09:30", "10:30", "Paulo - Visita")
    second = legacy(wb, "Seg 26-10 (2)")
    second.put(S05, "14:00", "15:00", "Chamado 48230 - João - RH")  # repetida
    second.put(S07, "09:00", "10:00", "Maria - Treino")  # conflita com Paulo (aba 1)

    for dry_run in (True, False):
        report = await run(session, admin, wb, dry_run=dry_run)
        a, b = sheet_report(report, "Seg 26-10"), sheet_report(report, "Seg 26-10 (2)")
        assert (a.bookings_found, a.bookings_imported, a.bookings_skipped) == (3, 2, 1)
        assert (b.bookings_found, b.bookings_imported, b.bookings_skipped) == (2, 0, 2)
        assert any(
            w.startswith(
                'Aba "Seg 26-10", B6:B7 (Sala 01 - Executiva, 09:30–10:30): conflito com a '
                "reserva já cadastrada de Carla Existente (09:00–10:00, chamado 1111) — pulada"
            )
            for w in a.warnings
        )
        assert any(
            w.startswith(
                'Aba "Seg 26-10 (2)", E5:E6 (Sala 07 - Atendimento, 09:00–10:00): conflito '
                'com E6:E7 (aba "Seg 26-10"), da própria planilha — pulada'
            )
            for w in b.warnings
        )
        assert (
            "1 reserva(s) repetida(s) dentro da própria planilha — pulada(s): D15:D16."
            in b.warnings
        )
        assert any("também aparece na aba" in w for w in b.warnings)
        if dry_run:
            assert await count(session, Booking) == 1
    assert len(await active_bookings(session)) == 3


async def test_lost_race_is_skipped_without_aborting(session, admin, rooms, monkeypatch):
    """Conflito detectado só na gravação (corrida): a reserva é pulada e as demais seguem."""
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Carla")
    sheet.put(S05, "09:00", "10:00", "Pedro")
    original = bookings_service.find_conflicts
    calls = {"n": 0}

    async def first_check_misses(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # outra recepcionista grava no mesmo instante, depois da checagem do importador
            await original(*args, **kwargs)
            await bookings_service.create_booking(
                session,
                BookingCreate(
                    room_id=rooms[S01].id,
                    date=MONDAY,
                    start=t("09:30"),
                    end=t("10:30"),
                    requester_name="Concorrente",
                ),
                admin,
                publish_events=False,
            )
            return []
        return await original(*args, **kwargs)

    monkeypatch.setattr(bookings_service, "find_conflicts", first_check_misses)
    report = await run(session, admin, wb, dry_run=False)
    monkeypatch.undo()
    rep = sheet_report(report, "Seg 26-10")
    assert (rep.bookings_imported, rep.bookings_skipped) == (1, 1)
    assert any("B5:B6" in w and "Concorrente" in w and "pulada" in w for w in rep.warnings)
    assert report.errors == []
    assert sorted(b.requester_name for b in await active_bookings(session)) == [
        "Concorrente",
        "Pedro",
    ]


# ---------------------------------------------------------------- horário de funcionamento e salas


async def test_outside_operating_hours_is_imported_with_warning(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", start="07:00", end="20:00")
    sheet.put(S01, "07:00", "08:00", "Carla - Café da manhã")
    sheet.put(S02, "07:00", "20:00", "Evento - Dia todo")
    sheet.put(S05, "19:00", "20:00", "Pedro - Noite")
    report = await run(session, admin, wb, dry_run=False)
    warnings = sheet_report(report, "Seg 26-10").warnings
    assert report.total_imported == 3
    bookings = await active_bookings(session)
    assert [(n, s, e) for n, s, e in spans(bookings)] == [
        (S01, "07:00", "08:00"),
        (S02, "08:00", "19:00"),
        (S05, "19:00", "20:00"),
    ]
    assert next(b for b in bookings if b.room.name == S02).period_type == PeriodType.FULL_DAY
    assert (
        sum(
            "fora do horário de funcionamento (08:00–19:00); importada mesmo assim" in w
            for w in warnings
        )
        == 2
    )
    assert any(
        "bloco de 07:00–20:00 importado como Dia Inteiro (08:00–19:00)" in w for w in warnings
    )


async def test_inactive_room_and_video_in_maintenance(session, admin, rooms):
    rooms[S07].is_active = False
    rooms[S02].video_status = VideoStatus.MAINTENANCE
    rooms[S02].maintenance_ticket_id = "TI-9"
    await session.commit()
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S07, "09:00", "10:00", "Carla")
    sheet.put(S07, "11:00", None, "Pedro")
    sheet.put(S02, "09:00", "10:00", "Bia - Reunião Teams")
    sheet.put(S05, "09:00", None, "Lia - Workshop (20 pessoas)")
    report = await run(session, admin, wb, dry_run=False)
    rep = sheet_report(report, "Seg 26-10")
    assert (rep.bookings_found, rep.bookings_imported, rep.bookings_skipped) == (4, 2, 2)
    assert report.unknown_rooms == []
    assert any(
        'Coluna E ("Sala 07 - Atendimento"): sala desativada — 2 reserva(s)' in w
        for w in rep.warnings
    )
    assert any("C5:C6" in w and "não tem vídeo operacional" in w for w in rep.warnings)
    assert any(
        "D5" in w and "20 participantes para uma sala de 6 lugares" in w for w in rep.warnings
    )


async def test_merge_over_several_rooms_and_banner_rows(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S05, "12:00", None, "Antes do almoço")
    banner_row = sheet.row("12:30")
    sheet.ws.cell(banner_row, 1).value = "ALMOÇO"
    sheet.ws.merge_cells(start_row=banner_row, start_column=1, end_row=banner_row, end_column=5)
    sheet.put(S05, "13:00", None, "Depois do almoço")
    first, last = sheet.row("15:00"), sheet.row("16:00") - 1
    sheet.ws.cell(first, sheet.col(S01)).value = "Evento integrado - RH"
    sheet.ws.merge_cells(
        start_row=first, start_column=sheet.col(S01), end_row=last, end_column=sheet.col(S02)
    )

    report = await run(session, admin, wb, dry_run=False)
    rep = sheet_report(report, "Seg 26-10")
    assert rep.bookings_imported == 4
    assert spans(await active_bookings(session)) == [
        (S05, "12:00", "12:30"),
        (S05, "13:00", "13:30"),
        (S01, "15:00", "16:00"),
        (S02, "15:00", "16:00"),
    ]
    assert any(
        f'A{banner_row}:E{banner_row}: faixa "ALMOÇO" atravessa a coluna de horários' in w
        for w in rep.warnings
    )
    assert any("B17:C18: célula mesclada sobre 2 salas" in w for w in rep.warnings)


async def test_merged_time_cells_and_hourly_labels(session, admin, rooms):
    """Rótulo de hora mesclado sobre duas linhas: cada linha vale meia hora."""
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["A1"], ws["B1"] = "Hora", S01
    for i, hour in enumerate(range(8, 19)):
        row = 2 + 2 * i
        ws.cell(row, 1, f"{hour}h")
        ws.merge_cells(start_row=row, start_column=1, end_row=row + 1, end_column=1)
    ws["B3"] = "Ana"  # 08:30
    ws["B4"] = "Bia"  # 09:00 até 11:00
    ws.merge_cells("B4:B7")
    ws["B23"] = "Caio"  # 18:30, última linha
    report = await run(session, admin, wb, dry_run=False)
    assert report.total_imported == 3
    assert sheet_report(report, "Seg 26-10").warnings == []
    assert spans(await active_bookings(session)) == [
        (S01, "08:30", "09:00"),
        (S01, "09:00", "11:00"),
        (S01, "18:30", "19:00"),
    ]


async def test_stacked_grids_with_repeated_header(session, admin, rooms):
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["A1"], ws["B1"], ws["C1"] = "Horário", S01, S05
    for i, label in enumerate(["08:00", "08:30", "09:00", "09:30"]):
        ws.cell(2 + i, 1, label)
    ws["B3"] = "Manhã sala 01"
    ws["A7"], ws["B7"], ws["C7"] = "Horário", S05, S01  # segundo bloco com ordem trocada
    for i, label in enumerate(["13:00", "13:30", "14:00"]):
        ws.cell(8 + i, 1, label)
    ws["B9"] = "Tarde sala 05"
    ws["C10"] = "Tarde sala 01"
    report = await run(session, admin, wb, dry_run=False)
    assert report.total_imported == 3
    assert spans(await active_bookings(session)) == [
        (S01, "08:30", "09:00"),
        (S05, "13:30", "14:00"),
        (S01, "14:00", "14:30"),
    ]


async def test_stray_text_and_duplicated_room_columns(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10", [S01, S05, "SALA 01 - EXECUTIVA"])
    sheet.put(S01, "09:00", None, "Carla")
    sheet.ws.cell(sheet.row("19:00") + 2, sheet.col(S05), "Obs.: ligar para a copa")
    report = await run(session, admin, wb)
    rep = sheet_report(report, "Seg 26-10")
    assert any(
        'A sala "Sala 01 - Executiva" aparece em mais de uma coluna (B e D)' in w
        for w in rep.warnings
    )
    assert "1 célula(s) com texto fora da grade de horários foram ignoradas: C27." in rep.warnings
    assert rep.bookings_found == 1


# ---------------------------------------------------------------- arquivo inválido


async def test_invalid_files(session, admin, rooms):
    for content, message in (
        (b"", "O arquivo está vazio."),
        (b"isto nao e um xlsx", "não é uma planilha .xlsx válida"),
        (b"PK\x03\x04quebrado", "não é uma planilha .xlsx válida"),
    ):
        report = await import_workbook(
            session, content, user=admin, dry_run=True, include_past=False, today=TODAY
        )
        assert report.sheets == [] and report.total_found == 0
        assert message in report.errors[0]
        with pytest.raises(InvalidWorkbookError):
            await import_workbook_or_raise(
                session, content, user=admin, dry_run=True, include_past=False, today=TODAY
            )


async def test_unexpected_sheet_error_is_isolated(session, admin, rooms, monkeypatch):
    from app.excel import reader

    wb = new_workbook()
    legacy(wb, "Seg 26-10").put(S01, "09:00", None, "Carla")
    legacy(wb, "Ter 27-10").put(S01, "09:00", None, "Pedro")
    original = reader._SheetReader.read

    def broken(self, **kwargs):
        if self.name == "Seg 26-10":
            raise RuntimeError("falha simulada")
        return original(self, **kwargs)

    monkeypatch.setattr(reader._SheetReader, "read", broken)
    report = await run(session, admin, wb)
    assert sheet_report(report, "Seg 26-10").status == "ERROR"
    assert sheet_report(report, "Ter 27-10").status == "DRY_RUN"
    assert report.errors == ['Aba "Seg 26-10": erro inesperado ao ler a aba.']
    assert report.total_found == 1


async def test_time_values_do_not_need_datetime_conversion(session, admin, rooms):
    """Valores de hora com resíduo de ponto flutuante (08:29:59.999) contam como 08:30."""
    wb = new_workbook()
    ws = wb.create_sheet("Seg 26-10")
    ws["A1"], ws["B1"] = "Horário", S01
    ws["A2"], ws["A3"], ws["A4"] = time(8, 0), time(8, 29, 59, 999000), time(9, 0)
    ws["B3"] = "Carla"
    await run(session, admin, wb, dry_run=False)
    assert spans(await active_bookings(session)) == [(S01, "08:30", "09:00")]


# ---------------------------------------------------------------- reimportação (import_key)


async def test_reimport_never_recreates_moved_cancelled_or_edited_bookings(session, admin, rooms):
    """Cada reserva importada guarda a marca da célula de origem (import_key): depois de movida,
    remarcada, editada ou cancelada no sistema, reimportar a planilha não a traz de volta."""
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Chamado 48211 - Carla - Diretoria")  # será cancelada
    sheet.put(S02, "09:00", "10:00", "Pedro - RH")  # será movida para a sala 05
    sheet.put(S05, "14:00", "15:00", "Chamado 48300 - Bia - Treino")  # remarcada para 16:00
    sheet.put(S07, "11:00", None, "Lia - Visita")  # chamado corrigido, mesmo lugar
    first = await run(session, admin, wb, dry_run=False)
    assert first.total_imported == 4
    by_name = {b.requester_name: b for b in await active_bookings(session)}
    keys = {b.import_key for b in by_name.values()}
    assert len(keys) == 4 and all(k.startswith("v1:") and len(k) == 43 for k in keys)

    await bookings_service.cancel_booking(session, by_name["Carla"].id, BookingCancel(), admin)
    for name, fields in (
        ("Pedro", {"room_id": rooms[S05].id}),
        ("Bia", {"start": t("16:00"), "end": t("17:00")}),
        ("Lia", {"ticket_number": "999"}),
    ):
        booking = by_name[name]
        await bookings_service.update_booking(
            session, booking.id, BookingUpdate(version=booking.version, **fields), admin
        )

    for dry_run in (True, False):
        again = await run(session, admin, wb, dry_run=dry_run)
        rep = sheet_report(again, "Seg 26-10")
        assert (rep.bookings_found, rep.bookings_imported, rep.bookings_skipped) == (4, 0, 4)
        assert (
            "4 reserva(s) já importada(s) anteriormente (pode ter sido movida ou cancelada no "
            "sistema) — pulada(s): B5:B6, C5:C6, E9, D15:D16." in rep.warnings
        )
        assert not any("conflito" in w for w in rep.warnings)
    assert await count(session, Booking) == 4
    assert sorted(spans(await active_bookings(session))) == [
        (S05, "09:00", "10:00"),
        (S05, "16:00", "17:00"),
        (S07, "11:00", "11:30"),
    ]


async def test_new_cells_still_import_after_a_previous_import(session, admin, rooms):
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Carla - Diretoria")
    await run(session, admin, wb, dry_run=False)
    booking = (await active_bookings(session))[0]
    await bookings_service.cancel_booking(session, booking.id, BookingCancel(), admin)
    sheet.put(S01, "11:00", None, "Carla - Diretoria")  # mesmo texto em outro horário: é nova
    sheet.put(S05, "09:00", "10:00", "Carla - Diretoria")  # e em outra sala também
    report = await run(session, admin, wb, dry_run=False)
    assert (report.total_imported, report.total_skipped) == (2, 1)
    assert spans(await active_bookings(session)) == [
        (S05, "09:00", "10:00"),
        (S01, "11:00", "11:30"),
    ]


# ---------------------------------------------------------------- travas de seleção (soft locks)


async def test_bookings_someone_is_selecting_are_skipped(session, admin, receptionist, rooms):
    store = MemoryLockStore()
    await store.acquire(
        room_id=rooms[S01].id,
        date=MONDAY,
        start=t("09:30"),
        end=t("10:00"),
        user_id=receptionist.id,
        user_name=receptionist.full_name,
    )
    await store.acquire(  # trava de quem importa também vale (a tela dele está no meio de algo)
        room_id=rooms[S05].id,
        date=MONDAY,
        start=t("14:00"),
        end=t("14:30"),
        user_id=admin.id,
        user_name=admin.full_name,
    )
    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", "10:00", "Carla - Diretoria")
    sheet.put(S05, "14:00", "15:00", "Pedro - RH")
    sheet.put(S07, "09:00", None, "Bia - Visita")
    for dry_run in (True, False):
        report = await import_workbook(
            session,
            to_bytes(wb),
            user=admin,
            dry_run=dry_run,
            include_past=False,
            today=TODAY,
            lock_store=store,
        )
        rep = sheet_report(report, "Seg 26-10")
        assert (rep.bookings_found, rep.bookings_imported, rep.bookings_skipped) == (3, 1, 2)
        assert (
            'Aba "Seg 26-10", B5:B6 (Sala 01 - Executiva, 09:00–10:00): Ana Recepção está '
            "reservando este horário agora na grade — pulada." in rep.warnings
        )
        assert any(
            w.startswith('Aba "Seg 26-10", D15:D16') and f"{admin.full_name} está reservando" in w
            for w in rep.warnings
        )
    assert [b.requester_name for b in await active_bookings(session)] == ["Bia"]


async def test_unreachable_lock_store_only_warns(session, admin, rooms):
    class BrokenStore(NullLockStore):
        calls = 0

        async def find_overlapping(self, room_id, start_dt, end_dt):
            BrokenStore.calls += 1
            raise ConnectionError("Redis fora do ar")

    wb = new_workbook()
    sheet = legacy(wb, "Seg 26-10")
    sheet.put(S01, "09:00", None, "Carla")
    sheet.put(S05, "09:00", None, "Pedro")
    legacy(wb, "Ter 27-10").put(S01, "09:00", None, "Bia")
    report = await import_workbook(
        session,
        to_bytes(wb),
        user=admin,
        dry_run=False,
        include_past=False,
        today=TODAY,
        lock_store=BrokenStore(),
    )
    assert report.total_imported == 3 and report.errors == []
    assert BrokenStore.calls == 1  # desiste na primeira falha (não espera o Redis a cada reserva)
    warnings = [w for s in report.sheets for w in s.warnings]
    assert warnings == [
        "Não foi possível consultar as travas de seleção das telas abertas (tempo real "
        "indisponível); as reservas foram conferidas só contra o banco de dados."
    ]


# ---------------------------------------------------------------- conteúdo oculto e salas


async def test_hidden_stale_copy_does_not_beat_the_visible_sheet(session, admin, rooms):
    wb = new_workbook()
    stale = legacy(wb, "Seg 26-10 (antiga)")
    stale.put(S01, "09:00", "10:00", "Reunião antiga - cancelada")
    stale.ws.sheet_state = "veryHidden"
    legacy(wb, "Seg 26-10").put(S01, "09:30", "10:30", "Carla - Diretoria")
    report = await run(session, admin, wb, dry_run=False)
    assert sheet_report(report, "Seg 26-10 (antiga)").status == "SKIPPED_UNRECOGNIZED"
    assert (report.total_imported, report.total_skipped) == (1, 0)
    assert [b.requester_name for b in await active_bookings(session)] == ["Carla"]


async def test_header_matches_the_active_room_not_a_deactivated_twin(
    session, admin, rooms, make_room
):
    await make_room("Sala 5 - Reunião", is_active=False, display_order=0)
    wb = new_workbook()
    legacy(wb, "Seg 26-10").put(S05, "09:00", None, "Pedro - RH")
    report = await run(session, admin, wb, dry_run=False)
    assert report.total_imported == 1, sheet_report(report, "Seg 26-10").warnings
    assert spans(await active_bookings(session)) == [(S05, "09:00", "09:30")]
