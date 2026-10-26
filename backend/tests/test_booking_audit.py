"""Histórico por reserva (/logs) e tela de auditoria (/api/audit) — RF07."""

from __future__ import annotations

from datetime import timedelta

from app.timeutil import today_local
from tests.core_support import D, create_booking, error_code


async def _history_scenario(client, auth, receptionist, receptionist2, normal_room, video_room):
    h1, h2 = auth(receptionist), auth(receptionist2)
    b = await create_booking(client, h1, normal_room)
    url = f"/api/bookings/{b['id']}"
    steps = [
        (h2, "patch", url, {"version": 1, "title": "Novo assunto"}),
        (h1, "patch", url, {"version": 2, "start": "16:00", "end": "17:00"}),
        (
            h2,
            "patch",
            url,
            {"version": 3, "room_id": video_room.id, "start": "09:00", "end": "10:00"},
        ),
        (h1, "post", f"{url}/status", {"version": 4, "status": "CHECKED_IN"}),
        (h2, "post", f"{url}/cancel", {"version": 5, "reason": "Cliente desmarcou"}),
    ]
    for headers, method, path, body in steps:
        resp = await getattr(client, method)(path, json=body, headers=headers)
        assert resp.status_code == 200, resp.text
    return b


async def test_log_action_per_change_type(
    client, auth, receptionist, receptionist2, normal_room, video_room
):
    b = await _history_scenario(client, auth, receptionist, receptionist2, normal_room, video_room)
    resp = await client.get(f"/api/bookings/{b['id']}/logs", headers=auth(receptionist))
    assert resp.status_code == 200
    logs = resp.json()
    assert [entry["action"] for entry in logs] == [
        "CREATED",
        "UPDATED",
        "RESCHEDULED",
        "MOVED_ROOM",
        "STATUS_CHANGED",
        "CANCELLED",
    ]
    assert [entry["user"]["id"] for entry in logs] == [
        receptionist.id,
        receptionist2.id,
        receptionist.id,
        receptionist2.id,
        receptionist.id,
        receptionist2.id,
    ]
    created, updated, rescheduled, moved, status, cancelled = logs
    assert created["previous_state"] is None
    assert created["new_state"]["room_name"] == "Sala 05"
    assert created["new_state"]["version"] == 1 and created["new_state"]["status"] == "CONFIRMED"
    assert (updated["previous_state"]["title"], updated["new_state"]["title"]) == (
        "Reunião de alinhamento",
        "Novo assunto",
    )
    assert (rescheduled["previous_state"]["start"], rescheduled["new_state"]["start"]) == (
        "14:00",
        "16:00",
    )
    assert (moved["previous_state"]["room_id"], moved["new_state"]["room_id"]) == (
        normal_room.id,
        video_room.id,
    )
    assert moved["new_state"]["room_name"] == "Sala 01 - Executiva"
    assert (status["previous_state"]["status"], status["new_state"]["status"]) == (
        "CONFIRMED",
        "CHECKED_IN",
    )
    assert (
        cancelled["new_state"]["status"] == "CANCELLED" and cancelled["new_state"]["version"] == 6
    )
    assert all(entry["booking_id"] == b["id"] for entry in logs)


async def test_logs_unknown_booking(client, auth, receptionist):
    resp = await client.get(
        "/api/bookings/00000000-0000-0000-0000-000000000000/logs", headers=auth(receptionist)
    )
    assert resp.status_code == 404 and error_code(resp) == "BOOKING_NOT_FOUND"


async def test_audit_listing_and_filters(
    client, auth, receptionist, receptionist2, normal_room, video_room
):
    b = await _history_scenario(client, auth, receptionist, receptionist2, normal_room, video_room)
    other = await create_booking(
        client,
        auth(receptionist2),
        normal_room,
        date=(D + timedelta(days=1)).isoformat(),
        requester_name="Outra pessoa",
        ticket_number="999",
    )
    h = auth(receptionist)

    resp = await client.get("/api/audit", headers=h)
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 7
    assert (
        entries[0]["booking_id"] == other["id"] and entries[0]["action"] == "CREATED"
    )  # mais recente primeiro
    assert [e["id"] for e in entries] == sorted((e["id"] for e in entries), reverse=True)
    first = next(e for e in entries if e["booking_id"] == b["id"])
    # resumo pelo retrato da própria entrada (o cancelamento é a última: já na sala de vídeo)
    assert first["booking_room_name"] == "Sala 01 - Executiva"
    assert first["booking_ticket_number"] == "48211"
    assert first["booking_requester_name"] == "Fulano de Tal"
    assert first["booking_date"] == D.isoformat()

    async def ids(**params) -> list[str]:
        r = await client.get("/api/audit", params=params, headers=h)
        assert r.status_code == 200, r.text
        return [e["action"] for e in r.json()]

    assert await ids(action="MOVED_ROOM") == ["MOVED_ROOM"]
    assert await ids(user_id=receptionist2.id) == ["CREATED", "CANCELLED", "MOVED_ROOM", "UPDATED"]
    assert await ids(user_id=receptionist2.id, action="CREATED") == ["CREATED"]
    assert len(await ids(date=today_local().isoformat())) == 7
    assert await ids(date=(today_local() - timedelta(days=1)).isoformat()) == []
    assert await ids(booking_date=(D + timedelta(days=1)).isoformat()) == ["CREATED"]
    assert len(await ids(booking_date=D.isoformat())) == 6
    assert await ids(limit=2) == ["CREATED", "CANCELLED"]
    assert await ids(limit=2, offset=2) == ["STATUS_CHANGED", "MOVED_ROOM"]

    resp = await client.get("/api/audit", params={"action": "NOPE"}, headers=h)
    assert resp.status_code == 422
    resp = await client.get("/api/audit", params={"limit": 0}, headers=h)
    assert resp.status_code == 422


async def test_audit_summary_and_booking_date_follow_each_entry(
    client, auth, receptionist, make_room
):
    """Criada na Sala 03 para 18/11 e depois movida para a Sala 08 em 19/11: quem procura quem
    reservou a Sala 03 para 18/11 encontra a criação, com o resumo de quando ela aconteceu."""
    sala03, sala08 = await make_room("Sala 03"), await make_room("Sala 08")
    h = auth(receptionist)
    b = await create_booking(client, h, sala03, date="2026-11-18", requester_name="Marina")
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "room_id": sala08.id, "date": "2026-11-19"},
        headers=h,
    )
    assert resp.status_code == 200
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 2, "requester_name": "Marina Souza"}, headers=h
    )
    assert resp.status_code == 200

    async def entries(**params) -> list[tuple]:
        r = await client.get("/api/audit", params=params, headers=h)
        assert r.status_code == 200, r.text
        return [
            (e["action"], e["booking_room_name"], e["booking_date"], e["booking_requester_name"])
            for e in r.json()
        ]

    assert await entries(booking_date="2026-11-18") == [
        ("MOVED_ROOM", "Sala 08", "2026-11-19", "Marina"),  # saiu de 18/11 nesta entrada
        ("CREATED", "Sala 03", "2026-11-18", "Marina"),
    ]
    assert await entries(booking_date="2026-11-19") == [
        ("UPDATED", "Sala 08", "2026-11-19", "Marina Souza"),
        ("MOVED_ROOM", "Sala 08", "2026-11-19", "Marina"),
    ]
    assert await entries(booking_date="2026-11-20") == []


async def test_audit_requires_login(client):
    resp = await client.get("/api/audit")
    assert resp.status_code == 401
