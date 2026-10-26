"""Cabeçalhos de segurança (D7), limite de corpo e guarda do upload da planilha (D8) e o 422 para
valores que o PostgreSQL recusa."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import httpx
import pytest
from sqlalchemy import text

from app.main import create_app
from app.middleware import CONTENT_SECURITY_POLICY, MAX_BODY_BYTES, MAX_IMPORT_BODY_BYTES
from app.models import User, UserRole
from tests.realtime_support import serve_app

SECURITY = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def assert_security_headers(resp: httpx.Response) -> None:
    for name, value in SECURITY.items():
        assert resp.headers.get(name) == value, (resp.request.url, name, dict(resp.headers))


# ---------------------------------------------------------------- cabeçalhos de segurança


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Agenda</title>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc.js").write_text("console.log('ok')", encoding="utf-8")
    return tmp_path


@pytest.fixture
async def spa_app(dist):
    application = create_app(frontend_dist=dist)

    @application.get("/api/test-boom")
    async def boom() -> None:
        raise RuntimeError("falha inesperada")

    async with application.router.lifespan_context(application):
        yield application


def asgi_client(application, **kwargs) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False, **kwargs)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_every_response_has_security_headers(spa_app, auth, receptionist):
    async with asgi_client(spa_app) as c:
        responses = [
            await c.get("/api/health"),
            await c.get("/api/config", headers=auth(receptionist)),
            await c.get("/api/config"),  # 401
            await c.get("/api/nao-existe"),  # 404 da API
            await c.post("/api/auth/login", json={}),  # 422
            await c.get("/assets/index-abc.js"),
            await c.get("/api/test-boom"),  # 500 do ServerErrorMiddleware
            await c.get("/"),
            await c.get("/docs"),
        ]
    assert [r.status_code for r in responses] == [200, 200, 401, 404, 422, 200, 500, 200, 200]
    for resp in responses:
        assert_security_headers(resp)


async def test_csp_only_on_the_spa_html(spa_app):
    async with asgi_client(spa_app) as c:
        for path in ("/", "/grade", "/admin/salas"):
            resp = await c.get(path)
            assert resp.headers["content-type"].startswith("text/html")
            assert resp.headers["content-security-policy"] == CONTENT_SECURITY_POLICY, path
        assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
        assert "script-src 'self';" in CONTENT_SECURITY_POLICY  # sem inline nem eval
        assert "connect-src 'self' ws: wss:" in CONTENT_SECURITY_POLICY
        # JSON, JS e o Swagger (que usa CDN e script inline) ficam sem CSP
        for path in ("/api/health", "/assets/index-abc.js", "/docs"):
            resp = await c.get(path)
            assert "content-security-policy" not in resp.headers, path


async def test_websocket_still_works_through_the_middlewares(receptionist):
    from tests.realtime_support import WsClient, token_for

    async with serve_app(create_app()) as base:
        client = await WsClient.connect(base, token_for(receptionist))
        await client.send("PING")
        await client.expect("PONG")
        await client.close()


# ---------------------------------------------------------------- guarda do upload (ASGI direto)


class Body:
    """`receive` ASGI que conta quanto do corpo a aplicação leu."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.read = 0
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if not self.chunks:
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = self.chunks.pop(0)
        self.read += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(self.chunks)}


async def call_asgi(application, path: str, headers: dict[str, str], body: Body,
                    method: str = "POST"):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
        "state": {},
    }
    await application(scope, body, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), json.loads(raw) if raw else None


@pytest.fixture
async def guarded_app():
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


MULTIPART = "multipart/form-data; boundary=xyz"
CRLF = bytes([13, 10])
HUGE = str(50 * 1024 * 1024 * 1024)  # 50 GB anunciados


async def test_import_refuses_anonymous_and_non_admin_before_reading_the_body(
    guarded_app, auth, receptionist, make_user, session
):
    inactive_admin = await make_user("velho", "Admin Antigo", role=UserRole.ADMIN)
    inactive_headers = auth(inactive_admin)
    inactive_admin.is_active = False
    await session.commit()
    cases = [
        ({}, 401, "UNAUTHORIZED"),
        ({"authorization": "Bearer lixo"}, 401, "UNAUTHORIZED"),
        ({"authorization": "Basic YWRtaW46YWRtaW4="}, 401, "UNAUTHORIZED"),
        (inactive_headers, 401, "ACCOUNT_DISABLED"),
        (auth(receptionist), 403, "FORBIDDEN"),
    ]
    for headers, status, code in cases:
        body = Body([b"x" * 1024] * 4)
        all_headers = {"content-type": MULTIPART, "content-length": HUGE, **headers}
        got_status, got_headers, payload = await call_asgi(
            guarded_app, "/api/import/xlsx", all_headers, body
        )
        assert (got_status, payload["detail"]["code"]) == (status, code), headers
        assert body.calls == 0, "o corpo não pode ser lido antes da autenticação"
        assert got_headers[b"x-frame-options"] == b"DENY"
        if status == 401:
            assert got_headers[b"www-authenticate"] == b"Bearer"


