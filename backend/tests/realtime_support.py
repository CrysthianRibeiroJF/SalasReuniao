"""Utilitários dos testes de tempo real: Redis falso via TCP, servidor uvicorn e cliente WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
import threading
import time as clock
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import uvicorn
from fakeredis._clients._tcp_server import TCPFakeRequestHandler
from websockets.asyncio.client import ClientConnection, connect

from app.models import User
from app.security import create_access_token

BACKEND_DIR = Path(__file__).resolve().parent.parent

# O handshake do /ws inclui a autenticação no banco, e nos testes (NullPool) cada uma abre uma
# conexão nova com o PostgreSQL: sob carga (grupos de testes em paralelo) isso passa de segundos.
# Os prazos são generosos porque só limitam falhas; o caminho normal termina bem antes.
WS_OPEN_TIMEOUT = 30.0
HELLO_TIMEOUT = 15.0


# ---------------------------------------------------------------------- Redis falso (TCP)


class _PollingHandler(TCPFakeRequestHandler):
    """Handler do TcpFakeServer que funciona no Windows.

    O original deixa o socket em modo não bloqueante via fcntl, que não existe no Windows: lá ele
    fica preso em readline() e mensagens de pub/sub nunca são empurradas ao assinante. Aqui o
    socket é consultado com select() e as respostas pendentes são despachadas a cada volta.
    """

    def handle(self) -> None:
        while not self.server._shutdown_event.is_set():
            try:
                if self.current_client.can_read():
                    self.writer.dump(self.current_client.read_response())
                    continue
                readable, _, _ = select.select([self.connection], [], [], 0.005)
                if not readable:
                    continue
                data = self.connection.recv(65536)
                if not data:
                    break
                self.current_client.get_socket().sendall(data)
            except OSError:
                break
            except Exception as exc:  # erro de protocolo: devolve ao cliente e encerra
                try:
                    self.writer.dump(exc)
                except OSError:
                    pass
                break


class FakeRedisTcp:
    """fakeredis.TcpFakeServer numa thread, acessível por redis://127.0.0.1:<port>."""

    def __init__(self, port: int = 0) -> None:
        self.port = port
        self._server: fakeredis.TcpFakeServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/0"

    def start(self) -> FakeRedisTcp:
        server = fakeredis.TcpFakeServer(("127.0.0.1", self.port))
        server.RequestHandlerClass = _PollingHandler
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> FakeRedisTcp:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ---------------------------------------------------------------------- autenticação


def token_for(user: User) -> str:
    return create_access_token(user)[0]


# ---------------------------------------------------------------------- cliente WebSocket


