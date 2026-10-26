"""O JWT de /ws?token=... nunca chega aos logs do uvicorn (acesso e "WebSocket ... [accepted]")."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager

import pytest
import uvicorn
from uvicorn.logging import AccessFormatter

from app.logging_config import configure_app_logging
from app.main import create_app
from app.realtime.log_redaction import (
    UVICORN_LOGGERS,
    TokenRedactingFilter,
    install_token_redaction,
    redact_token,
)
from tests.hardening_support import AppServerProcess
from tests.realtime_support import WsClient, token_for

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2VncmVkbw"


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]


@pytest.fixture
def capture():
    """Handler direto nos loggers do uvicorn (o filtro de logger roda antes dos handlers)."""
    handler = Capture()
    loggers = [logging.getLogger(name) for name in UVICORN_LOGGERS]
    levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
    yield handler
    for lg, level in zip(loggers, levels):
        lg.removeHandler(handler)
        lg.setLevel(level)


def ours(logger: logging.Logger) -> list[logging.Filter]:
    return [f for f in logger.filters if isinstance(f, TokenRedactingFilter)]


def test_redact_token_keeps_everything_else():
    assert redact_token(f"/ws?token={JWT}") == "/ws?token=***"
    assert redact_token(f"/ws?x=1&token={JWT}&y=2") == "/ws?x=1&token=***&y=2"
    assert redact_token(f'"GET /ws?TOKEN={JWT} HTTP/1.1"') == '"GET /ws?TOKEN=*** HTTP/1.1"'
    assert redact_token("/api/bookings?date=2026-10-26") == "/api/bookings?date=2026-10-26"
    assert redact_token("/ws?mytoken=abc") == "/ws?mytoken=abc"  # só o parâmetro token
    assert redact_token("/ws?token=") == "/ws?token="


def test_create_app_installs_the_filter_once():
    create_app()
    create_app()
    install_token_redaction()
    for name in UVICORN_LOGGERS:
        assert len(ours(logging.getLogger(name))) == 1, name


def test_access_log_args_and_formatted_line_are_redacted(capture):
    create_app()
    access = logging.getLogger("uvicorn.access")
    # exatamente a chamada do h11_impl/httptools_impl do uvicorn
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/ws?token={JWT}", "1.1", 404)
    access.info(f"cliente pediu /ws?token={JWT}")  # token já formatado na mensagem
    first, second = capture.records
    assert first.args[2] == "/ws?token=***"
    assert len(first.args) == 5  # o AccessFormatter desempacota a tupla
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(first)
    assert line.startswith('127.0.0.1:5000 - "GET /ws?token=*** HTTP/1.1" 404'), line
    assert second.getMessage() == "cliente pediu /ws?token=***"
    assert all(JWT not in message for message in capture.messages())


def test_mapping_args_are_redacted(capture):
    create_app()
    logging.getLogger("uvicorn.error").info("caminho %(path)s", {"path": f"/ws?token={JWT}"})
    assert capture.messages() == ["caminho /ws?token=***"]


@asynccontextmanager
async def serve_real_app_with_info_logs():
    """A aplicação real num uvicorn com log INFO (o "[accepted]" do WebSocket é INFO)."""
    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=0, log_config=None, log_level="info",
        lifespan="on", ws="websockets-sansio",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="test:uvicorn")
    try:
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("uvicorn não iniciou")
            await asyncio.sleep(0.01)
        yield f"127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 20)


async def test_websocket_handshake_log_has_no_token(capture, receptionist):
    token = token_for(receptionist)
    async with serve_real_app_with_info_logs() as base:
        client = await WsClient.connect(base, token)
        await client.close()
        refused = await WsClient.connect(base, "token-invalido", hello=False)
        await refused.close()
    messages = capture.messages()
    accepted = [m for m in messages if "WebSocket /ws" in m]
    assert any(m.endswith('"WebSocket /ws?token=***" [accepted]') for m in accepted), messages
    assert all(token not in m and "token-invalido" not in m for m in messages), messages


# ---------------------------------------------------------------- logs INFO de "app.*" (R53)


@contextmanager
def bare_logging():
    """Estado de um processo novo: nenhum handler no root nem em "app" (como no uvicorn). Usado
    dentro do teste: o pytest põe os handlers de captura no root só quando o teste começa."""
    root, app_logger = logging.getLogger(), logging.getLogger("app")
    uvicorn_error = logging.getLogger("uvicorn.error")  # outros testes sobem uvicorn em "warning"
    saved = (root.handlers[:], app_logger.handlers[:], app_logger.level, uvicorn_error.level)
    root.handlers.clear()
    app_logger.handlers.clear()
    app_logger.setLevel(logging.NOTSET)
    uvicorn_error.setLevel(logging.NOTSET)
    try:
        yield app_logger
    finally:
        root.handlers[:] = saved[0]
        app_logger.handlers[:] = saved[1]
        app_logger.setLevel(saved[2])
        uvicorn_error.setLevel(saved[3])


def test_app_logging_gets_one_handler_when_nobody_configured_it():
    with bare_logging() as app_logger:
        configure_app_logging()
        configure_app_logging()  # create_app pode rodar mais de uma vez
        handlers = app_logger.handlers[:]
        level = app_logger.level
    assert len(handlers) == 1
    assert level == logging.INFO
    record = logging.LogRecord("app.realtime.setup", logging.INFO, "x", 1, "Tempo real", (), None)
    assert handlers[0].format(record).endswith(" INFO app.realtime.setup: Tempo real")


def test_app_logging_respects_an_existing_configuration():
    with bare_logging() as app_logger:
        logging.getLogger().addHandler(logging.NullHandler())
        configure_app_logging()
        assert app_logger.handlers == []  # o root já escreve: sem linhas duplicadas


def test_app_logging_follows_uvicorn_log_level():
    with bare_logging() as app_logger:
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)  # --log-level warning
        configure_app_logging()
        assert app_logger.level == logging.WARNING


def test_startup_line_with_instance_id_appears_once_with_uvicorn_default_logging():
    """O processo real (logging padrão do uvicorn, --log-level info): a linha de início do tempo
    real, com a instância, aparece uma vez e com horário, nível e origem."""
    proc = AppServerProcess("logs", {"AGENDA_INSTANCE_ID": "inst-log", "AGENDA_TEST_LOG_LEVEL":
                                     "info"})
    proc.start()
    output = proc.stop()
    started = [
        line for line in output.splitlines()
        if "Tempo real iniciado (backend memory, instância inst-log)" in line
    ]
    assert len(started) == 1, output
    assert " INFO app.realtime.setup: " in started[0]
    assert "Tempo real encerrado (instância inst-log)" in output
