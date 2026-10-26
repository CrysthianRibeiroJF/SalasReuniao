"""Middlewares ASGI puros (rodam antes do roteamento, sem ler o corpo da requisição).

- SecurityHeadersMiddleware: cabeçalhos de segurança em toda resposta HTTP e CSP no HTML da SPA.
- RequestGuardMiddleware: limita o tamanho do corpo de toda requisição e, no upload da planilha
  (POST /api/import/xlsx), confere o token de ADMIN ANTES de o corpo ser lido. O FastAPI lê (e o
  Starlette grava em disco) o multipart inteiro antes de resolver as dependências da rota, então
  sem isto qualquer um na rede poderia encher o disco do servidor com uploads anônimos.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.db import SessionLocal
from app.models import UserRole
from app.security import AuthFailure, auth_error_detail, check_token, forbidden_detail

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)
# Só para o HTML da SPA (o build do Vite não tem scripts inline nem eval).
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
# HTML do próprio backend (Swagger em /docs e /redoc usa CDN e script inline): sem CSP.
_NO_CSP_PREFIXES = ("/docs", "/redoc")

IMPORT_PATH = "/api/import/xlsx"
# Limite da rota (10 MB, conferido também em app/api/excel.py) + folga para o envelope multipart.
MAX_IMPORT_BODY_BYTES = 10 * 1024 * 1024 + 64 * 1024
# Demais rotas só recebem JSON pequeno (o maior campo, observações, tem 2.000 caracteres).
MAX_BODY_BYTES = 1024 * 1024


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        spa_html = not any(path == p or path.startswith(p + "/") for p in _NO_CSP_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                if spa_html and _is_html(headers) and b"content-security-policy" not in present:
                    headers.append((b"content-security-policy", CONTENT_SECURITY_POLICY.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _is_html(headers: list[tuple[bytes, bytes]]) -> bool:
    for name, value in headers:
        if name.lower() == b"content-type":
            return value.lower().startswith(b"text/html")
    return False


class _BodyTooLarge(Exception):
    pass


class RequestGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_BODY_BYTES,
        max_import_bytes: int = MAX_IMPORT_BODY_BYTES,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_import_bytes = max_import_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_import = scope["method"] == "POST" and scope["path"].rstrip("/") == IMPORT_PATH
        if is_import:
            refusal = await _check_admin(_bearer_token(scope))
            if refusal is not None:
                await _send_json(send, *refusal)
                return
        limit = self.max_import_bytes if is_import else self.max_body_bytes
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await _send_json(send, 413, _too_large(is_import))
            return
        await self._limited(scope, receive, send, limit, is_import)

    async def _limited(
        self, scope: Scope, receive: Receive, send: Send, limit: int, is_import: bool
    ) -> None:
        """Conta os bytes que chegam (corpo chunked ou Content-Length mentiroso)."""
        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                return  # a rota respondeu ao erro de leitura: vale o 413 enviado abaixo
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        except Exception:
            if not exceeded:
                raise
        if exceeded and not response_started:
            await _send_json(send, 413, _too_large(is_import))


async def _check_admin(token: str | None) -> tuple[int, dict[str, str], dict[str, str]] | None:
    """(status, detail, headers) da recusa, ou None se o token é de um ADMIN ativo."""
    async with SessionLocal() as session:
        result = await check_token(token, session)
    if result.user is None:
        failure = result.failure or AuthFailure.UNAUTHORIZED
        return 401, auth_error_detail(failure), {"WWW-Authenticate": "Bearer"}
    if result.user.role != UserRole.ADMIN:
        return 403, forbidden_detail(), {}
    return None


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _too_large(is_import: bool) -> dict[str, str]:
    if is_import:
        return {
            "code": "FILE_TOO_LARGE",
            "message": (
                "O arquivo passa do limite de 10 MB. Remova abas antigas da planilha e tente de "
                "novo."
            ),
        }
    return {"code": "REQUEST_TOO_LARGE", "message": "A requisição é grande demais."}


async def _send_json(
    send: Send, status: int, detail: dict[str, str], headers: dict[str, str] | None = None
) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False, separators=(",", ":")).encode()
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        # O corpo não foi lido: a conexão não pode ser reaproveitada.
        (b"connection", b"close"),
    ]
    raw_headers.extend((k.lower().encode(), v.encode()) for k, v in (headers or {}).items())
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})
