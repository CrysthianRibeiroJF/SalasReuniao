"""Exportação .xlsx no formato espelho (contrato 5.6) e ida e volta exportação -> importação."""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pytest
from sqlalchemy import delete

from app.excel.common import EXPORT_MARKER, PART_SEPARATOR
from app.excel.exporter import export_day, export_week
from app.excel.importer import import_workbook
from app.models import Booking, BookingLog, BookingStatus, PeriodType, RoomType, VideoStatus
from app.schemas import BookingCancel, BookingCreate, BookingStatusIn
from app.services import bookings as bookings_service
from tests.excel_builders import MONDAY, TODAY, active_bookings, booking_tuples, reload, t

SATURDAY = MONDAY + timedelta(days=5)


async def book(session, user, room, d=MONDAY, *, enforce_hours=True, **fields):
    fields.setdefault("requester_name", "Fulano de Tal")
    for key in ("start", "end"):
        if isinstance(fields.get(key), str):
            fields[key] = t(fields[key])
    result = await bookings_service.create_booking(
        session,
        BookingCreate(room_id=room.id, date=d, **fields),
        user,
        enforce_hours=enforce_hours,
        publish_events=False,
    )
    return result.booking


@pytest.fixture
async def grid(session, admin, make_room):
    """Salas de vídeo, normais, vídeo em manutenção e reservas com todos os selos."""
    rooms = {
        "s01": await make_room("Sala 01 - Executiva", RoomType.VIDEO, capacity=12, display_order=1),
        "s02": await make_room(
            "Sala 02 - Diretoria",
            RoomType.VIDEO,
            capacity=10,
            display_order=2,
            video_status=VideoStatus.MAINTENANCE,
            maintenance_ticket_id="TI-77",
        ),
        "s03": await make_room(
            "Sala 03 - Treinamento", RoomType.VIDEO, capacity=20, display_order=3
        ),
        "s05": await make_room("Sala 05 - Reunião", RoomType.NORMAL, capacity=6, display_order=5),
        "s07": await make_room(
            "Sala 07 - Atendimento", RoomType.NORMAL, capacity=4, display_order=7
        ),
        "old": await make_room(
            "Sala 08 - Antiga", RoomType.NORMAL, display_order=8, is_active=False
        ),
    }
    await book(
        session,
        admin,
        rooms["s01"],
        start="09:00",
        end="10:30",
        ticket_number="48211",
        requester_name="Carla Mendes",
        title="Diretoria Financeira",
        requires_video=True,
        attendees=8,
    )
    await book(
        session,
        admin,
        rooms["s03"],
        period_type=PeriodType.FULL_DAY,
        requester_name="Marcos Silva",
        title="Integração",
    )
    await book(
        session,
        admin,
        rooms["s05"],
        period_type=PeriodType.MORNING,
        ticket_number="INC0012345",
        requester_name="Fernanda Costa",
    )
    afternoon = await book(
        session,
        admin,
        rooms["s05"],
        period_type=PeriodType.AFTERNOON,
        requester_name="Gustavo Nogueira",
        title="Conselho",
    )
    await bookings_service.set_booking_status(
        session, afternoon.id, BookingStatusIn(status="CHECKED_IN"), admin
    )
    await book(
        session,
        admin,
        rooms["s02"],
        start="14:00",
        end="15:00",
        requester_name="Bia Lima",
        title="RH - Recrutamento",
        requires_video=True,
    )
    await book(session, admin, rooms["s07"], start="14:15", end="15:45", requester_name="Lia Prado")
    await book(session, admin, rooms["s07"], start="16:00", end="16:15", requester_name="Ana")
    await book(session, admin, rooms["s07"], start="16:15", end="16:30", requester_name="Bia")
    no_show = await book(
        session,
        admin,
        rooms["s01"],
        start="11:00",
        end="11:30",
        requester_name="Rui Alves",
        ticket_number="48300",
    )
    await bookings_service.set_booking_status(
        session, no_show.id, BookingStatusIn(status="NO_SHOW"), admin
    )
    cancelled = await book(
        session, admin, rooms["s01"], start="17:00", end="18:00", requester_name="Cancelada"
    )
    await bookings_service.cancel_booking(session, cancelled.id, BookingCancel(), admin)
    return rooms


