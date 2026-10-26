"""Conflitos (409 BOOKING_CONFLICT com alternativas) e avisos não bloqueantes (regras 5 e 8)."""

from __future__ import annotations

from app.models import RoomType, VideoStatus
from tests.core_support import booking_payload, create_booking, error_code


async def _rooms(make_room):
    """Cenário: 3 salas de vídeo (uma em manutenção), 3 normais (uma inativa)."""
    return {
        "v1": await make_room("Vídeo 1", RoomType.VIDEO, capacity=10),
        "v2": await make_room("Vídeo 2", RoomType.VIDEO, capacity=10),
        "v3": await make_room("Vídeo 3", RoomType.VIDEO, capacity=10),
        "vm": await make_room(
            "Vídeo Manutenção",
            RoomType.VIDEO,
            video_status=VideoStatus.MAINTENANCE,
            maintenance_ticket_id="TI-77",
        ),
        "n1": await make_room("Normal 1", RoomType.NORMAL, capacity=4),
        "n2": await make_room("Normal 2", RoomType.NORMAL, capacity=6),
        "off": await make_room("Normal Inativa", RoomType.NORMAL, is_active=False),
    }


# ---------------------------------------------------------------- conflitos


async def test_full_day_conflict_offers_rooms_free_all_day(client, auth, receptionist, make_room):
    r = await _rooms(make_room)
    h = auth(receptionist)
    blocking = await create_booking(
        client, h, r["v1"], start="15:00", end="16:00", requester_name="Reunião das 15h"
    )
    await create_booking(
        client, h, r["v3"], start="09:00", end="10:00"
    )  # v3 não está livre o dia todo
    await create_booking(client, h, r["n2"], start="18:00", end="19:00")  # n2 também não

    resp = await client.post(
        "/api/bookings", json=booking_payload(r["v1"], period_type="FULL_DAY"), headers=h
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "BOOKING_CONFLICT"
    assert "Reunião das 15h" in detail["message"] and "15:00" in detail["message"]
    assert [c["id"] for c in detail["conflicts"]] == [blocking["id"]]
    assert detail["conflicts"][0]["room_name"] == "Vídeo 1"
    # sem vídeo: salas efetivamente NORMAIS primeiro (inclui a de vídeo em manutenção), depois VIDEO
    assert [a["name"] for a in detail["alternatives"]] == [
        "Vídeo Manutenção",
        "Normal 1",
        "Vídeo 2",
    ]
    assert detail["alternatives"][0]["effective_type"] == "NORMAL"
    assert set(detail["alternatives"][0]) == {
        "id",
        "name",
        "room_type",
        "effective_type",
        "capacity",
    }

    resp = await client.post(
        "/api/bookings",
        json=booking_payload(r["v1"], period_type="FULL_DAY", requires_video=True),
        headers=h,
    )
    assert [a["name"] for a in resp.json()["detail"]["alternatives"]] == [
        "Vídeo 2",
        "Vídeo Manutenção",
        "Normal 1",
    ]


async def test_partial_overlaps_and_adjacent_slots(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    await create_booking(client, h, normal_room, start="14:00", end="15:00")
    await create_booking(client, h, normal_room, start="15:00", end="16:00")  # adjacente: ok
    await create_booking(client, h, normal_room, start="13:00", end="14:00")  # adjacente: ok
    for start, end in [
        ("14:30", "15:30"),
        ("13:30", "14:05"),
        ("15:55", "17:00"),
        ("12:00", "17:00"),
    ]:
        resp = await client.post(
            "/api/bookings", json=booking_payload(normal_room, start=start, end=end), headers=h
        )
        assert resp.status_code == 409 and error_code(resp) == "BOOKING_CONFLICT", (start, end)
    resp = await client.post(
        "/api/bookings", json=booking_payload(normal_room, start="12:00", end="17:00"), headers=h
    )
    assert len(resp.json()["detail"]["conflicts"]) == 3
    assert "3 reservas" in resp.json()["detail"]["message"]


async def test_patch_does_not_conflict_with_itself(
    client, auth, receptionist, normal_room, video_room
):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room, start="14:00", end="15:00")
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 1, "start": "14:30", "end": "16:00"}, headers=h
    )
    assert resp.status_code == 200

    other = await create_booking(client, h, video_room, start="14:00", end="15:00")
    resp = await client.patch(
        f"/api/bookings/{other['id']}", json={"version": 1, "room_id": normal_room.id}, headers=h
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert [c["id"] for c in detail["conflicts"]] == [b["id"]]
    # a sala atual da reserva remanejada conta como livre (ela mesma não bloqueia)
    assert [a["id"] for a in detail["alternatives"]] == [video_room.id]


async def test_cancelled_booking_does_not_conflict(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room, period_type="FULL_DAY")
    await client.post(f"/api/bookings/{b['id']}/cancel", json={}, headers=h)
    await create_booking(client, h, normal_room, start="10:00", end="11:00")


async def test_conflict_detail_is_complete_booking(client, auth, receptionist, video_room):
    h = auth(receptionist)
    await create_booking(client, h, video_room, ticket_number="#77")
    resp = await client.post("/api/bookings", json=booking_payload(video_room), headers=h)
    conflict = resp.json()["detail"]["conflicts"][0]
    assert conflict["ticket_number"] == "77"
    assert conflict["start"] == "14:00" and conflict["created_by"]["id"] == receptionist.id


# ---------------------------------------------------------------- avisos


def _warning(body: dict, code: str) -> dict | None:
    return next((w for w in body["warnings"] if w["code"] == code), None)


async def test_video_room_not_needed_lists_free_normal_rooms(client, auth, receptionist, make_room):
    r = await _rooms(make_room)
    h = auth(receptionist)
    await create_booking(client, h, r["n2"], start="14:00", end="15:00")  # n2 ocupada no horário
    resp = await client.post("/api/bookings", json=booking_payload(r["v1"]), headers=h)
    assert resp.status_code == 201
    body = resp.json()
    w = _warning(body, "VIDEO_ROOM_NOT_NEEDED")
    assert w is not None and "salas normais livres" in w["message"]
    # salas efetivamente normais e ativas, na ordem da grade (manutenção conta como normal)
    assert [x["name"] for x in w["data"]["free_normal_rooms"]] == ["Vídeo Manutenção", "Normal 1"]
    assert body["booking"]["is_relocatable"] is True


async def test_video_room_not_needed_absent_when_no_normal_room_free(
    client, auth, receptionist, video_room, normal_room
):
    h = auth(receptionist)
    await create_booking(client, h, normal_room, start="13:00", end="16:00")
    resp = await client.post("/api/bookings", json=booking_payload(video_room), headers=h)
    assert resp.status_code == 201
    assert resp.json()["warnings"] == []
    assert resp.json()["booking"]["is_relocatable"] is True  # o selo continua (RF06)


async def test_no_video_warning_when_video_is_needed(
    client, auth, receptionist, video_room, normal_room
):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(video_room, requires_video=True),
        headers=auth(receptionist),
    )
    assert resp.json()["warnings"] == []
    assert resp.json()["booking"]["is_relocatable"] is False


