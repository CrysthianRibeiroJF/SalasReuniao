"""Entradas fora do que o banco/a aritmética de datas aceitam viram 422, nunca 500 (R23/R29)."""

from __future__ import annotations

import pytest

from tests.core_support import booking_payload, create_booking

NUL = chr(0)
TOO_BIG = 2**31  # um a mais que o INTEGER do PostgreSQL


async def test_out_of_range_ids_are_422(client, auth, admin, receptionist, normal_room):
    h, a = auth(receptionist), auth(admin)
    requests = [
        client.get(f"/api/rooms/{TOO_BIG}", headers=h),
        client.get("/api/rooms/0", headers=h),
        client.patch(f"/api/rooms/{TOO_BIG}", json={"capacity": 1}, headers=a),
        client.put(f"/api/rooms/{10**20}/maintenance", json={"ticket_id": "TI-1"}, headers=h),
        client.delete(f"/api/rooms/{TOO_BIG}/maintenance", headers=h),
        client.patch(f"/api/users/{TOO_BIG}", json={"full_name": "Fulano"}, headers=a),
        client.get("/api/audit", params={"user_id": TOO_BIG}, headers=h),
        client.get("/api/audit", params={"offset": 10**12}, headers=h),
        client.post("/api/bookings", json=booking_payload(normal_room, room_id=TOO_BIG), headers=h),
        client.post("/api/rooms", json={"name": "X", "room_type": "NORMAL",
                                        "display_order": 10**12}, headers=a),
    ]
    for request in requests:
        resp = await request
        assert resp.status_code == 422, (resp.request.url, resp.text)
        assert isinstance(resp.json()["detail"], list)  # formato padrão do FastAPI
    b = await create_booking(client, h, normal_room)
    resp = await client.patch(f"/api/bookings/{b['id']}", json={"version": 1, "room_id": TOO_BIG},
                              headers=h)
    assert resp.status_code == 422


async def test_nul_characters_are_rejected_or_ignored(client, auth, admin, receptionist,
                                                      normal_room):
    h = auth(receptionist)
    for field in ("requester_name", "title", "notes", "ticket_number"):
        resp = await client.post(
            "/api/bookings", json=booking_payload(normal_room, **{field: f"a{NUL}b"}), headers=h
        )
        assert resp.status_code == 422, (field, resp.text)
    b = await create_booking(client, h, normal_room)
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 1, "title": f"x{NUL}"}, headers=h
    )
    assert resp.status_code == 422
    resp = await client.post(f"/api/bookings/{b['id']}/cancel", json={"reason": NUL}, headers=h)
    assert resp.status_code == 422
    resp = await client.post("/api/auth/login", json={"username": f"ana{NUL}", "password": "x"})
    assert resp.status_code == 422
    resp = await client.post("/api/rooms", json={"name": f"Sala{NUL}", "room_type": "NORMAL"},
                             headers=auth(admin))
    assert resp.status_code == 422
    resp = await client.post(
        "/api/rooms", json={"name": "Sala", "room_type": "NORMAL", "resources": [f"TV{NUL}"]},
        headers=auth(admin),
    )
    assert resp.status_code == 422
    resp = await client.put(f"/api/rooms/{normal_room.id}/maintenance",
                            json={"ticket_id": f"TI{NUL}"}, headers=h)
    assert resp.status_code == 422
    # na busca o NUL é só descartado
    resp = await client.get("/api/bookings/search", params={"q": f"Ful{NUL}ano"}, headers=h)
    assert resp.status_code == 200 and [r["id"] for r in resp.json()] == [b["id"]]


@pytest.mark.parametrize("day", ["9999-12-31", "0001-01-01", "1999-12-31", "2101-01-01"])
async def test_dates_outside_the_supported_window_are_422(client, auth, receptionist,
                                                          normal_room, day):
    h = auth(receptionist)
    requests = [
        client.get("/api/bookings", params={"date": day}, headers=h),
        client.get("/api/bookings", params={"start_date": day}, headers=h),
        client.get("/api/availability/full-day", params={"date": day}, headers=h),
        client.get("/api/availability/free-rooms",
                   params={"date": day, "start": "09:00", "end": "10:00"}, headers=h),
        client.get("/api/audit", params={"date": day}, headers=h),
        client.get("/api/audit", params={"booking_date": day}, headers=h),
        client.post("/api/bookings", json=booking_payload(normal_room, date=day), headers=h),
    ]
    for request in requests:
        resp = await request
        assert resp.status_code == 422, (resp.request.url, resp.text)
    resp = await client.get("/api/bookings", params={"date": "2100-12-31"}, headers=h)
    assert resp.status_code == 200  # os extremos da janela valem


@pytest.mark.parametrize("value", ["09:00Z", "09:00+05:00", "09:00:00-03:00"])
async def test_times_with_timezone_are_422(client, auth, receptionist, normal_room, value):
    h = auth(receptionist)
    resp = await client.post("/api/bookings", json=booking_payload(normal_room, start=value),
                             headers=h)
    assert resp.status_code == 422, resp.text
    resp = await client.get(
        "/api/availability/free-rooms",
        params={"date": "2026-10-26", "start": value, "end": "11:00"},
        headers=h,
    )
    assert resp.status_code == 422, resp.text
