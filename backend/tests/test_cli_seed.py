"""CLI (`python -m app.cli`) e dados de demonstração (seção 9).

Os comandos usam asyncio.run(): rodam numa thread (asyncio.to_thread) para não colidir com o loop
do pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import date

import pytest
from sqlalchemy import func, select

from app import cli
from app.config import settings
from app.models import Booking, BookingLog, LogAction, PeriodType, Room, RoomType, User, UserRole
from app.schemas import ImportReport, ImportSheetReport
from app.cli import bootstrap_password_problem
from app.seed import DEMO_BOOKINGS, demo_week_monday, seed_demo
from app.services import users as users_service
from app.timeutil import to_local, week_monday

MONDAY = date(2026, 10, 26)


async def run_cli(*argv: str) -> int:
    return await asyncio.to_thread(cli.main, list(argv))


async def _count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


# ---------------------------------------------------------------- seed


async def test_seed_demo_creates_rooms_users_and_week_bookings(session):
    summary = await seed_demo(session, today=date(2026, 10, 28))  # quarta
    assert summary.week_start == MONDAY
    assert len(summary.rooms_created) == 10 and len(summary.users_created) == 3
    assert summary.bookings_created == len(DEMO_BOOKINGS) >= 15

    rooms = (await session.scalars(select(Room))).all()
    assert sum(r.room_type == RoomType.VIDEO for r in rooms) == 4
    assert sum(r.room_type == RoomType.NORMAL for r in rooms) == 6
    assert "Sala 01 - Executiva" in {r.name for r in rooms}
    assert all(r.capacity > 0 for r in rooms)

    bookings = (await session.scalars(select(Booking))).all()
    assert all(MONDAY <= b.start_time.date() <= date(2026, 10, 31) for b in bookings)
    assert any(b.period_type == PeriodType.FULL_DAY and b.is_full_day for b in bookings)
    video_ids = {r.id for r in rooms if r.room_type == RoomType.VIDEO}
    assert any(b.room_id in video_ids and not b.requires_video for b in bookings)  # remanejável
    assert sum(b.ticket_number is not None for b in bookings) > len(bookings) / 2
    assert await _count(session, BookingLog, BookingLog.action == LogAction.CREATED) == len(
        bookings
    )

    for username, password, role in [
        ("admin", "admin123", UserRole.ADMIN),
        ("recepcao1", "recepcao123", UserRole.RECEPTIONIST),
        ("recepcao2", "recepcao123", UserRole.RECEPTIONIST),
    ]:
        user = await users_service.authenticate(session, username, password)
        assert user.role == role


async def test_seed_demo_is_idempotent(session, admin):
    first = await seed_demo(session, today=MONDAY)
    assert first.users_created == ["recepcao1", "recepcao2"]
    again = await seed_demo(session, today=MONDAY)
    assert again.rooms_created == [] and again.users_created == [] and again.bookings_created == 0
    assert again.bookings_skipped == len(DEMO_BOOKINGS)
    assert await _count(session, Room) == 10
    assert await _count(session, Booking) == len(DEMO_BOOKINGS)
    assert await _count(session, User) == 3  # "admin" já existia (fixture) e foi reaproveitado


@pytest.mark.parametrize(
    ("today", "monday"),
    [
        (date(2026, 10, 26), MONDAY),  # segunda
        (date(2026, 10, 30), MONDAY),  # sexta: ainda a semana atual
        (date(2026, 10, 31), date(2026, 11, 2)),  # sábado: a próxima semana útil
        (date(2026, 11, 1), date(2026, 11, 2)),  # domingo
    ],
)
def test_demo_week_is_the_next_one_on_weekends(today, monday):
    assert demo_week_monday(today) == monday


async def test_seed_demo_on_a_saturday_fills_the_next_work_week(session):
    summary = await seed_demo(session, today=date(2026, 10, 31))  # sábado
    assert summary.week_start == date(2026, 11, 2)
    assert summary.bookings_created == len(DEMO_BOOKINGS)
    days = {to_local(b.start_time).date() for b in (await session.scalars(select(Booking))).all()}
    assert min(days) == date(2026, 11, 2) and max(days) == date(2026, 11, 6)


# ---------------------------------------------------------------- comandos


async def test_seed_demo_command_only_in_development(capsys, session, monkeypatch):
    assert await run_cli("seed-demo") == 1
    assert "AGENDA_ENV=development" in capsys.readouterr().err
    assert await _count(session, Room) == 0

    monkeypatch.setattr(settings, "env", "development")
    assert await run_cli("seed-demo") == 0
    out = capsys.readouterr().out
    assert "salas criadas: 10" in out and "recepcao1 / recepcao123" in out
    assert await _count(session, Room) == 10


async def test_create_user_command(capsys, session, monkeypatch):
    code = await run_cli(
        "create-user",
        "--username",
        "joana",
        "--name",
        "Joana Dias",
        "--role",
        "RECEPTIONIST",
        "--password",
        "segredo1",
    )
    assert code == 0, capsys.readouterr()
    assert 'Usuário "joana" (Joana Dias, recepcionista) criado.' in capsys.readouterr().out
    user = await users_service.authenticate(session, "joana", "segredo1")
    assert user.role == UserRole.RECEPTIONIST

    assert (
        await run_cli(
            "create-user",
            "--username",
            "JOANA",
            "--name",
            "Outra",
            "--role",
            "ADMIN",
            "--password",
            "segredo1",
        )
        == 1
    )
    assert "já existe" in capsys.readouterr().err

    assert (
        await run_cli(
            "create-user", "--username", "x", "--name", "Y", "--role", "ADMIN", "--password", "1"
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "usuário:" in err and "senha:" in err

    prompts = iter(["senha-secreta", "senha-secreta"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(prompts))
    assert (
        await run_cli("create-user", "--username", "chefe", "--name", "Chefe TI", "--role", "ADMIN")
        == 0
    )
    assert (
        await users_service.authenticate(session, "chefe", "senha-secreta")
    ).role == UserRole.ADMIN

    prompts = iter(["uma", "outra"])
    assert await run_cli("create-user", "--username", "z1", "--name", "Zé", "--role", "ADMIN") == 1
    assert "não conferem" in capsys.readouterr().err


async def test_init_db_bootstraps_admin_once(capsys, session, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_username", None)
    assert await run_cli("init-db") == 0
    assert "Nenhum usuário cadastrado" in capsys.readouterr().out
    assert await _count(session, User) == 0

    monkeypatch.setattr(settings, "bootstrap_admin_username", "root")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "root-pass-123")
    assert await run_cli("init-db") == 0
    assert 'Administrador inicial "root" criado' in capsys.readouterr().out
    assert (
        await users_service.authenticate(session, "root", "root-pass-123")
    ).role == UserRole.ADMIN

    assert await run_cli("init-db") == 0
    assert "já cadastrado" in capsys.readouterr().out
    assert await _count(session, User) == 1


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("admin123", "senha de exemplo ou de demonstração conhecida"),
        ("troque-no-primeiro-acesso", "senha de exemplo ou de demonstração conhecida"),
        ("Troque-Esta-Senha-Ja", "senha de exemplo ou de demonstração conhecida"),
        ("recepcao123", "senha de exemplo ou de demonstração conhecida"),
        ("curta-9chr", "pelo menos 12 caracteres em produção (tem 10)"),
        ("administrador", "senha de exemplo ou de demonstração conhecida"),
    ],
)
async def test_init_db_refuses_weak_bootstrap_password_in_production(
    capsys, session, monkeypatch, password, reason
):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", password)
    assert await run_cli("init-db") == 1
    err = capsys.readouterr().err
    assert "administrador inicial não criado" in err and reason in err, err
    assert "create-user" in err  # caminho alternativo, com a senha pedida no terminal
    assert await _count(session, User) == 0


async def test_init_db_accepts_strong_bootstrap_password_in_production(
    capsys, session, monkeypatch
):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "Cavalo-Bateria-Grampo-42")
    assert await run_cli("init-db") == 0
    assert 'Administrador inicial "admin" criado' in capsys.readouterr().out
    assert await _count(session, User) == 1
    assert bootstrap_password_problem("admin", "admin") is not None  # igual ao usuário/lista


async def test_reset_password_command_revokes_sessions(capsys, session, receptionist, monkeypatch):
    old_version = receptionist.token_version
    assert await run_cli("reset-password", "--username", "ANA", "--password", "nova-senha-1") == 0
    assert 'Senha de "ana" alterada' in capsys.readouterr().out
    await session.refresh(receptionist)
    assert receptionist.token_version == old_version + 1  # tokens antigos: SESSION_REVOKED
    user = await users_service.authenticate(session, "ana", "nova-senha-1")
    assert user.id == receptionist.id

    assert await run_cli("reset-password", "--username", "ninguem", "--password", "x" * 8) == 1
    assert 'usuário "ninguem" não encontrado' in capsys.readouterr().err
    assert await run_cli("reset-password", "--username", "ana", "--password", "123") == 1
    assert "no mínimo 6 caracteres" in capsys.readouterr().err

    prompts = iter(["outra-senha-2", "diferente"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(prompts))
    assert await run_cli("reset-password", "--username", "ana") == 1
    assert "não conferem" in capsys.readouterr().err


def test_parser_rejects_bad_arguments(capsys):
    with pytest.raises(SystemExit):
        cli.main(["create-user", "--username", "a", "--name", "b", "--role", "CHEFE"])
    with pytest.raises(SystemExit):
        cli.main(["export-xlsx", "--date", "26/10/2026"])
    with pytest.raises(SystemExit):
        cli.main([])


# ---------------------------------------------------------------- Excel (módulos falsos)


def _report(dry_run: bool) -> ImportReport:
    return ImportReport(
        dry_run=dry_run,
        sheets=[
            ImportSheetReport(
                sheet_name="Seg 26-10",
                date=MONDAY,
                status="DRY_RUN" if dry_run else "IMPORTED",
                bookings_found=3,
                bookings_imported=2,
                bookings_skipped=1,
                warnings=["Linha 12: conflito com reserva existente (pulada)"],
            ),
            ImportSheetReport(sheet_name="Resumo", status="SKIPPED_UNRECOGNIZED"),
        ],
        total_found=3,
        total_imported=2,
        total_skipped=1,
        unknown_rooms=["Sala 99"],
        errors=[],
    )


@pytest.fixture
def fake_importer(monkeypatch):
    calls: list[dict] = []

    async def import_workbook(
        session, source, *, user, dry_run, include_past, today=None, lock_store=None
    ):
        calls.append(
            {
                "source": source,
                "user": user.username,
                "dry_run": dry_run,
                "include_past": include_past,
            }
        )
        return _report(dry_run)

    module = types.ModuleType("app.excel.importer")
    module.import_workbook = import_workbook
    monkeypatch.setitem(sys.modules, "app.excel.importer", module)
    return calls


async def test_import_xlsx_command(capsys, fake_importer, admin, receptionist, tmp_path):
    xlsx = tmp_path / "planilha.xlsx"
    xlsx.write_bytes(b"PK-fake")
    assert await run_cli("import-xlsx", str(xlsx)) == 0
    out = capsys.readouterr().out
    assert fake_importer[-1] == {
        "source": b"PK-fake",
        "user": "admin",
        "dry_run": True,
        "include_past": False,
    }
    assert "SIMULAÇÃO DA IMPORTAÇÃO" in out
    assert 'Aba "Seg 26-10" (26/10/2026): simulada' in out
    assert "3 encontradas, 2 importáveis, 1 puladas" in out
    assert "aviso: Linha 12" in out
    assert 'Aba "Resumo": ignorada (data não reconhecida)' in out
    assert "Salas não encontradas no cadastro: Sala 99" in out
    assert "--commit" in out

    assert (
        await run_cli("import-xlsx", str(xlsx), "--commit", "--include-past", "--user", "ANA") == 0
    )
    out = capsys.readouterr().out
    assert fake_importer[-1]["dry_run"] is False and fake_importer[-1]["include_past"] is True
    assert fake_importer[-1]["user"] == "ana"
    assert "IMPORTAÇÃO CONCLUÍDA" in out and "--commit" not in out

    assert await run_cli("import-xlsx", str(xlsx), "--user", "ninguem") == 1
    assert "ninguem" in capsys.readouterr().err
    assert await run_cli("import-xlsx", str(tmp_path / "nao-existe.xlsx")) == 1
    assert "arquivo não encontrado" in capsys.readouterr().err


async def test_import_xlsx_requires_an_admin(capsys, fake_importer, receptionist, tmp_path):
    xlsx = tmp_path / "planilha.xlsx"
    xlsx.write_bytes(b"x")
    assert await run_cli("import-xlsx", str(xlsx)) == 1
    assert "nenhum administrador ativo" in capsys.readouterr().err
    assert fake_importer == []


async def test_import_report_with_errors_returns_failure(capsys, monkeypatch, admin, tmp_path):
    async def import_workbook(
        session, source, *, user, dry_run, include_past, today=None, lock_store=None
    ):
        return ImportReport(
            dry_run=dry_run,
            sheets=[],
            total_found=0,
            total_imported=0,
            total_skipped=0,
            errors=["Arquivo não é uma planilha .xlsx válida"],
        )

    module = types.ModuleType("app.excel.importer")
    module.import_workbook = import_workbook
    monkeypatch.setitem(sys.modules, "app.excel.importer", module)
    xlsx = tmp_path / "ruim.xlsx"
    xlsx.write_bytes(b"x")
    assert await run_cli("import-xlsx", str(xlsx)) == 1
    out = capsys.readouterr().out
    assert "Nenhuma aba encontrada" in out and "- Arquivo não é uma planilha" in out


async def test_export_xlsx_command(capsys, monkeypatch, tmp_path):
    calls: list[tuple[str, date]] = []

    async def export_day(session, d):
        calls.append(("day", d))
        return b"DAY"

    async def export_week(session, monday):
        calls.append(("week", monday))
        return b"WEEK"

    module = types.ModuleType("app.excel.exporter")
    module.export_day, module.export_week = export_day, export_week
    monkeypatch.setitem(sys.modules, "app.excel.exporter", module)

    out_file = tmp_path / "saida" / "dia.xlsx"
    assert await run_cli("export-xlsx", "--date", "2026-10-28", "-o", str(out_file)) == 0
    assert out_file.read_bytes() == b"DAY" and calls[-1] == ("day", date(2026, 10, 28))
    assert "Planilha gerada" in capsys.readouterr().out

    week_file = tmp_path / "semana.xlsx"
    assert (
        await run_cli("export-xlsx", "--date", "2026-10-28", "--week", "--output", str(week_file))
        == 0
    )
    assert week_file.read_bytes() == b"WEEK"
    assert calls[-1] == ("week", week_monday(date(2026, 10, 28))) == ("week", MONDAY)


def test_console_is_reconfigured_to_utf8(monkeypatch):
    class Stream:
        encoding = "cp1252"
        reconfigured: dict | None = None

        def reconfigure(self, **kwargs):
            self.reconfigured = kwargs

    out, err = Stream(), Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli._configure_console()
    assert out.reconfigured == {"encoding": "utf-8", "errors": "replace"}
    assert err.reconfigured == {"encoding": "utf-8", "errors": "replace"}