def texts(ws) -> dict[str, str]:
    return {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}


def merged(ws) -> set[str]:
    return {str(r) for r in ws.merged_cells.ranges}


def rgb(cell) -> str:
    return cell.fill.start_color.rgb[-6:]


# ---------------------------------------------------------------- layout


async def test_day_export_layout(session, grid):
    wb = reload(await export_day(session, MONDAY))
    assert wb.sheetnames == ["Seg 26-10"]
    assert wb.properties.keywords == EXPORT_MARKER
    ws = wb["Seg 26-10"]
    values = texts(ws)

    # A: horários | B-C: vídeo (01, 03) | D-F: normais (02 em manutenção, 05, 07); 08 desativada sai
    assert values["A1"] == "Agenda de Salas — Segunda-feira, 26/10/2026"
    assert {"A1:F1", "A2:A3", "B2:C2", "D2:F2"} <= merged(ws)
    assert (values["A2"], values["B2"], values["D2"]) == (
        "Horário",
        "Videoconferência",
        "Salas Normais",
    )
    assert values["B3"] == "Sala 01 - Executiva\n12 lugares"
    assert values["C3"] == "Sala 03 - Treinamento\n20 lugares"
    assert values["D3"] == "Sala 02 - Diretoria\n10 lugares\n(vídeo em manutenção — chamado TI-77)"
    assert values["F3"] == "Sala 07 - Atendimento\n4 lugares"
    assert "G3" not in values  # sala desativada sem reservas não aparece

    labels = [ws.cell(row, 1).value for row in range(4, 26)]
    assert labels[0] == "08:00" and labels[1] == "08:30" and labels[-1] == "18:30"
    assert ws["A26"].value is None

    assert values["B6"] == (
        "Chamado 48211\nCarla Mendes\nDiretoria Financeira\n[Videoconferência] [8 participantes]"
    )
    assert values["C4"] == "Marcos Silva\nIntegração\n[Dia inteiro] [Presencial — remanejável]"
    assert values["E4"] == "Chamado INC0012345\nFernanda Costa\n[Manhã toda]"
    assert values["E14"] == "Gustavo Nogueira\nConselho\n[Tarde toda] [Check-in]"
    assert values["D16"] == "Bia Lima\nRH - Recrutamento\n[⚠ Precisa de vídeo]"
    assert values["B10"] == "Chamado 48300\nRui Alves\n[Presencial — remanejável] [Não compareceu]"
    assert values["F16"] == "Lia Prado\n14:15–15:45"  # fora dos slots: horário explícito
    assert values["F20"] == f"Ana\n16:00–16:15\n{PART_SEPARATOR}\nBia\n16:15–16:30"
    assert "Cancelada" not in "".join(values.values())
    assert {"B6:B8", "C4:C25", "E4:E11", "E14:E25", "D16:D17", "F16:F19"} <= merged(ws)
    assert not any(r.startswith("F20") for r in merged(ws))

    assert rgb(ws["B6"]) == "BDD7EE"  # reserva em sala de vídeo
    assert rgb(ws["E4"]) == "C6E0B4"  # reserva em sala normal
    assert rgb(ws["C4"]) == "FCE4D6"  # remanejável
    assert rgb(ws["E14"]) == "A9D08E"  # check-in
    assert rgb(ws["B10"]) == "D9D9D9" and ws["B10"].font.i  # não compareceu
    assert rgb(ws["D16"]) == "F8CBAD"  # precisa de vídeo
    assert rgb(ws["B3"]) != rgb(ws["E3"])  # cabeçalhos de bloco com cores distintas
    assert rgb(ws["D3"]) == "FFF2CC"  # vídeo em manutenção
    assert ws["B6"].alignment.wrap_text and ws["B6"].alignment.vertical == "top"
    assert ws["B6"].border.top.style == "thin" and ws["E5"].border.left.style == "thin"
    assert ws["D10"].border.left.style == "medium"  # separa os blocos

    assert ws.freeze_panes == "B4"
    assert ws.column_dimensions["B"].width == 24 and ws.column_dimensions["A"].width == 9
    assert ws.row_dimensions[5].height >= 30
    assert ws.page_setup.orientation == "landscape"
    assert (ws.page_setup.fitToWidth, ws.page_setup.fitToHeight) == (1, 0)
    assert ws.sheet_properties.pageSetUpPr.fitToPage is True
    assert ws.print_title_rows == "$1:$3"
    assert "Exportado em" in ws.oddFooter.left.text


