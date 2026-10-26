"""Aplicação FastAPI: REST /api/*, WebSocket /ws e, opcionalmente, a SPA do frontend em "/".

Execução local:  uvicorn app.main:app --reload --port 8000   (de dentro de backend/)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import audit, auth, availability, bookings, config, health, rooms, users
from app.api.excel import router as excel_router
from app.config import settings
from app.db import engine
from app.logging_config import configure_app_logging
from app.middleware import RequestGuardMiddleware, SecurityHeadersMiddleware
from app.realtime.log_redaction import install_token_redaction
from app.realtime.setup import router as ws_router
from app.realtime.setup import shutdown as realtime_shutdown
from app.realtime.setup import startup as realtime_startup
from app.services.errors import BusinessError
from app.services.login_throttle import LoginThrottle, MemoryThrottleStore, build_login_throttle

log = logging.getLogger(__name__)

# Prefixos que pertencem ao backend: um 404 neles nunca vira index.html.
_BACKEND_PREFIXES = ("/api", "/ws", "/docs", "/redoc", "/openapi.json")


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(exc.detail)},
        headers=exc.headers,
    )


# Valor que o PostgreSQL não aceita (ex.: caractere NUL num texto, número fora do INTEGER) e que a
# validação dos schemas deixou passar: é erro de quem enviou (422), não do servidor (500).
_DATA_ERROR_SQLSTATE_CLASS = "22"


async def data_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
        getattr(exc.orig, "__cause__", None), "sqlstate", None
    )
    if not (isinstance(sqlstate, str) and sqlstate.startswith(_DATA_ERROR_SQLSTATE_CLASS)):
        raise exc
    log.warning("Dado recusado pelo banco (%s) em %s %s", sqlstate, request.method,
                request.url.path)
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "INVALID_INPUT", "message": "Algum valor enviado é inválido."}},
    )


class AgendaApp(FastAPI):
    def build_middleware_stack(self):
        # Por fora de tudo (inclusive do ServerErrorMiddleware): até um 500 leva os cabeçalhos.
        return SecurityHeadersMiddleware(super().build_middleware_stack())


def create_app(*, frontend_dist: str | Path | None = None) -> FastAPI:
    """`frontend_dist` sobrescreve settings.frontend_dist (pasta do build do frontend)."""
    install_token_redaction()  # /ws?token=<jwt> nunca aparece nos logs do uvicorn
    configure_app_logging()  # INFO de "app.*" visível com o logging padrão do uvicorn

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Repetido no startup (idempotente), caso o servidor tenha refeito os filtros dos seus
        # loggers depois do import do app.
        install_token_redaction()
        for warning in settings.production_warnings():
            log.warning(warning)
        app.state.login_throttle = build_login_throttle()
        await realtime_startup(app)
        try:
            yield
        finally:
            await realtime_shutdown(app)
            await app.state.login_throttle.aclose()
            await engine.dispose()

    app = AgendaApp(
        title="Agenda de Salas",
        description="Agendamento de salas em tempo real (REST /api, WebSocket /ws).",
        version="1.0.0",
        lifespan=lifespan,
    )
    # Sem lifespan (ex.: testes que só montam a app) o limite de login fica na memória.
    app.state.login_throttle = LoginThrottle(MemoryThrottleStore())
    # Limite de corpo e guarda do upload: por dentro do CORS (as recusas também levam os
    # cabeçalhos de CORS) e antes de qualquer leitura do corpo.
    app.add_middleware(RequestGuardMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # downloads .xlsx: o frontend lê o nome do arquivo
        expose_headers=["Content-Disposition"],
    )
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(DBAPIError, data_error_handler)

    for module in (auth, users, rooms, bookings, availability, audit, config, health):
        app.include_router(module.router)
    app.include_router(excel_router)
    app.include_router(ws_router)

    dist = frontend_dist if frontend_dist is not None else settings.frontend_dist
    if dist:
        dist_path = Path(dist).resolve()
        if (dist_path / "index.html").is_file():
            _install_spa(app, dist_path)
        else:
            log.warning("frontend_dist=%s não contém index.html; SPA não será servida", dist_path)
    return app


def _install_spa(app: FastAPI, dist: Path) -> None:
    """Serve o build do Vite: /assets/* com cache longo e index.html para as rotas da SPA.

    Implementado como tratamento de 404 (e não como rota curinga) para não transformar um
    POST /api/inexistente em 405 nem esconder os 404 da API.
    """
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", _ImmutableStaticFiles(directory=assets), name="spa-assets")

    async def spa_fallback(request: Request, exc: StarletteHTTPException) -> Response:
        path = request.url.path
        if (
            exc.status_code == 404
            and request.method in ("GET", "HEAD")
            and not _is_backend_path(path)
            and not path.startswith("/assets/")
        ):
            file = _static_file(dist, path)
            if file is not None:
                return FileResponse(file)
            if not Path(path).suffix:  # rota da SPA (sem extensão de arquivo)
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return await http_exception_handler(request, exc)

    app.add_exception_handler(StarletteHTTPException, spa_fallback)


class _ImmutableStaticFiles(StaticFiles):
    # Arquivos de /assets têm hash no nome: podem ficar em cache "para sempre".
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response


def _is_backend_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _BACKEND_PREFIXES)


def _static_file(dist: Path, path: str) -> Path | None:
    """Arquivo da raiz do build (favicon, robots.txt...), sem permitir sair da pasta."""
    relative = path.lstrip("/")
    if not relative:
        return None
    candidate = (dist / relative).resolve()
    if candidate.is_relative_to(dist) and candidate.is_file():
        return candidate
    return None


app = create_app()
