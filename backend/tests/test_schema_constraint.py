"""RNF02 no nível do banco: a constraint GiST impede sobreposição mesmo em corrida."""

from __future__ import annotations

import asyncio
from datetime import date, time

import asyncpg
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Booking, BookingStatus, RoomType
from app.timeutil import local_dt
from tests.conftest import TEST_DB_URL, _plain_dsn

D = date(2026, 10, 26)


def _booking(room, user, start: time, end: time, **kw) -> Booking:
    return Booking(
        room_id=room.id,
        requester_name="Fulano",
        start_time=local_dt(D, start),
        end_time=local_dt(D, end),
        created_by=user.id,
        **kw,
    )


async def test_overlap_rejected_adjacent_allowed(session, receptionist, video_room):
    session.add(_booking(video_room, receptionist, time(14), time(15)))
    await session.commit()
    session.add(_booking(video_room, receptionist, time(15), time(16)))  # adjacente: '[)'
    await session.commit()
    session.add(_booking(video_room, receptionist, time(14, 30), time(15, 30)))
    with pytest.raises(IntegrityError) as exc:
        await session.commit()
    assert "bookings_no_overlap" in str(exc.value)
    await session.rollback()


async def test_cancelled_does_not_block(session, receptionist, video_room):
    session.add(_booking(video_room, receptionist, time(9), time(10), status=BookingStatus.CANCELLED))
    await session.commit()
    session.add(_booking(video_room, receptionist, time(9), time(10)))
    await session.commit()


async def test_other_room_same_time_ok(session, receptionist, video_room, make_room):
    other = await make_room("Sala 02", RoomType.NORMAL)
    session.add(_booking(video_room, receptionist, time(9), time(10)))
    session.add(_booking(other, receptionist, time(9), time(10)))
    await session.commit()


async def test_simultaneous_inserts_only_one_wins(receptionist, video_room):
    """Duas conexões independentes inserindo o mesmo horário ao mesmo tempo."""
    dsn = _plain_dsn(TEST_DB_URL)
    conns = [await asyncpg.connect(dsn) for _ in range(2)]
    sql = (
        "INSERT INTO bookings (room_id, requester_name, start_time, end_time, created_by) "
        "VALUES ($1, $2, $3, $4, $5)"
    )
    args = (video_room.id, "X", local_dt(D, time(14)), local_dt(D, time(15)), receptionist.id)
    try:
        results = await asyncio.gather(
            *(c.execute(sql, *args) for c in conns), return_exceptions=True
        )
    finally:
        for c in conns:
            await c.close()
    ok = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 1 and len(errors) == 1
    # Em corrida exata o PostgreSQL pode abortar o perdedor por deadlock (40P01) em vez de
    # 23P01 — nos dois casos só uma gravação sobrevive (o serviço repete o 40P01 e responde 409).
    assert isinstance(
        errors[0],
        (asyncpg.exceptions.ExclusionViolationError, asyncpg.exceptions.DeadlockDetectedError),
    )


async def test_interval_check(session, receptionist, video_room):
    session.add(_booking(video_room, receptionist, time(15), time(14)))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