async def test_import_rejects_declared_oversize_without_reading(guarded_app, auth, admin):
    body = Body([b"x" * 1024])
    headers = {**auth(admin), "content-type": MULTIPART,
               "content-length": str(MAX_IMPORT_BODY_BYTES + 1)}
    status, _, payload = await call_asgi(guarded_app, "/api/import/xlsx", headers, body)
    assert status == 413 and payload["detail"]["code"] == "FILE_TOO_LARGE"
    assert "10 MB" in payload["detail"]["message"]
    assert body.calls == 0


async def test_import_counts_chunked_bytes_and_stops_early(guarded_app, auth, admin):
    megabyte = b"x" * (1024 * 1024)
    part = (
        b"--xyz" + CRLF + b'Content-Disposition: form-data; name="file"; filename="a.xlsx"' + CRLF
        + b"Content-Type: application/octet-stream" + CRLF + CRLF
    )
    body = Body([part] + [megabyte] * 200)  # 200 MB sem Content-Length (chunked)
    headers = {**auth(admin), "content-type": MULTIPART}
    status, _, payload = await call_asgi(guarded_app, "/api/import/xlsx", headers, body)
    assert status == 413 and payload["detail"]["code"] == "FILE_TOO_LARGE"
    assert body.read <= MAX_IMPORT_BODY_BYTES + len(megabyte)  # parou logo depois do limite


async def test_other_routes_have_a_small_body_limit(guarded_app, auth, receptionist):
    body = Body([b"{" + b" " * 1024])
    headers = {**auth(receptionist), "content-type": "application/json",
               "content-length": str(MAX_BODY_BYTES + 1)}
    status, _, payload = await call_asgi(guarded_app, "/api/bookings", headers, body)
    assert status == 413 and payload["detail"]["code"] == "REQUEST_TOO_LARGE"
    assert body.calls == 0
    chunked = Body([b" " * (256 * 1024)] * 8)  # 2 MB chunked
    status, _, payload = await call_asgi(
        guarded_app, "/api/bookings", {**auth(receptionist), "content-type": "application/json"},
        chunked,
    )
    assert status == 413 and payload["detail"]["code"] == "REQUEST_TOO_LARGE"


async def test_small_requests_pass_through(guarded_app, auth, receptionist, video_room):
    async with asgi_client(guarded_app) as c:
        resp = await c.post(
            "/api/bookings",
            json={"room_id": video_room.id, "date": "2026-10-26", "start": "09:00",
                  "end": "10:00", "requester_name": "Fulana"},
            headers=auth(receptionist),
        )
        assert resp.status_code == 201, resp.text
        resp = await c.post("/api/import/xlsx", headers=auth(receptionist), files={
            "file": ("a.xlsx", b"PK\x03\x04", "application/octet-stream")})
        assert resp.status_code == 403


async def test_real_server_refuses_anonymous_upload_without_spooling_it(monkeypatch, tmp_path):
    """O cenário do relatório: Content-Length gigante e o corpo chegando devagar. A resposta 401
    sai sem esperar o corpo e nada é gravado na pasta temporária."""
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spool))
    async with serve_app(create_app()) as base:
        host, port = base.split(":")
        reader, writer = await asyncio.open_connection(host, int(port))
        try:
            boundary = "xyz"
            head = (
                "POST /api/import/xlsx HTTP/1.1\r\n"
                f"Host: {base}\r\n"
                f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
                f"Content-Length: {HUGE}\r\n\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="a.xlsx"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            )
            writer.write(head.encode() + b"PK\x03\x04" + b"\0" * (2 * 1024 * 1024))
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), 10)
            assert status_line.startswith(b"HTTP/1.1 401"), status_line
            assert os.listdir(spool) == []
        finally:
            writer.close()


# ---------------------------------------------------------------- valores que o banco recusa


async def test_database_data_errors_become_422(auth, receptionist):
    application = create_app()

    @application.get("/api/test-nul")
    async def nul() -> dict:
        from app.db import SessionLocal

        async with SessionLocal() as s:
            await s.execute(text("SELECT CAST(:v AS text)"), {"v": "a" + chr(0) + "b"})
        return {}

    @application.get("/api/test-unique")
    async def unique() -> dict:
        from app.db import SessionLocal

        async with SessionLocal() as s:
            for _ in range(2):
                s.add(User(username="dup", full_name="Dup", password_hash="x"))
                await s.flush()
        return {}

    async with application.router.lifespan_context(application):
        async with asgi_client(application) as c:
            resp = await c.get("/api/test-nul")
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "INVALID_INPUT"
            # outros erros do banco continuam sendo 500 (não são culpa de quem enviou)
            resp = await c.get("/api/test-unique")
            assert resp.status_code == 500
