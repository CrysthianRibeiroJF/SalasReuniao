"""Sobe a aplicação REAL (`app.main:app`) num processo próprio (testes multi-instância).

Mesmo protocolo de tests/realtime_server.py: imprime "READY port=<porta>", encerra de forma
graciosa quando a entrada padrão fecha e, ao final, imprime "LEFTOVER_TASKS=[...]".
Usa a configuração de logging padrão do uvicorn (a mesma de `uvicorn app.main:app`) no nível
AGENDA_TEST_LOG_LEVEL (padrão "warning").
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

import uvicorn

from tests.realtime_server import _serve


def main() -> None:
    config = uvicorn.Config(
        os.environ.get("AGENDA_TEST_APP", "app.main:app"),
        host="127.0.0.1",
        port=0,
        log_level=os.environ.get("AGENDA_TEST_LOG_LEVEL", "warning"),
        lifespan="on",
        ws="websockets-sansio",
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)

    def wait_for_stdin_eof() -> None:
        sys.stdin.read()
        server.should_exit = True

    threading.Thread(target=wait_for_stdin_eof, daemon=True).start()
    asyncio.run(_serve(server))


if __name__ == "__main__":
    main()
