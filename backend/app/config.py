"""Configuração central (variáveis de ambiente com prefixo AGENDA_ ou arquivo backend/.env)."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256: com um segredo curto, quem tem um token consegue descobri-lo por força bruta.
MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENDA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # development | production | test
    env: str = "development"

    database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda"
    # Sem Redis (None) o backend usa travas e pub/sub em memória: válido apenas para UMA instância.
    redis_url: str | None = None

    jwt_secret: str = "dev-only-insecure-secret-change-me-0123456789"
    jwt_expire_minutes: int = 720  # um turno de 12h
    password_hash_iterations: int = 310_000

    # Limite de tentativas de login (por usuário e por IP): N falhas seguidas com no máximo
    # `login_failure_window_seconds` entre elas bloqueiam; cada falha seguinte escala o bloqueio.
    login_max_failures: int = 5
    login_failure_window_seconds: int = 900
    login_block_seconds: list[int] = [60, 300, 900]

    # Regras de calendário da recepção
    timezone: str = "America/Sao_Paulo"
    day_start: time = time(8, 0)
    day_end: time = time(19, 0)
    morning_end: time = time(12, 0)  # "Manhã Toda" = day_start .. morning_end
    afternoon_start: time = time(13, 0)  # "Tarde Toda" = afternoon_start .. day_end
    slot_minutes: int = 30
    work_days: list[int] = [0, 1, 2, 3, 4]  # 0 = segunda-feira (datetime.weekday)

    # Soft lock (seleção de célula)
    lock_ttl_seconds: int = 120

    # Link para o sistema de chamados. "{ticket}" é substituído pelo número (URL-encoded).
    ticket_url_template: str | None = None

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Pasta com o build do frontend (frontend/dist). Se definida, o backend serve a SPA em "/".
    frontend_dist: str | None = None

    # Criação automática do primeiro administrador (usado pelo comando `init-db`)
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "Administrador"

    instance_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    # GET /api/health: prazo do "SELECT 1" (estourou = banco indisponível, HTTP 503)
    health_db_timeout_seconds: float = 3.0

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, v):
        # Provedores (Render, Heroku...) entregam "postgres://" ou "postgresql://": o SQLAlchemy
        # assíncrono precisa do driver asyncpg explícito.
        if isinstance(v, str):
            for prefix in ("postgres://", "postgresql://"):
                if v.startswith(prefix):
                    return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    @field_validator("redis_url", "ticket_url_template", "frontend_dist", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _check_calendar(self) -> "Settings":
        if not (self.day_start < self.morning_end <= self.afternoon_start < self.day_end):
            raise ValueError(
                "Horários inválidos: exige day_start < morning_end <= afternoon_start < day_end"
            )
        if self.slot_minutes <= 0 or 60 % self.slot_minutes != 0:
            raise ValueError("slot_minutes deve dividir 60 (ex.: 15, 30, 60)")
        for t in (self.day_start, self.day_end, self.morning_end, self.afternoon_start):
            if (t.hour * 60 + t.minute) % self.slot_minutes != 0 or t.second or t.microsecond:
                raise ValueError(f"Horário {t} não está alinhado a slot_minutes={self.slot_minutes}")
        if not self.work_days or any(d < 0 or d > 6 for d in self.work_days):
            raise ValueError("work_days deve conter valores entre 0 (segunda) e 6 (domingo)")
        if self.env == "production":
            if self.jwt_secret.startswith("dev-only"):
                raise ValueError("Defina AGENDA_JWT_SECRET em produção")
            if len(self.jwt_secret) < MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"AGENDA_JWT_SECRET muito curto ({len(self.jwt_secret)} caracteres): em "
                    f"produção use pelo menos {MIN_JWT_SECRET_LENGTH} caracteres aleatórios "
                    '(ex.: python -c "import secrets; print(secrets.token_urlsafe(48))")'
                )
        return self

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    def production_warnings(self) -> list[str]:
        """Configurações que funcionam, mas são arriscadas em produção (logadas no startup)."""
        if self.env != "production":
            return []
        warnings: list[str] = []
        if not self.redis_url:
            warnings.append(
                "AGENDA_REDIS_URL não definido: travas e eventos em tempo real ficam na memória "
                "deste processo. Rode somente uma instância/worker, ou configure o Redis."
            )
        local = [origin for origin in self.cors_origins if _is_local_origin(origin)]
        if local:
            warnings.append(
                f"AGENDA_CORS_ORIGINS contém origens locais ({', '.join(local)}): em produção "
                "liste somente o endereço público do frontend."
            )
        return warnings


def _is_local_origin(origin: str) -> bool:
    host = (urlsplit(origin.strip()).hostname or "").lower()
    return (
        host in ("localhost", "::1", "0.0.0.0")
        or host.endswith(".localhost")
        or host.startswith("127.")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