class WsClient:
    """Cliente de teste: guarda as mensagens não consumidas para `expect` posteriores."""

    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.received: list[dict[str, Any]] = []
        self._pending: deque[dict[str, Any]] = deque()
        self.hello: dict[str, Any] | None = None

    @classmethod
    async def connect(
        cls,
        base: str,
        token: str | None,
        *,
        hello: bool = True,
        open_timeout: float = WS_OPEN_TIMEOUT,
    ) -> WsClient:
        url = f"ws://{base}/ws" + (f"?token={token}" if token is not None else "")
        ws = await connect(
            url, proxy=None, open_timeout=open_timeout, ping_interval=None, close_timeout=2
        )
        client = cls(ws)
        if hello:
            client.hello = await client.expect("HELLO", timeout=HELLO_TIMEOUT)
        return client

    async def send(
        self, msg_type: str, data: dict[str, Any] | None = None, request_id: str | None = None
    ) -> None:
        envelope: dict[str, Any] = {"type": msg_type, "data": data or {}}
        if request_id is not None:
            envelope["request_id"] = request_id
        await self.ws.send(json.dumps(envelope))

    async def send_raw(self, payload: str | bytes) -> None:
        await self.ws.send(payload)

    async def expect(
        self,
        msg_type: str,
        *,
        timeout: float = 3.0,
        where: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        def matches(msg: dict[str, Any]) -> bool:
            return msg.get("type") == msg_type and (where is None or where(msg))

        for msg in list(self._pending):
            if matches(msg):
                self._pending.remove(msg)
                return msg
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                msg = await self._recv(remaining)
            except TimeoutError:
                break
            if matches(msg):
                return msg
            self._pending.append(msg)
        seen = [m.get("type") for m in self.received]
        raise AssertionError(f"{msg_type} não chegou em {timeout}s; recebidas: {seen}")

    async def assert_silent(
        self,
        msg_type: str,
        *,
        within: float,
        where: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        try:
            msg = await self.expect(msg_type, timeout=within, where=where)
        except AssertionError:
            return
        raise AssertionError(f"Mensagem inesperada: {msg}")

    async def count(
        self, msg_type: str, *, within: float, where: Callable[[dict[str, Any]], bool] | None = None
    ) -> int:
        """Consome por `within` segundos e conta as mensagens que casam."""
        found = 0
        while True:
            try:
                await self.expect(msg_type, timeout=within, where=where)
            except AssertionError:
                return found
            found += 1

    async def close(self) -> None:
        await self.ws.close()

    async def _recv(self, timeout: float) -> dict[str, Any]:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        msg = json.loads(raw)
        self.received.append(msg)
        return msg


def lock_data(room_id: int, day: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {"room_id": room_id, "date": day, "start": start, "end": end, **extra}


# ---------------------------------------------------------------------- servidores


@asynccontextmanager
async def serve_app(app: Any) -> AsyncIterator[str]:
    """uvicorn real no loop atual (porta livre do SO). Rende "127.0.0.1:<porta>"."""
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_config=None,
        log_level="warning",
        lifespan="on",
        ws="websockets-sansio",
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="test:uvicorn")
    try:
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("uvicorn não iniciou (falha no lifespan?)")
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 20)


def http_client(base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://{base}", trust_env=False, timeout=10)


class ServerProcess:
    """Instância separada (tests/realtime_server.py). Encerra de forma graciosa ao fechar o stdin.

    Pronta = imprimiu "READY port=" E respondeu 200 em `ready_path` (a porta aberta não garante
    que a primeira requisição seja atendida a tempo com a máquina carregada).
    """

    ready_path = "/test/state"

    def __init__(self, name: str, env: dict[str, str]) -> None:
        self.name = name
        self.env = {**os.environ, **env, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.port: int | None = None
        self.lines: list[str] = []
        self._ready = threading.Event()
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None

    @property
    def base(self) -> str:
        assert self.port is not None, "servidor não iniciado"
        return f"127.0.0.1:{self.port}"

    @property
    def output(self) -> str:
        return "".join(self.lines)

    def start(self, timeout: float = 60) -> ServerProcess:
        self.launch()
        return self.wait_ready(timeout)

    @staticmethod
    def start_all(processes: list[ServerProcess], timeout: float = 60) -> None:
        """Sobe várias instâncias em paralelo (cada uma leva alguns segundos para importar)."""
        try:
            for proc in processes:
                proc.launch()
            for proc in processes:
                proc.wait_ready(timeout)
        except BaseException:
            for proc in processes:
                proc.stop(timeout=5)
            raise

    def launch(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-W", "default", "-m", "tests.realtime_server"],
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

    def wait_ready(self, timeout: float = 60) -> ServerProcess:
        deadline = clock.monotonic() + timeout
        if not self._ready.wait(timeout) or self.port is None:
            self.stop(timeout=5)
            raise RuntimeError(f"Instância {self.name} não subiu:\n{self.output}")
        if not self._wait_http(deadline):
            self.stop(timeout=5)
            raise RuntimeError(
                f"Instância {self.name} não respondeu em {self.ready_path}:\n{self.output}"
            )
        return self

    def _wait_http(self, deadline: float) -> bool:
        with httpx.Client(base_url=f"http://{self.base}", trust_env=False, timeout=10) as client:
            while clock.monotonic() < deadline:
                if self.returncode is not None:
                    return False
                try:
                    if client.get(self.ready_path).status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                clock.sleep(0.1)
        return False

    def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self.lines.append(line)
            if line.startswith("READY port="):
                self.port = int(line.split("=", 1)[1])
                self._ready.set()
        self._ready.set()  # processo terminou

    def stop(self, timeout: float = 20) -> str:
        """Shutdown gracioso (lifespan). Mata o processo se não terminar a tempo."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                assert proc.stdin is not None
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(10)
        if self._reader is not None:
            self._reader.join(timeout=5)
        return self.output

    def kill(self) -> None:
        """Morte abrupta (sem lifespan): simula uma instância que caiu."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(10)
        if self._reader is not None:
            self._reader.join(timeout=5)

    @property
    def returncode(self) -> int | None:
        return None if self._proc is None else self._proc.poll()