async def test_week_export_has_one_sheet_per_work_day_plus_booked_weekends(session, admin, grid):
    await book(
        session, admin, grid["s05"], SATURDAY, start="09:00", end="10:00", requester_name="Plantão"
    )
    wb = reload(await export_week(session, MONDAY + timedelta(days=2)))  # quarta -> segunda
    assert wb.sheetnames == [
        "Seg 26-10",
        "Ter 27-10",
        "Qua 28-10",
        "Qui 29-10",
        "Sex 30-10",
        "Sáb 31-10",
    ]
    assert wb["Ter 27-10"]["A1"].value == "Agenda de Salas — Terça-feira, 27/10/2026"
    assert texts(wb["Sáb 31-10"])["E6"] == "Plantão"
    # sem reservas: título + "Horário" e 2 grupos + 5 salas + 22 horários
    assert len(texts(wb["Qua 28-10"])) == 1 + 3 + 5 + 22


async def test_rows_extend_to_bookings_outside_operating_hours(session, admin, grid):
    await book(
        session,
        admin,
        grid["s05"],
        MONDAY + timedelta(days=1),
        start="07:00",
        end="07:30",
        requester_name="Cedo",
        enforce_hours=False,
    )
    await book(
        session,
        admin,
        grid["s05"],
        MONDAY + timedelta(days=1),
        start="19:00",
        end="20:00",
        requester_name="Tarde da noite",
        enforce_hours=False,
    )
    ws = reload(await export_day(session, MONDAY + timedelta(days=1))).active
    assert ws["A4"].value == "07:00" and ws["E4"].value == "Cedo"
    last = 4 + (20 * 60 - 7 * 60) // 30 - 1
    assert ws.cell(last, 1).value == "19:30"
    assert ws.cell(last - 1, 5).value == "Tarde da noite"


async def test_export_without_rooms_and_with_illegal_characters(session, admin, make_room):
    ws = reload(await export_day(session, MONDAY)).active
    assert ws["A1"].value.endswith("(nenhuma sala ativa cadastrada)")
    assert ws["A4"].value == "08:00"

    room = await make_room("Sala 05 - Reunião")
    await book(
        session,
        admin,
        room,
        start="09:00",
        end="10:00",
        requester_name="Ana\x0bSouza",
        title="Plano\x01 anual",
    )
    ws = reload(await export_day(session, MONDAY)).active
    assert ws["B6"].value == "Ana Souza\nPlano anual"


async def test_inactive_room_with_bookings_is_kept_in_export(session, admin, grid):
    old = grid["old"]
    old.is_active = True
    await session.commit()
    await book(session, admin, old, start="09:00", end="10:00", requester_name="Histórico")
    old.is_active = False
    await session.commit()
    ws = reload(await export_day(session, MONDAY)).active
    assert ws["G3"].value == "Sala 08 - Antiga\n8 lugares\n(sala desativada)"
    assert ws["G6"].value == "Histórico"


# ---------------------------------------------------------------- ida e volta


async def _wipe_bookings(session) -> None:
    await session.execute(delete(BookingLog))
    await session.execute(delete(Booking))
    await session.commit()


