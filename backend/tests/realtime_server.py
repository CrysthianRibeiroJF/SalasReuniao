"""Sobe tests.realtime_app num processo próprio (teste multi-instância).

Imprime "READY port=<porta>" quando aceita conexões. Fechar a entrada padrão dispara o shutdown
gracioso (lifespan) — no Windows não dá para mandar SIGINT/CTRL_BREAK a um processo sem console, e
assim o servidor também termina sozinho se o processo de testes morrer. Ao final imprime
"LEFTOVER_TASKS=[...]" com as tarefas asyncio que ainda estavam vivas (devem ser nenhuma).
"""

from __future__ import annotations

import asyncio
import sys
import threading

import uvicorn


async def _serve(server: uvicorn.Server) -> None:
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.02)
    if server.started:
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY port={port}", flush=True)
    await task
    leftover = sorted(
        t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task()
    )
    print(f"LEFTOVER_TASKS={leftover}", flush=True)


def main() -> None:
    config = uvicorn.Config(
        "tests.realtime_app:app",
        host="127.0.0.1",
        port=0,
        log_level="warning",
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
