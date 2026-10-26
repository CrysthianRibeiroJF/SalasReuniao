"""Aplicação: saúde, configuração, CORS, formato de erro e SPA servida pelo backend."""

from __future__ import annotations

import httpx
import pytest

from app.api import health as health_api
from app.config import settings
from app.main import create_app
from tests.realtime_support import FakeRedisTcp


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "database": True,
        "realtime": "memory",
        "redis": "disabled",
    }


async def test_health_reports_database_down(client, monkeypatch):
    async def down() -> bool:
        return False

    monkeypatch.setattr(health_api, "check_database", down)
    resp = await client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "database": False,
        "realtime": "memory",
        "redis": "disabled",
    }


async def test_health_with_redis_up_then_down(monkeypatch):
    """Redis fora do ar: 200 com status "degraded" (o REST segue funcionando); só o banco dá 503."""
    with FakeRedisTcp() as fake:
        monkeypatch.setattr(settings, "redis_url", fake.url)
        application = create_app()
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/api/health")
                assert resp.status_code == 200
                assert resp.json() == {
                    "status": "ok",
                    "database": True,
                    "realtime": "redis",
                    "redis": "ok",
                }
                fake.stop()
                resp = await c.get("/api/health")
                assert resp.status_code == 200
                assert resp.json() == {
                    "status": "degraded",
                    "database": True,
                    "realtime": "redis",
                    "redis": "down",
                }


async def test_config(client, auth, receptionist):
    assert (await client.get("/api/config")).status_code == 401
    resp = await client.get("/api/config", headers=auth(receptionist))
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "America/Sao_Paulo"
    assert (body["day_start"], body["day_end"], body["slot_minutes"]) == ("08:00", "19:00", 30)
    assert body["morning"] == {"start": "08:00", "end": "12:00"}
    assert body["afternoon"] == {"start": "13:00", "end": "19:00"}
    assert body["work_days"] == [0, 1, 2, 3, 4]
    assert body["lock_ttl_seconds"] == 120
    assert body["ticket_url_template"] == "https://chamados.example.com/ticket/{ticket}"
    assert body["realtime_backend"] == "memory"
    assert len(body["today"]) == 10


async def test_cors_preflight(client):
    resp = await client.options(
        "/api/bookings",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    resp = await client.options(
        "/api/bookings",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in resp.headers


async def test_openapi_and_unknown_api_route(client):
    resp = await client.get("/openapi.json")
    assert resp.json()["info"]["title"] == "Agenda de Salas"
    resp = await client.get("/api/nao-existe")
    assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}
    resp = await client.get("/")
    assert resp.status_code == 404  # sem frontend_dist configurado


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Agenda</title>", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path.parent / "segredo.txt").write_text("não pode vazar", encoding="utf-8")
    return tmp_path


@pytest.fixture
async def spa_client(dist):
    application = create_app(frontend_dist=dist)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_spa_serves_index_and_assets(spa_client):
    for path in ("/", "/grade", "/admin/salas", "/reservas/2026-10-26"):
        resp = await spa_client.get(path)
        assert resp.status_code == 200, path
        assert "<title>Agenda</title>" in resp.text
        assert resp.headers["cache-control"] == "no-cache"
    resp = await spa_client.get("/assets/index-abc123.js")
    assert resp.status_code == 200 and "console.log" in resp.text
    assert "immutable" in resp.headers["cache-control"]
    resp = await spa_client.get("/favicon.svg")
    assert resp.status_code == 200 and resp.text == "<svg/>"


async def test_spa_never_shadows_backend_404s(spa_client):
    resp = await spa_client.get("/api/nao-existe")
    assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}
    resp = await spa_client.post("/api/nao-existe", json={})
    assert resp.status_code == 404
    resp = await spa_client.get("/api")
    assert resp.status_code == 404 and "Agenda" not in resp.text
    resp = await spa_client.get("/assets/nao-existe.js")
    assert resp.status_code == 404
    resp = await spa_client.get("/arquivo-que-falta.png")
    assert resp.status_code == 404
    resp = await spa_client.post("/grade")
    assert resp.status_code == 404
    resp = await spa_client.get("/api/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


async def test_spa_blocks_path_traversal(spa_client):
    for path in ("/../segredo.txt", "/%2e%2e/segredo.txt", "/assets/../../segredo.txt"):
        resp = await spa_client.get(path)
        assert "não pode vazar" not in resp.text, path


def test_missing_dist_is_ignored(tmp_path):
    application = create_app(frontend_dist=tmp_path / "nao-existe")
    assert not any(getattr(r, "name", None) == "spa-assets" for r in application.routes)
