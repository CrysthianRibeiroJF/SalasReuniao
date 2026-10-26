"""Fixtures compartilhadas.

Cada execução usa o banco de AGENDA_TEST_DATABASE_URL (padrão: agenda_test na porta 5433).
Execuções paralelas (ex.: vários agentes/terminais) DEVEM usar bancos diferentes:
    AGENDA_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_test_x pytest
O banco é criado se não existir, o schema é recriado via Alembic no início da sessão e as
tabelas são truncadas antes de cada teste.
"""

from __future__ import annotations

import os

TEST_DB_URL = os.environ.get(
    "AGENDA_TEST_DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_test"
)
os.environ["AGENDA_ENV"] = "test"
os.environ["AGENDA_DATABASE_URL"] = TEST_DB_URL
os.environ["AGENDA_REDIS_URL"] = ""  # testes padrão usam backend em memória
os.environ["AGENDA_PASSWORD_HASH_ITERATIONS"] = "1000"
os.environ.setdefault("AGENDA_JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("AGENDA_TICKET_URL_TEMPLATE", "https://chamados.example.com/ticket/{ticket}")
os.environ.setdefault("AGENDA_FRONTEND_DIST", "")
# NullPool: cada /api/health abre uma conexão nova, que sob carga (grupos em paralelo) passa de 3 s.
os.environ.setdefault("AGENDA_HEALTH_DB_TIMEOUT_SECONDS", "20")

from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.models import Room, RoomType, User, UserRole, VideoStatus  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(scope="session", autouse=True)
async def _database() -> AsyncIterator[None]:
    dsn = _plain_dsn(TEST_DB_URL)
    base, dbname = dsn.rsplit("/", 1)
    dbname = dbname.split("?", 1)[0]
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname):
            await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()

    from app.migrate import upgrade_head_async

    await upgrade_head_async(TEST_DB_URL)
    yield
    from app.db import engine

    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_state(_database) -> AsyncIterator[None]:
    from app.db import engine
    from app.realtime import events, registry

    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE booking_logs, bookings, rooms, users RESTART IDENTITY CASCADE")
        )
    yield
    registry.set_lock_store(registry.NullLockStore())
    events.set_event_bus(None)


@pytest.fixture
async def session():
    from app.db import SessionLocal

    async with SessionLocal() as s:
        yield s


@pytest.fixture
def make_user(session) -> Callable[..., Awaitable[User]]:
    async def _make(
        username: str = "recep",
        full_name: str = "Recepcionista",
        role: UserRole = UserRole.RECEPTIONIST,
        password: str = "senha123",
        is_active: bool = True,
    ) -> User:
        user = User(
            username=username,
            full_name=full_name,
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    return _make


@pytest.fixture
async def admin(make_user) -> User:
    return await make_user("admin", "Administradora TI", UserRole.ADMIN)


@pytest.fixture
async def receptionist(make_user) -> User:
    return await make_user("ana", "Ana Recepção")


@pytest.fixture
async def receptionist2(make_user) -> User:
    return await make_user("bia", "Bia Recepção")


def auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth() -> Callable[[User], dict[str, str]]:
    return auth_headers


@pytest.fixture
def make_room(session) -> Callable[..., Awaitable[Room]]:
    counter = {"order": 0}

    async def _make(
        name: str,
        room_type: RoomType = RoomType.NORMAL,
        capacity: int = 8,
        display_order: int | None = None,
        video_status: VideoStatus = VideoStatus.OPERATIONAL,
        maintenance_ticket_id: str | None = None,
        is_active: bool = True,
        resources: list[str] | None = None,
    ) -> Room:
        counter["order"] += 1
        room = Room(
            name=name,
            room_type=room_type,
            capacity=capacity,
            display_order=counter["order"] if display_order is None else display_order,
            video_status=video_status,
            maintenance_ticket_id=maintenance_ticket_id,
            is_active=is_active,
            resources=resources or [],
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)
        return room

    return _make


@pytest.fixture
async def video_room(make_room) -> Room:
    return await make_room("Sala 01 - Executiva", RoomType.VIDEO, capacity=12)


@pytest.fixture
async def normal_room(make_room) -> Room:
    return await make_room("Sala 05", RoomType.NORMAL, capacity=6)


@pytest.fixture
async def app():
    from app.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