async def test_week_round_trip_reproduces_bookings(session, admin, grid):
    s01, s07 = grid["s01"], grid["s07"]
    wednesday, friday = MONDAY + timedelta(days=2), MONDAY + timedelta(days=4)
    await book(
        session,
        admin,
        s01,
        wednesday,
        start="08:00",
        end="08:30",
        requester_name="Equipe de TI",
        title="Daily Teams",
    )
    await book(
        session,
        admin,
        s01,
        wednesday,
        start="08:30",
        end="09:00",
        requester_name="Equipe de TI",
        title="Daily Teams",
    )  # mesmo texto, reservas distintas
    await book(
        session,
        admin,
        s07,
        friday,
        start="10:00",
        end="12:00",
        ticket_number="RITM0045678",
        requester_name="RH - Recrutamento",
        attendees=1,
    )
    await book(
        session,
        admin,
        s07,
        SATURDAY,
        start="09:00",
        end="10:00",
        requester_name="Plantão",
        title="Atendimento 2026 - #12",
    )
    expected = booking_tuples(await active_bookings(session))
    assert len(expected) == 13

    content = await export_week(session, MONDAY)
    await _wipe_bookings(session)
    report = await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert report.errors == [] and report.unknown_rooms == []
    assert (report.total_found, report.total_imported, report.total_skipped) == (13, 13, 0)
    assert booking_tuples(await active_bookings(session)) == expected
    assert {s.sheet_name: s.bookings_imported for s in report.sheets} == {
        "Seg 26-10": 9,
        "Ter 27-10": 0,
        "Qua 28-10": 2,
        "Qui 29-10": 0,
        "Sex 30-10": 1,
        "Sáb 31-10": 1,
    }
    imported = await active_bookings(session)
    assert {b.status for b in imported} == {BookingStatus.CONFIRMED}  # status não é importado

    again = await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert (again.total_imported, again.total_skipped) == (0, 13)


async def test_day_round_trip_with_dry_run(session, admin, grid):
    content = await export_day(session, MONDAY)
    expected = booking_tuples(await active_bookings(session))
    await _wipe_bookings(session)
    dry = await import_workbook(
        session, content, user=admin, dry_run=True, include_past=False, today=TODAY
    )
    assert (dry.total_found, dry.total_imported, dry.total_skipped) == (
        len(expected),
        len(expected),
        0,
    )
    assert await active_bookings(session) == []
    await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert booking_tuples(await active_bookings(session)) == expected


async def test_exported_misaligned_times_round_trip(session, admin, make_room):
    room = await make_room("Sala 05 - Reunião", capacity=6)
    for start, end, who in (
        ("08:05", "08:20", "A"),
        ("08:20", "08:40", "B"),
        ("08:40", "09:10", "C"),
        ("09:10", "09:15", "D"),
        ("10:00", "10:45", "E"),
    ):
        await book(session, admin, room, start=start, end=end, requester_name=who)
    expected = booking_tuples(await active_bookings(session))
    content = await export_day(session, MONDAY)
    ws = reload(content).active
    assert "B4:B6" in merged(ws)  # A, B, C e D dividem as linhas 08:00–09:30 numa célula só
    assert ws["B4"].value.count(PART_SEPARATOR) == 3

    await _wipe_bookings(session)
    report = await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert report.total_imported == 5
    assert booking_tuples(await active_bookings(session)) == expected


async def test_export_of_imported_legacy_matches_after_reimport(session, admin, grid):
    """Planilha exportada e importada num banco que já tem parte das reservas: só falta entra."""
    content = await export_day(session, MONDAY)
    first = (await active_bookings(session))[0]
    await bookings_service.cancel_booking(session, first.id, BookingCancel(), admin)
    report = await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert report.total_imported == 1
    assert report.total_skipped == report.total_found - 1
    assert len(await active_bookings(session)) == report.total_found


async def test_week_export_of_empty_week_is_importable(session, admin, grid):
    content = await export_week(session, MONDAY + timedelta(days=7))
    report = await import_workbook(
        session, content, user=admin, dry_run=True, include_past=False, today=TODAY
    )
    assert [s.status for s in report.sheets] == ["DRY_RUN"] * 5
    assert report.total_found == 0 and report.unknown_rooms == []
    assert [s.date for s in report.sheets] == [
        date(2026, 11, 2) + timedelta(days=i) for i in range(5)
    ]


# ---------------------------------------------------------------- textos perigosos ou ambíguos


