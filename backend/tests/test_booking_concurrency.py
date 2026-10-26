"""Critério de pronto da Fase 1 (RNF02): requisições simultâneas nunca geram duas reservas."""

from __future__ import annotations

import asyncio
from datetime import time

import asyncpg
import pytest
from sqlalchemy import func, select

from app.models import Booking, BookingLog, RoomType
from app.schemas import BookingCreate, BookingUpdate
from app.services import bookings as bookings_service
from app.services.errors import BusinessError
from app.timeutil import local_dt
from tests.conftest import TEST_DB_URL, _plain_dsn
from tests.core_support import D, booking_payload, create_booking, error_code


async def _active_rows(session, room_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Booking).where(Booking.room_id == room_id)
    )


async def test_two_simultaneous_posts_same_slot(
    client, auth, receptionist, receptionist2, video_room, session
):
    payload = booking_payload(video_room)
    responses = await asyncio.gather(
        client.post("/api/bookings", json=payload, headers=auth(receptionist)),
        client.post("/api/bookings", json=payload, headers=auth(receptionist2)),
    )
    assert sorted(r.status_code for r in responses) == [201, 409]
    loser = next(r for r in responses if r.status_code == 409)
    winner = next(r for r in responses if r.status_code == 201)
    detail = loser.json()["detail"]
    assert detail["code"] == "BOOKING_CONFLICT"
    assert [c["id"] for c in detail["conflicts"]] == [winner.json()["booking"]["id"]]
    assert await _active_rows(session, video_room.id) == 1
    assert await session.scalar(select(func.count()).select_from(BookingLog)) == 1


async def test_ten_simultaneous_overlapping_intervals(client, make_user, auth, video_room, session):
    users = [await make_user(f"recep{i}", f"Recepcionista {i}") for i in range(10)]
    # Intervalos diferentes, mas todos se sobrepõem dois a dois (início escalonado de 10 em 10 min).
    payloads = []
    for i in range(10):
        start = 9 * 60 + 10 * i
        end = start + 120
        payloads.append(
            booking_payload(
                video_room,
                start=f"{start // 60:02d}:{start % 60:02d}",
                end=f"{end // 60:02d}:{end % 60:02d}",
                requester_name=f"Solicitante {i}",
            )
        )
    responses = await asyncio.gather(
        *(
            client.post("/api/bookings", json=p, headers=auth(u))
            for p, u in zip(payloads, users, strict=True)
        )
    )
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 9, codes
    assert {error_code(r) for r in responses if r.status_code == 409} == {"BOOKING_CONFLICT"}
    assert await _active_rows(session, video_room.id) == 1


async def test_simultaneous_posts_in_different_rooms_all_succeed(
    client, auth, receptionist, make_room, session
):
    rooms = [await make_room(f"Sala {i:02d}", RoomType.NORMAL) for i in range(1, 6)]
    responses = await asyncio.gather(
        *(
            client.post("/api/bookings", json=booking_payload(r), headers=auth(receptionist))
            for r in rooms
        )
    )
    assert [r.status_code for r in responses] == [201] * 5
    assert await session.scalar(select(func.count()).select_from(Booking)) == 5


async def test_constraint_race_path_returns_conflict(
    client, auth, receptionist, receptionist2, video_room, normal_room, session, monkeypatch
):
    """Sem pré-checagem: a violação 23P01 vira o mesmo 409 (com conflitos e alternativas)."""
    first = await create_booking(client, auth(receptionist), video_room)

    async def skip_precheck(*args, **kwargs):
        return None

    monkeypatch.setattr(bookings_service, "_ensure_no_conflict", skip_precheck)
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(video_room, start="14:30", end="15:30"),
        headers=auth(receptionist2),
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "BOOKING_CONFLICT"
    assert [c["id"] for c in detail["conflicts"]] == [first["id"]]
    assert [a["id"] for a in detail["alternatives"]] == [normal_room.id]
    assert await _active_rows(session, video_room.id) == 1

    # mesmo caminho no PATCH (mover uma reserva por cima de outra)
    other = await create_booking(client, auth(receptionist2), normal_room)
    resp = await client.patch(
        f"/api/bookings/{other['id']}",
        json={"version": 1, "room_id": video_room.id},
        headers=auth(receptionist2),
    )
    assert resp.status_code == 409 and error_code(resp) == "BOOKING_CONFLICT"
    resp = await client.get(f"/api/bookings/{other['id']}", headers=auth(receptionist2))
    assert resp.json()["room_id"] == normal_room.id and resp.json()["version"] == 1


