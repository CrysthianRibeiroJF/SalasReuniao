"""Apoio aos testes de robustez (test_hardening_*.py), complementando tests/realtime_support.py."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import jwt
from websockets.exceptions import ConnectionClosed

from app.config import settings
from app.models import User
from tests.realtime_support import BACKEND_DIR, ServerProcess, WsClient


class AppServerProcess(ServerProcess):
    """Instância da aplicação real (tests/hardening_server.py) em processo separado."""

    ready_path = "/api/health"  # também abre a primeira conexão com o banco

    def launch(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-W", "default", "-m", "tests.hardening_server"],
            cwd=BACKEND_DIR,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()


def assert_clean_shutdown(proc: ServerProcess, output: str) -> None:
    assert proc.returncode == 0, output
    assert "LEFTOVER_TASKS=[]" in output, output
    for marker in ("Traceback", "Task was destroyed", "ResourceWarning", "ERROR"):
        assert marker not in output, output


def token_expiring_in(user: User, seconds: float) -> str:
    """JWT igual ao de create_access_token, mas vencendo em `seconds`."""
    exp = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    payload = {
        "sub": str(user.id),
        "name": user.full_name,
        "role": str(user.role),
        "tv": int(user.token_version or 0),
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def closed_with(client: WsClient, timeout: float = 5.0) -> tuple[int, str]:
    """Consome o socket até o servidor fechá-lo; devolve (código, motivo) do fechamento."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while (remaining := deadline - loop.time()) > 0:
            await client._recv(remaining)
    except ConnectionClosed as exc:
        assert exc.rcvd is not None, "conexão caiu sem frame de fechamento do servidor"
        return exc.rcvd.code, exc.rcvd.reason
    except TimeoutError:
        pass
    seen = [m.get("type") for m in client.received]
    raise AssertionError(f"o servidor não fechou o socket em {timeout}s; recebidas: {seen}")


async def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await _maybe_await(predicate()):
        if loop.time() > deadline:
            raise AssertionError("condição não satisfeita a tempo")
        await asyncio.sleep(interval)


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