def _sheet_xml(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


async def test_formula_like_texts_are_exported_as_text(session, admin, make_room):
    """Um solicitante "=1+1" (ou =HYPERLINK(...)) virava fórmula ativa no arquivo exportado;
    com assunto, o texto de várias linhas deixava o arquivo inteiro ilegível no Excel."""
    room = await make_room("=X", capacity=3)
    await book(
        session,
        admin,
        room,
        start="09:00",
        end="10:00",
        requester_name="=1+1",
        title='=HYPERLINK("http://exemplo.invalid/x","Abrir agenda")',
    )
    await book(session, admin, room, start="11:00", end="11:30", requester_name="+55 11 9999")
    await book(session, admin, room, start="12:00", end="12:30", requester_name="@Ana -RH")
    expected = booking_tuples(await active_bookings(session))
    content = await export_day(session, MONDAY)

    xml = _sheet_xml(content)
    assert "<f>" not in xml and "<f " not in xml
    ws = reload(content, data_only=False).active
    assert (ws["B3"].data_type, ws["B3"].value) == ("s", "=X\n3 lugares")
    cell = ws["B6"]
    assert cell.data_type == "s" and cell.quotePrefix
    assert cell.value == '=1+1\n=HYPERLINK("http://exemplo.invalid/x","Abrir agenda")'
    assert ws["B10"].quotePrefix and ws["B12"].quotePrefix

    await _wipe_bookings(session)
    report = await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert (report.total_found, report.total_imported, report.errors) == (3, 3, [])
    assert booking_tuples(await active_bookings(session)) == expected


async def test_xml_illegal_characters_do_not_break_the_file(session, admin, make_room):
    room = await make_room("Sala 05 - Reunião")
    await book(
        session,
        admin,
        room,
        start="09:00",
        end="10:00",
        requester_name="Ana\ufffeSouza\uffff",
        title="Plano\ufffe anual",
    )
    content = await export_day(session, MONDAY)
    ws = reload(content).active  # antes: o arquivo não abria (XML inválido)
    assert ws["B6"].value == "AnaSouza\nPlano anual"


async def test_round_trip_is_exact_for_texts_that_look_like_markup(session, admin, make_room):
    """A exportação é lida por posição: chamados livres, horários e participantes no assunto,
    solicitante "-" ou começando por "Chamado" voltam exatamente como estavam."""
    room = await make_room("Sala 05 - Reunião", capacity=6, display_order=5)
    video = await make_room("Sala 01 - Executiva", RoomType.VIDEO, capacity=12, display_order=1)
    for target, fields in (
        (
            room,
            dict(
                start="08:00",
                end="09:00",
                ticket_number="a definir",
                requester_name="Ana Souza",
                title="Revisão 14h às 15h (8 pessoas) #4567",
            ),
        ),
        (room, dict(start="09:00", end="10:00", ticket_number="INC 0012345", requester_name="-")),
        (room, dict(start="10:05", end="10:50", requester_name="Chamado Fulano", title="Projeto")),
        (room, dict(start="11:00", end="12:00", requester_name="Chamado 777", title="sem chamado")),
        (room, dict(start="12:00", end="13:00", requester_name="Bia", title="14:00–15:00")),
        (room, dict(start="13:00", end="14:00", requester_name="Caio", title="[Check-in]")),
        (room, dict(start="14:15", end="15:40", requester_name="Gil da Unidade")),
        (room, dict(start="16:00", end="17:00", requester_name="Dora  Lima", title="Qualidade")),
        (
            video,
            dict(
                start="08:00",
                end="12:00",  # Horário Específico de 08:00 às 12:00 não vira "Manhã toda"
                requester_name="Elis",
                title="Comitê Teams",
                attendees=10_000,
            ),
        ),
        (
            video,
            dict(
                period_type=PeriodType.AFTERNOON,
                requester_name="Fábio",
                title="[RH]",
                requires_video=True,
            ),
        ),
    ):
        await book(session, admin, target, **fields)
    expected = booking_tuples(await active_bookings(session))
    content = await export_day(session, MONDAY)
    ws = reload(content).active
    texts_by_value = set(texts(ws).values())
    assert "Chamado —\nChamado Fulano\nProjeto\n10:05–10:50" in texts_by_value
    assert "Bia\n14:00–15:00\n12:00–13:00" in texts_by_value
    assert "Caio\n[Check-in]\n13:00–14:00" in texts_by_value

    await _wipe_bookings(session)
    report = await import_workbook(
        session, content, user=admin, dry_run=False, include_past=False, today=TODAY
    )
    assert (report.total_found, report.total_imported, report.errors) == (10, 10, [])
    assert booking_tuples(await active_bookings(session)) == expected

    again = await import_workbook(
        session, content, user=admin, dry_run=True, include_past=False, today=TODAY
    )
    assert (again.total_imported, again.total_skipped) == (0, 10)
    assert any("10 reserva(s) já cadastrada(s)" in w for w in again.sheets[0].warnings)