async def test_video_unavailable_in_maintenance_room(client, auth, receptionist, make_room):
    r = await _rooms(make_room)
    h = auth(receptionist)
    await create_booking(client, h, r["v2"], start="14:00", end="15:00", requires_video=True)
    resp = await client.post(
        "/api/bookings", json=booking_payload(r["vm"], requires_video=True), headers=h
    )
    assert resp.status_code == 201
    body = resp.json()
    w = _warning(body, "VIDEO_UNAVAILABLE")
    assert w is not None and "manutenção" in w["message"]
    assert [x["name"] for x in w["data"]["free_video_rooms"]] == ["Vídeo 1", "Vídeo 3"]
    assert body["booking"]["video_unavailable"] is True
    assert _warning(body, "VIDEO_ROOM_NOT_NEEDED") is None


async def test_video_unavailable_in_normal_room(
    client, auth, receptionist, normal_room, video_room
):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, requires_video=True),
        headers=auth(receptionist),
    )
    w = _warning(resp.json(), "VIDEO_UNAVAILABLE")
    assert "não tem videoconferência" in w["message"]
    assert [x["id"] for x in w["data"]["free_video_rooms"]] == [video_room.id]


async def test_over_capacity(client, auth, receptionist, normal_room, make_room):
    h = auth(receptionist)
    resp = await client.post(
        "/api/bookings", json=booking_payload(normal_room, attendees=9), headers=h
    )
    w = _warning(resp.json(), "OVER_CAPACITY")
    assert w["data"] == {"capacity": 6, "attendees": 9}
    assert "6 pessoas" in w["message"]

    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, attendees=6, start="16:00", end="17:00"),
        headers=h,
    )
    assert resp.json()["warnings"] == []
    unlimited = await make_room("Sem capacidade definida", capacity=0)
    resp = await client.post(
        "/api/bookings", json=booking_payload(unlimited, attendees=50), headers=h
    )
    assert resp.json()["warnings"] == []


async def test_warnings_returned_on_patch(client, auth, receptionist, normal_room, video_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room)
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 1, "room_id": video_room.id}, headers=h
    )
    body = resp.json()
    # a própria reserva saiu da sala normal: ela aparece como livre para remanejar de volta
    w = _warning(body, "VIDEO_ROOM_NOT_NEEDED")
    assert [x["id"] for x in w["data"]["free_normal_rooms"]] == [normal_room.id]
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 2, "requires_video": True, "attendees": 20},
        headers=h,
    )
    assert [w["code"] for w in resp.json()["warnings"]] == ["OVER_CAPACITY"]
