"""App mínima para testar o tempo real sem depender de app.main (núcleo REST).

Expõe /ws (app.realtime.setup.router) e rotas só de teste:
    POST /test/publish  {type, data}  -> events.publish() (simula um serviço após o COMMIT)
    GET  /test/state                  -> travas ativas, conexões locais e presença agregada

O módulo também cria `app` a partir de variáveis de ambiente para o processo separado
(tests/realtime_server.py): AGENDA_TEST_SWEEP_INTERVAL, AGENDA_TEST_HEARTBEAT_INTERVAL,
AGENDA_TEST_PRESENCE_TTL (segundos).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import settings
from app.realtime import events, registry, setup


class PublishIn(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


def create_test_app(**startup_options: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await setup.startup(application, **startup_options)
        try:
            yield
        finally:
            await setup.shutdown(application)

    application = FastAPI(lifespan=lifespan)
    application.include_router(setup.router)

    @application.post("/test/publish")
    async def publish(body: PublishIn) -> dict[str, bool]:
        await events.publish(body.type, body.data)
        return {"ok": True}

    @application.get("/test/state")
    async def state() -> dict[str, Any]:
        runtime = application.state.realtime
        locks = await registry.get_lock_store().list_active()
        return {
            "instance_id": settings.instance_id,
            "backend": runtime.backend,
            "connections": len(runtime.hub),
            "locks": [lock.to_out().model_dump(mode="json") for lock in locks],
            "online": [user.model_dump(mode="json") for user in await runtime.presence.online()],
        }

    return application


def _env_float(name: str) -> float | None:
    value = os.environ.get(name)
    return float(value) if value else None


app = create_test_app(
    sweep_interval=_env_float("AGENDA_TEST_SWEEP_INTERVAL"),
    heartbeat_interval=_env_float("AGENDA_TEST_HEARTBEAT_INTERVAL"),
    presence_ttl=_env_float("AGENDA_TEST_PRESENCE_TTL"),
)
