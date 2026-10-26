"""Avisos de configuração arriscada em produção (logados no startup, nada exposto na API)."""

from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.main import create_app

PROD = {"env": "production", "jwt_secret": "segredo-de-producao-0123456789abcdef0123456789"}


async def start_and_capture(caplog) -> tuple[list[str], dict]:
    application = create_app()
    with caplog.at_level(logging.WARNING, logger="app.main"):
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                health = (await client.get("/api/health")).json()
    warnings = [r.getMessage() for r in caplog.records if r.name == "app.main"]
    return warnings, health


async def test_production_without_redis_and_with_local_origins_warns(caplog, monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(
        settings, "cors_origins", ["http://localhost:5173", "https://agenda.empresa.com.br"]
    )
    warnings, health = await start_and_capture(caplog)
    assert len(warnings) == 2, warnings
    assert "AGENDA_REDIS_URL não definido" in warnings[0]
    assert "somente uma instância/worker" in warnings[0]
    assert "AGENDA_CORS_ORIGINS contém origens locais (http://localhost:5173)" in warnings[1]
    assert "agenda.empresa.com.br" not in warnings[1]
    # nada novo é exposto publicamente
    assert health == {"status": "ok", "database": True, "realtime": "memory", "redis": "disabled"}


async def test_no_warnings_outside_production(caplog):
    assert settings.env == "test" and settings.redis_url is None  # e CORS com localhost (padrão)
    warnings, _ = await start_and_capture(caplog)
    assert warnings == []


@pytest.mark.parametrize(
    ("origins", "local"),
    [
        (["https://agenda.empresa.com.br"], []),
        (["http://127.0.0.1:5173", "http://[::1]:8000", "http://app.localhost"],
         ["http://127.0.0.1:5173", "http://[::1]:8000", "http://app.localhost"]),
        (["http://LOCALHOST:5173", "https://localhost.empresa.com.br"], ["http://LOCALHOST:5173"]),
    ],
)
def test_local_origin_detection(origins, local):
    config = Settings(**PROD, redis_url="redis://redis:6379/0", cors_origins=origins)
    warnings = config.production_warnings()
    if not local:
        assert warnings == []
    else:
        assert warnings == [
            f"AGENDA_CORS_ORIGINS contém origens locais ({', '.join(local)}): em produção "
            "liste somente o endereço público do frontend."
        ]


@pytest.mark.parametrize("secret", ["curto", "x" * 31])
def test_production_rejects_short_jwt_secret(secret):
    with pytest.raises(ValidationError) as exc:
        Settings(env="production", jwt_secret=secret)
    assert "AGENDA_JWT_SECRET muito curto" in str(exc.value)
    assert "pelo menos 32 caracteres" in str(exc.value)
    Settings(env="production", jwt_secret="x" * 32)  # 32 já serve
    Settings(env="development", jwt_secret="curto")  # fora de produção não se exige


def test_production_with_redis_and_public_origins_is_quiet():
    config = Settings(
        **PROD, redis_url="redis://redis:6379/0", cors_origins=["https://agenda.empresa.com.br"]
    )
    assert config.production_warnings() == []
    assert Settings(env="development").production_warnings() == []