async def test_session_stays_usable_after_lost_race(session, receptionist, video_room, monkeypatch):
    """Quem reutiliza a sessão (importação) continua usando os mesmos objetos após um conflito."""

    async def skip_precheck(*args, **kwargs):
        return None

    monkeypatch.setattr(bookings_service, "_ensure_no_conflict", skip_precheck)

    def data(start: time, end: time) -> BookingCreate:
        return BookingCreate(
            room_id=video_room.id, date=D, start=start, end=end, requester_name="Importado"
        )

    await bookings_service.create_booking(session, data(time(9), time(10)), receptionist)
    with pytest.raises(BusinessError) as exc:
        await bookings_service.create_booking(
            session, data(time(9, 30), time(10, 30)), receptionist
        )
    assert exc.value.code == "BOOKING_CONFLICT" and exc.value.status_code == 409
    assert len(exc.value.extra["conflicts"]) == 1

    result = await bookings_service.create_booking(session, data(time(10), time(11)), receptionist)
    assert result.booking.created_by.full_name == receptionist.full_name
    assert await _active_rows(session, video_room.id) == 2
    assert await session.scalar(select(func.count()).select_from(BookingLog)) == 2


# ---------------------------------------------------------------- deadlock na exclusion constraint


async def _wait_until_blocked(conn: asyncpg.Connection, timeout: float = 15.0) -> None:
    """Espera algum backend deste banco ficar bloqueado num lock (o INSERT/UPDATE do serviço)."""
    sql = (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while await conn.fetchval(sql) == 0:
        assert loop.time() < deadline, "o serviço não chegou a esperar o lock"
        await asyncio.sleep(0.05)


async def _force_deadlock(service_call, room, user):
    """Provoca um deadlock real entre o serviço e outra transação; o serviço é a vítima.

    1. a outra transação insere R1 (14:00-15:00) e não confirma;
    2. o serviço grava 14:30-15:30 e fica esperando R1;
    3. a outra transação insere R2 (15:00-16:00), que conflita com a linha pendente do serviço.
    deadlock_timeout alto na outra conexão garante que o detector rode no serviço.
    """
    other = await asyncpg.connect(_plain_dsn(TEST_DB_URL))
    probe = await asyncpg.connect(_plain_dsn(TEST_DB_URL))
    sql = (
        "INSERT INTO bookings (room_id, requester_name, start_time, end_time, created_by) "
        "VALUES ($1, $2, $3, $4, $5)"
    )
    try:
        await other.execute("SET deadlock_timeout = '30s'")
        tx = other.transaction()
        await tx.start()
        await other.execute(
            sql, room.id, "R1", local_dt(D, time(14)), local_dt(D, time(15)), user.id
        )
        task = asyncio.create_task(service_call())
        await _wait_until_blocked(probe)
        await other.execute(
            sql, room.id, "R2", local_dt(D, time(15)), local_dt(D, time(16)), user.id
        )
        await tx.commit()
        return await asyncio.gather(task, return_exceptions=True)
    finally:
        await other.close()
        await probe.close()


async def test_deadlock_on_create_is_retried_into_conflict(session, receptionist, normal_room):
    data = BookingCreate(
        room_id=normal_room.id, date=D, start=time(14, 30), end=time(15, 30), requester_name="S"
    )
    (result,) = await _force_deadlock(
        lambda: bookings_service.create_booking(session, data, receptionist),
        normal_room,
        receptionist,
    )
    assert isinstance(result, BusinessError), result
    assert result.code == "BOOKING_CONFLICT"
    assert [c["requester_name"] for c in result.extra["conflicts"]] == ["R1", "R2"]
    names = (
        await session.scalars(select(Booking.requester_name).order_by(Booking.start_time))
    ).all()
    assert names == ["R1", "R2"]

    # a sessão continua utilizável depois do deadlock + conflito
    ok = await bookings_service.create_booking(
        session, data.model_copy(update={"start": time(17), "end": time(18)}), receptionist
    )
    assert ok.booking.requester_name == "S"


async def test_deadlock_on_update_is_retried_into_conflict(
    session, receptionist, normal_room, video_room
):
    created = await bookings_service.create_booking(
        session,
        BookingCreate(
            room_id=video_room.id, date=D, start=time(14), end=time(15), requester_name="B"
        ),
        receptionist,
    )
    move = BookingUpdate(version=1, room_id=normal_room.id, start=time(14, 30), end=time(15, 30))
    (result,) = await _force_deadlock(
        lambda: bookings_service.update_booking(session, created.booking.id, move, receptionist),
        normal_room,
        receptionist,
    )
    assert isinstance(result, BusinessError), result
    assert result.code == "BOOKING_CONFLICT"
    assert {c["requester_name"] for c in result.extra["conflicts"]} == {"R1", "R2"}

    await session.rollback()  # libera o FOR UPDATE da tentativa
    current = await bookings_service.get_booking(session, created.booking.id)
    assert (current.room_id, current.start, current.version) == (video_room.id, time(14), 1)
    # e a mesma reserva pode ser editada normalmente em seguida
    edit = BookingUpdate(version=1, title="Depois do deadlock")
    updated = await bookings_service.update_booking(session, created.booking.id, edit, receptionist)
    assert (updated.booking.title, updated.booking.version) == ("Depois do deadlock", 2)
