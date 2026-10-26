"""Engine e sessões assíncronas do SQLAlchemy (asyncpg)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

# Em testes usamos NullPool: o mesmo engine é usado a partir de loops de eventos diferentes
# (pytest-asyncio, TestClient, uvicorn em thread) e conexões asyncpg não podem trocar de loop.
_engine_kwargs: dict = {"pool_pre_ping": True}
if settings.is_test:
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependência FastAPI: uma sessão por requisição."""
    async with SessionLocal() as session:
        yield session
