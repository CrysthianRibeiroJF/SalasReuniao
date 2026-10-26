"""Aplica as migrations programaticamente (usado pela CLI `init-db` e pelos testes)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def upgrade_head(database_url: str | None = None) -> None:
    """Síncrono (usa asyncio.run internamente). Em código async use `await upgrade_head_async()`."""
    command.upgrade(alembic_config(database_url), "head")


async def upgrade_head_async(database_url: str | None = None) -> None:
    await asyncio.to_thread(upgrade_head, database_url)
