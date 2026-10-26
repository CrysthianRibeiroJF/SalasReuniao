"""Disponibilidade (5.5): salas livres num intervalo e "Livres Dia Inteiro"."""

from __future__ import annotations

from datetime import time, timedelta

from app.models import RoomType, VideoStatus
from app.services import availability
from app.timeutil import local_dt
from tests.core_support import D, create_booking, error_code


async def _setup(make_room):
    return {
        "v1": await make_room("Vídeo 1", RoomType.VIDEO),
        "v2": await make_room("Vídeo 2", RoomType.VIDEO),
        "vm": await make_room(
            "Vídeo Manutenção", RoomType.VIDEO, video_status=VideoStatus.MAINTENANCE
        ),
        "n1": await make_room("Normal 1"),
        "n2": await make_room("Normal 2"),
        "off": await make_room("Inativa", is_active=False),
    }


async def _names(client, headers, path: str, **params) -> list[str]:
    resp = await client.get(path, params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return [r["name"] for r in resp.json()]


async def test_free_rooms_by_interval_and_type(client, auth, receptionist, make_room):
    r = await _setup(make_room)
    h = auth(receptionist)
    await create_booking(client, h, r["v1"], start="14:00", end="15:00")
    await create_booking(client, h, r["n1"], start="15:00", end="16:00")
    cancelled = await create_booking(client, h, r["n2"], start="14:00", end="15:00")
    await client.post(f"/api/bookings/{cancelled['id']}/cancel", json={}, headers=h)

    path = "/api/availability/free-rooms"
    day = D.isoformat()
    assert await _names(client, h, path, date=day, start="14:00", end="15:00") == [
        "Vídeo 2",
        "Vídeo Manutenção",
        "Normal 1",
        "Normal 2",
    ]
    assert await _names(client, h, path, date=day, start="14:30", end="15:30") == [
        "Vídeo 2",
        "Vídeo Manutenção",
        "Normal 2",
    ]
    assert await _names(
        client, h, path, date=day, start="14:00", end="15:00", room_type="VIDEO"
    ) == [
        "Vídeo 2",
    ]
    assert await _names(
        client, h, path, date=day, start="14:00", end="15:00", room_type="NORMAL"
    ) == [
        "Vídeo Manutenção",
        "Normal 1",
        "Normal 2",
    ]
    assert await _names(client, h, path, date=day, period_type="AFTERNOON") == [
        "Vídeo 2",
        "Vídeo Manutenção",
        "Normal 2",
    ]
    assert await _names(
        client, h, path, date=(D + timedelta(days=1)).isoformat(), period_type="FULL_DAY"
    ) == [
        "Vídeo 1",
        "Vídeo 2",
        "Vídeo Manutenção",
        "Normal 1",
        "Normal 2",
    ]


async def test_free_rooms_excluding_booking_being_moved(client, auth, receptionist, make_room):
    r = await _setup(make_room)
    h = auth(receptionist)
    b = await create_booking(client, h, r["v1"], start="14:00", end="15:00")
    names = await _names(
        client,
        h,
        "/api/availability/free-rooms",
        date=D.isoformat(),
        start="14:00",
        end="15:00",
        exclude_booking_id=b["id"],
    )
    assert names[0] == "Vídeo 1"


async def test_free_rooms_validation(client, auth, receptionist):
    h = auth(receptionist)
    path = "/api/availability/free-rooms"
    resp = await client.get(path, params={"date": D.isoformat(), "start": "14:00"}, headers=h)
    assert resp.status_code == 422 and error_code(resp) == "INVALID_TIME"
    resp = await client.get(
        path, params={"date": D.isoformat(), "start": "15:00", "end": "14:00"}, headers=h
    )
    assert resp.status_code == 422 and error_code(resp) == "INVALID_INTERVAL"
    resp = await client.get(path, params={"start": "14:00", "end": "15:00"}, headers=h)
    assert resp.status_code == 422
    resp = await client.get(path, params={"date": D.isoformat(), "period_type": "MORNING"})
    assert resp.status_code == 401


async def test_full_day_free_rooms(client, auth, receptionist, make_room):
    r = await _setup(make_room)
    h = auth(receptionist)
    await create_booking(client, h, r["v2"], start="18:30", end="19:00")
    await create_booking(client, h, r["n1"], start="08:00", end="08:30")
    await create_booking(client, h, r["n2"], date=(D + timedelta(days=1)).isoformat())
    assert await _names(client, h, "/api/availability/full-day", date=D.isoformat()) == [
        "Vídeo 1",
        "Vídeo Manutenção",
        "Normal 2",
    ]


async def test_service_free_rooms_ignores_bookings_outside_interval(
    session, make_room, receptionist
):
    from app.models import Booking

    room = await make_room("Única")
    session.add(
        Booking(
            room_id=room.id,
            requester_name="X",
            start_time=local_dt(D, time(7)),
            end_time=local_dt(D, time(8)),
            created_by=receptionist.id,
        )
    )
    await session.commit()
    assert [x.id for x in await availability.full_day_free_rooms(session, D)] == [room.id]
    assert (
        await availability.free_rooms(session, local_dt(D, time(7, 30)), local_dt(D, time(9))) == []
    )
