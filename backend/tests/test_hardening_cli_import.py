"""`python -m app.cli import-xlsx --commit` avisa as instâncias pelo Redis (BOOKINGS_IMPORTED).

A CLI roda numa thread (asyncio.run próprio), como em tests/test_excel_cli.py; o servidor de teste
e o Redis falso (TCP) rodam neste processo.
"""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app import cli
from app.config import settings
from app.models import Booking
from app.realtime.bus import CHANNEL
from app.timeutil import today_local
from tests.excel_builders import HEADERS, legacy, new_workbook
from tests.realtime_support import FakeRedisTcp
from tests.test_realtime_ws import run_server

pytest_plugins = ("tests.excel_fixtures",)


async def run_cli(*argv: str) -> int:
    return await asyncio.to_thread(cli.main, list(argv))


@pytest.fixture
def workbook(tmp_path):
    """Planilha legada com duas reservas em dois dias futuros."""
    first, second = today_local() + timedelta(days=4), today_local() + timedelta(days=5)
    wb = new_workbook()
    legacy(wb, f"{first:%d.%m.%Y}", HEADERS).put(
        "Sala 01 - Executiva", "09:00", "10:00", "Chamado 48211 - Carla - Diretoria"
    )
    legacy(wb, f"{second:%d.%m.%Y}", HEADERS).put(
        "Sala 05 - Reunião", "14:00", "15:00", "#48212 - Bruno - Financeiro"
    )
    path = tmp_path / "planilha legada.xlsx"
    wb.save(path)
    return path, sorted([first.isoformat(), second.isoformat()])


async def bookings(session) -> int:
    return await session.scalar(select(func.count()).select_from(Booking))


async def test_commit_reaches_open_browsers_through_redis(
    capsys, monkeypatch, admin, receptionist, rooms, workbook, session
):
    path, dates = workbook
    with FakeRedisTcp() as fake:
        monkeypatch.setattr(settings, "redis_url", fake.url)
        raw = Redis.from_url(fake.url, decode_responses=True, protocol=2)
        wire = raw.pubsub(ignore_subscribe_messages=True)
        await wire.subscribe(CHANNEL)
        try:
            async with run_server() as server:
                assert server.runtime.backend == "redis"
                browser = await server.connect(receptionist)

                # a simulação não grava nem avisa ninguém
                assert await run_cli("import-xlsx", str(path)) == 0
                assert "Servidor avisado" not in capsys.readouterr().out
                await browser.assert_silent("BOOKINGS_IMPORTED", within=0.3)

                assert await run_cli("import-xlsx", str(path), "--commit") == 0
                out = capsys.readouterr().out
                assert "IMPORTAÇÃO CONCLUÍDA" in out
                assert "Servidor avisado (" in out  # o servidor e o ouvinte deste teste
                event = await browser.expect("BOOKINGS_IMPORTED", timeout=5)
                assert event["data"] == {"dates": dates, "count": 2}
                assert await bookings(session) == 2

            # no canal, o formato do RedisBus com uma origem que nenhuma instância descarta
            message = await _next_message(wire)
            body = json.loads(message["data"])
            assert body["origin"].startswith("cli:")
            assert body["envelope"]["type"] == "BOOKINGS_IMPORTED"
            assert body["envelope"]["data"] == {"dates": dates, "count": 2}
        finally:
            await wire.aclose()
            await raw.aclose()


async def _next_message(pubsub) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if message is not None and json.loads(message["data"])["envelope"]["type"] != "PRESENCE":
            return message
    raise AssertionError("nenhuma mensagem no canal")


async def test_without_redis_the_command_explains_what_to_do(capsys, admin, rooms, workbook):
    path, _ = workbook
    assert settings.redis_url is None
    assert await run_cli("import-xlsx", str(path), "--commit") == 0
    out = capsys.readouterr().out
    assert "IMPORTAÇÃO CONCLUÍDA" in out
    assert "sem Redis configurado (AGENDA_REDIS_URL)" in out
    assert "recarregar a página (F5)" in out
    assert "Administração › Importar planilha" in out

    # nada importado (tudo já existe): nenhum evento, nenhuma observação
    assert await run_cli("import-xlsx", str(path), "--commit") == 0
    assert "AGENDA_REDIS_URL" not in capsys.readouterr().out


async def test_unreachable_redis_only_warns(capsys, monkeypatch, admin, rooms, workbook, session):
    path, _ = workbook
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(settings, "redis_url", f"redis://:segredo@127.0.0.1:{port}/0")
    assert await run_cli("import-xlsx", str(path), "--commit") == 0  # as reservas foram gravadas
    captured = capsys.readouterr()
    assert "IMPORTAÇÃO CONCLUÍDA" in captured.out
    assert "Aviso: as reservas foram gravadas, mas não foi possível avisar" in captured.err
    assert f"redis://:***@127.0.0.1:{port}/0" in captured.err
    assert "segredo" not in captured.err + captured.out
    assert await bookings(session) == 2


async def test_no_instance_listening(capsys, monkeypatch, admin, rooms, workbook):
    path, _ = workbook
    with FakeRedisTcp() as fake:
        monkeypatch.setattr(settings, "redis_url", fake.url)
        assert await run_cli("import-xlsx", str(path), "--commit") == 0
    assert "Nenhuma instância do servidor está ouvindo o Redis agora" in capsys.readouterr().out
