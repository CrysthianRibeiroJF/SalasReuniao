"""CLI (`import-xlsx` / `export-xlsx`) com o módulo Excel real e a planilha de exemplo.

Os comandos usam asyncio.run(): rodam numa thread para não colidir com o loop do pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from datetime import date, time, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app import cli
from app.config import settings
from app.excel.importer import import_workbook
from app.excel.parsing import parse_date_text, resolve_date
from app.excel.reader import open_workbook
from app.models import Booking, BookingLog, LogAction
from app.realtime.redis_store import RedisLockStore
from app.seed import DEMO_ROOMS
from app.timeutil import today_local
from scripts import make_sample_legacy
from tests.excel_builders import HEADERS, legacy, new_workbook, reload, sheet_report
from tests.realtime_support import FakeRedisTcp

pytest_plugins = ("tests.excel_fixtures",)


async def run_cli(*argv: str) -> int:
    return await asyncio.to_thread(cli.main, list(argv))


async def count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def demo_rooms(make_room) -> None:
    for order, (name, room_type, capacity, resources) in enumerate(DEMO_ROOMS, start=1):
        await make_room(
            name, room_type, capacity=capacity, display_order=order, resources=resources
        )


# ---------------------------------------------------------------- import-xlsx / export-xlsx


async def test_import_xlsx_command_simulates_then_commits(capsys, session, admin, rooms, tmp_path):
    d = today_local() + timedelta(days=4)
    wb = new_workbook()
    sheet = legacy(wb, f"{d:%d.%m.%Y}", [*HEADERS, "Sala 99"])
    sheet.put("Sala 01 - Executiva", "09:00", "10:00", "Chamado 48211 - Carla - Diretoria")
    sheet.put("Sala 99", "10:00", None, "Sala que não existe")
    path = tmp_path / "planilha legada.xlsx"
    wb.save(path)

    assert await run_cli("import-xlsx", str(path)) == 0
    out = capsys.readouterr().out
    assert "SIMULAÇÃO DA IMPORTAÇÃO (nada foi gravado)" in out
    assert f'Aba "{d:%d.%m.%Y}" ({d:%d/%m/%Y}): simulada' in out
    assert "reservas: 2 encontradas, 1 importáveis, 1 puladas" in out
    assert 'aviso: Coluna F ("Sala 99"): sala não cadastrada' in out
    assert "Salas não encontradas no cadastro: Sala 99" in out
    assert await count(session, Booking) == 0

    assert await run_cli("import-xlsx", str(path), "--commit", "--user", "admin") == 0
    out = capsys.readouterr().out
    assert "IMPORTAÇÃO CONCLUÍDA" in out and "1 importadas" in out
    assert await count(session, BookingLog, BookingLog.action == LogAction.IMPORTED) == 1

    assert await run_cli("import-xlsx", str(path), "--commit") == 0
    out = capsys.readouterr().out
    assert "0 importadas, 2 puladas" in out and "já cadastrada(s) no sistema" in out
    assert await count(session, Booking) == 1


async def test_import_xlsx_command_sees_soft_locks_through_redis(
    capsys, monkeypatch, session, admin, receptionist, rooms, tmp_path
):
    """Com AGENDA_REDIS_URL, a CLI confere as travas de seleção das telas abertas (mesmo Redis
    das instâncias do servidor) e pula a reserva que alguém está selecionando."""
    d = today_local() + timedelta(days=4)
    wb = new_workbook()
    sheet = legacy(wb, f"{d:%d.%m.%Y}", HEADERS)
    sheet.put("Sala 01 - Executiva", "09:00", "10:00", "Chamado 48211 - Carla - Diretoria")
    sheet.put("Sala 05 - Reunião", "14:00", None, "Pedro - RH")
    path = tmp_path / "planilha.xlsx"
    wb.save(path)
    with FakeRedisTcp() as fake:
        monkeypatch.setattr(settings, "redis_url", fake.url)
        client = Redis.from_url(fake.url, decode_responses=True, protocol=2)
        try:
            lock, _ = await RedisLockStore(client).acquire(
                room_id=rooms["Sala 01 - Executiva"].id,
                date=d,
                start=time(9, 30),
                end=time(10, 0),
                user_id=receptionist.id,
                user_name=receptionist.full_name,
            )
            assert lock is not None
            assert await run_cli("import-xlsx", str(path), "--commit") == 0
        finally:
            await client.aclose()
    out = capsys.readouterr().out
    assert "Ana Recepção está reservando este horário agora na grade — pulada." in out
    assert "reservas: 2 encontradas, 1 importadas, 1 puladas" in out
    assert await count(session, Booking) == 1


async def test_import_xlsx_command_reports_invalid_file(capsys, admin, tmp_path):
    path = tmp_path / "ruim.xlsx"
    path.write_bytes(b"isto nao e uma planilha")
    assert await run_cli("import-xlsx", str(path)) == 1
    out = capsys.readouterr().out
    assert "Nenhuma aba encontrada" in out and "não é uma planilha .xlsx válida" in out


async def test_export_xlsx_command(capsys, session, admin, rooms, tmp_path):
    day_file = tmp_path / "saida" / "dia.xlsx"
    assert await run_cli("export-xlsx", "--date", "2026-10-26", "-o", str(day_file)) == 0
    assert "Planilha gerada" in capsys.readouterr().out
    assert reload(day_file.read_bytes()).sheetnames == ["Seg 26-10"]

    week_file = tmp_path / "semana.xlsx"
    assert await run_cli("export-xlsx", "--date", "2026-10-29", "--week", "-o", str(week_file)) == 0
    assert reload(week_file.read_bytes()).sheetnames[0] == "Seg 26-10"
    assert len(reload(week_file.read_bytes()).sheetnames) == 5


# ---------------------------------------------------------------- planilha de exemplo


EXPECTED_SAMPLE = {
    # aba: (situação, encontradas, importáveis, puladas)
    "LEIA-ME": ("SKIPPED_UNRECOGNIZED", 0, 0, 0),
    "Qui 22-10": ("SKIPPED_PAST", 0, 0, 0),
    "Seg 26-10": ("DRY_RUN", 5, 4, 1),  # 1 na "Sala 11 - Copa" (não cadastrada)
    "Ter 27.10": ("DRY_RUN", 3, 3, 0),
    "quarta 28-10-26": ("DRY_RUN", 3, 3, 0),
    "Qui 29.10.2026": ("DRY_RUN", 3, 3, 0),
    "Sex 30-10": ("DRY_RUN", 3, 3, 0),
    "Sex 30-10 (cópia)": ("DRY_RUN", 3, 0, 3),  # 2 repetidas + 1 conflito com a aba original
}


async def test_sample_script_generates_the_expected_dry_run(
    session, admin, make_room, tmp_path, capsys
):
    await demo_rooms(make_room)
    output = tmp_path / "exemplo.xlsx"
    assert make_sample_legacy.main(["--monday", "2026-10-26", "-o", str(output)]) == 0
    assert "Planilha de exemplo gerada" in capsys.readouterr().out

    report = await import_workbook(
        session,
        output.read_bytes(),
        user=admin,
        dry_run=True,
        include_past=False,
        today=date(2026, 10, 23),
    )
    got = {
        s.sheet_name: (s.status, s.bookings_found, s.bookings_imported, s.bookings_skipped)
        for s in report.sheets
    }
    assert got == EXPECTED_SAMPLE
    assert (report.total_found, report.total_imported, report.total_skipped) == (20, 16, 4)
    assert report.unknown_rooms == ["Sala 11 - Copa"]
    assert report.errors == []
    copy = sheet_report(report, "Sex 30-10 (cópia)")
    assert any("conflito com B7:B8" in w for w in copy.warnings)
    assert any("2 reserva(s) repetida(s)" in w for w in copy.warnings)
    assert await count(session, Booking) == 0

    real = await import_workbook(
        session,
        output.read_bytes(),
        user=admin,
        dry_run=False,
        include_past=False,
        today=date(2026, 10, 23),
    )
    assert real.total_imported == 16
    assert await count(session, Booking) == 16


async def test_sample_script_rejects_non_monday(capsys, tmp_path):
    with pytest.raises(SystemExit):
        make_sample_legacy.main(["--monday", "2026-10-27", "-o", str(tmp_path / "x.xlsx")])
    assert "não é uma segunda-feira" in capsys.readouterr().err


async def test_committed_sample_file_imports_with_cli(session, admin, make_room, capsys):
    """docs/exemplos/planilha-legada-exemplo.xlsx abre e a simulação lista o esperado."""
    path = make_sample_legacy.DEFAULT_OUTPUT
    assert path.is_file(), "gere com: python scripts/make_sample_legacy.py"
    wb = open_workbook(path.read_bytes())
    monday_sheet = next(name for name in wb.sheetnames if name.startswith("Seg "))
    monday, _ = resolve_date(parse_date_text(monday_sheet), today_local())
    await demo_rooms(make_room)

    report = await import_workbook(
        session,
        path.read_bytes(),
        user=admin,
        dry_run=True,
        include_past=False,
        today=monday - timedelta(days=3),
    )
    assert (report.total_found, report.total_imported, report.total_skipped) == (20, 16, 4)

    assert await run_cli("import-xlsx", str(path)) == 0
    out = capsys.readouterr().out
    assert "SIMULAÇÃO DA IMPORTAÇÃO" in out and "Sala 11 - Copa" in out
    assert await count(session, Booking) == 0
