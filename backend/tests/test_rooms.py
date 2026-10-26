"""Salas (5.3): ordem da grade, cadastro admin, nomes únicos e vídeo em manutenção (RF01/RF02)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Booking, RoomType, VideoStatus
from app.timeutil import today_local
from tests.core_support import error_code

pytest_plugins = ("tests.core_fixtures",)


async def _names(client, headers, **params) -> list[str]:
    resp = await client.get("/api/rooms", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return [r["name"] for r in resp.json()]


async def test_list_order_and_inactive(client, auth, receptionist, make_room):
    await make_room("Normal B", RoomType.NORMAL, display_order=1)
    await make_room("Vídeo Z", RoomType.VIDEO, display_order=2)
    await make_room("Normal A", RoomType.NORMAL, display_order=1)
    await make_room("Vídeo Y", RoomType.VIDEO, display_order=1)
    await make_room(
        "Vídeo M", RoomType.VIDEO, display_order=0, video_status=VideoStatus.MAINTENANCE
    )
    await make_room("Inativa", RoomType.VIDEO, display_order=0, is_active=False)
    h = auth(receptionist)
    assert await _names(client, h) == ["Vídeo Y", "Vídeo Z", "Vídeo M", "Normal A", "Normal B"]
    assert await _names(client, h, include_inactive="true") == [
        "Inativa",
        "Vídeo Y",
        "Vídeo Z",
        "Vídeo M",
        "Normal A",
        "Normal B",
    ]
    resp = await client.get("/api/rooms")
    assert resp.status_code == 401


async def test_room_out_shape(client, auth, receptionist, make_room):
    room = await make_room(
        "Sala Vídeo",
        RoomType.VIDEO,
        capacity=10,
        resources=["TV", "Quadro"],
        video_status=VideoStatus.MAINTENANCE,
        maintenance_ticket_id="TI 9",
    )
    resp = await client.get(f"/api/rooms/{room.id}", headers=auth(receptionist))
    body = resp.json()
    assert body["room_type"] == "VIDEO" and body["effective_type"] == "NORMAL"
    assert body["resources"] == ["TV", "Quadro"]
    assert body["maintenance_ticket_url"] == "https://chamados.example.com/ticket/TI%209"
    resp = await client.get("/api/rooms/999", headers=auth(receptionist))
    assert resp.status_code == 404 and error_code(resp) == "ROOM_NOT_FOUND"


async def test_create_room_admin_only(client, auth, admin, receptionist, make_room, bus):
    await make_room("Vídeo 1", RoomType.VIDEO, display_order=3)
    await make_room("Normal 1", RoomType.NORMAL, display_order=10)
    payload = {
        "name": "  Sala Nova  ",
        "room_type": "VIDEO",
        "capacity": 8,
        "resources": [' TV 55" ', "", 'tv 55"', "Quadro"],
    }
    resp = await client.post("/api/rooms", json=payload, headers=auth(receptionist))
    assert resp.status_code == 403 and error_code(resp) == "FORBIDDEN"
    assert bus.envelopes == []

    resp = await client.post("/api/rooms", json=payload, headers=auth(admin))
    assert resp.status_code == 201, resp.text
    room = resp.json()
    assert room["name"] == "Sala Nova"
    assert room["resources"] == ['TV 55"', "Quadro"]
    assert room["display_order"] == 4  # fim do grupo de vídeo
    assert room["video_status"] == "OPERATIONAL" and room["effective_type"] == "VIDEO"
    assert bus.types == ["ROOM_CREATED"]
    assert bus.of_type("ROOM_CREATED")[0]["room"] == room

    resp = await client.post(
        "/api/rooms", json={"name": "Normal 2", "room_type": "NORMAL"}, headers=auth(admin)
    )
    assert resp.json()["display_order"] == 11


async def test_room_names_are_unique_case_insensitive(client, auth, admin, make_room):
    await make_room("Sala 01 - Executiva", RoomType.VIDEO)
    other = await make_room("Sala 02", RoomType.NORMAL)
    resp = await client.post(
        "/api/rooms",
        json={"name": "sala 01 - EXECUTIVA", "room_type": "NORMAL"},
        headers=auth(admin),
    )
    assert resp.status_code == 409 and error_code(resp) == "ROOM_NAME_TAKEN"
    resp = await client.patch(
        f"/api/rooms/{other.id}", json={"name": "SALA 01 - executiva"}, headers=auth(admin)
    )
    assert resp.status_code == 409 and error_code(resp) == "ROOM_NAME_TAKEN"
    # mudar só maiúsculas/minúsculas do próprio nome é permitido
    resp = await client.patch(
        f"/api/rooms/{other.id}", json={"name": "SALA 02"}, headers=auth(admin)
    )
    assert resp.status_code == 200 and resp.json()["name"] == "SALA 02"


async def test_update_room(client, auth, admin, receptionist, make_room, bus):
    room = await make_room(
        "Vídeo", RoomType.VIDEO, video_status=VideoStatus.MAINTENANCE, maintenance_ticket_id="TI-1"
    )
    resp = await client.patch(
        f"/api/rooms/{room.id}", json={"capacity": 3}, headers=auth(receptionist)
    )
    assert resp.status_code == 403

    resp = await client.patch(
        f"/api/rooms/{room.id}",
        json={"capacity": 14, "display_order": 7, "name": None},
        headers=auth(admin),
    )
    body = resp.json()
    assert (body["capacity"], body["display_order"], body["name"]) == (14, 7, "Vídeo")
    assert body["video_status"] == "MAINTENANCE"

    # VIDEO -> NORMAL zera a manutenção
    resp = await client.patch(
        f"/api/rooms/{room.id}", json={"room_type": "NORMAL"}, headers=auth(admin)
    )
    body = resp.json()
    assert body["room_type"] == "NORMAL" and body["video_status"] == "OPERATIONAL"
    assert body["maintenance_ticket_id"] is None and body["maintenance_since"] is None
    assert bus.types == ["ROOM_UPDATED", "ROOM_UPDATED"]

    # nada mudou: sem evento
    resp = await client.patch(f"/api/rooms/{room.id}", json={"capacity": 14}, headers=auth(admin))
    assert resp.status_code == 200
    assert len(bus.envelopes) == 2

    resp = await client.patch(
        f"/api/rooms/{room.id}", json={"is_active": False}, headers=auth(admin)
    )
    assert resp.json()["is_active"] is False
    resp = await client.patch("/api/rooms/999", json={"capacity": 1}, headers=auth(admin))
    assert resp.status_code == 404


async def test_maintenance_toggle_changes_effective_type_and_order(
    client, auth, receptionist, make_room, bus
):
    v1 = await make_room("Vídeo 1", RoomType.VIDEO, display_order=1)
    await make_room("Vídeo 2", RoomType.VIDEO, display_order=2)
    await make_room("Normal 1", RoomType.NORMAL, display_order=5)
    h = auth(receptionist)  # qualquer usuário logado pode marcar manutenção

    resp = await client.put(
        f"/api/rooms/{v1.id}/maintenance", json={"ticket_id": " TI-4242 "}, headers=h
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["video_status"] == "MAINTENANCE" and body["effective_type"] == "NORMAL"
    assert body["maintenance_ticket_id"] == "TI-4242"
    assert body["maintenance_ticket_url"].endswith("/TI-4242")
    since = body["maintenance_since"]
    assert since is not None
    assert await _names(client, h) == ["Vídeo 2", "Vídeo 1", "Normal 1"]

    # trocar o chamado mantém o "desde"
    resp = await client.put(
        f"/api/rooms/{v1.id}/maintenance", json={"ticket_id": "TI-5000"}, headers=h
    )
    assert resp.json()["maintenance_since"] == since

    resp = await client.delete(f"/api/rooms/{v1.id}/maintenance", headers=h)
    body = resp.json()
    assert body["video_status"] == "OPERATIONAL" and body["effective_type"] == "VIDEO"
    assert body["maintenance_ticket_id"] is None and body["maintenance_since"] is None
    assert await _names(client, h) == ["Vídeo 1", "Vídeo 2", "Normal 1"]
    assert bus.types == ["ROOM_UPDATED"] * 3
    assert bus.of_type("ROOM_UPDATED")[-1]["room"]["video_status"] == "OPERATIONAL"

    # idempotente: sem mudança, sem evento
    resp = await client.delete(f"/api/rooms/{v1.id}/maintenance", headers=h)
    assert resp.status_code == 200 and len(bus.envelopes) == 3


async def test_maintenance_only_for_video_rooms(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    resp = await client.put(
        f"/api/rooms/{normal_room.id}/maintenance", json={"ticket_id": "TI-1"}, headers=h
    )
    assert resp.status_code == 422 and error_code(resp) == "NOT_A_VIDEO_ROOM"
    resp = await client.put(
        f"/api/rooms/{normal_room.id}/maintenance", json={"ticket_id": "  "}, headers=h
    )
    assert resp.status_code == 422
    resp = await client.put("/api/rooms/999/maintenance", json={"ticket_id": "TI-1"}, headers=h)
    assert resp.status_code == 404


async def test_room_validation(client, auth, admin):
    h = auth(admin)
    resp = await client.post("/api/rooms", json={"name": "X", "room_type": "OUTRO"}, headers=h)
    assert resp.status_code == 422
    resp = await client.post(
        "/api/rooms", json={"name": "X", "room_type": "NORMAL", "capacity": -1}, headers=h
    )
    assert resp.status_code == 422
    resp = await client.post("/api/rooms", json={"name": "   ", "room_type": "NORMAL"}, headers=h)
    assert resp.status_code == 422


# ---------------------------------------------------------------- desativar sala com reservas (D1)


async def _future_booking(client, headers, room, day: str, start: str = "14:00", end: str = "15:00",
                          **extra) -> dict:
    resp = await client.post(
        "/api/bookings",
        json={"room_id": room.id, "date": day, "start": start, "end": end,
              "requester_name": "Fulana", **extra},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["booking"]


def _days_ahead(n: int) -> str:
    return (today_local() + timedelta(days=n)).isoformat()


async def test_deactivation_is_refused_while_the_room_has_upcoming_bookings(
    client, auth, admin, receptionist, make_room, bus
):
    room = await make_room("Sala 10 - Entrevistas")
    other = await make_room("Sala 11")
    h = auth(receptionist)
    later = await _future_booking(client, h, room, _days_ahead(9), "09:00", "10:00")
    sooner = await _future_booking(client, h, room, _days_ahead(2))
    checked_in = await _future_booking(client, h, room, _days_ahead(3))
    resp = await client.post(
        f"/api/bookings/{checked_in['id']}/status", json={"status": "CHECKED_IN"}, headers=h
    )
    assert resp.status_code == 200
    # não contam: canceladas, não compareceu e as que já terminaram
    cancelled = await _future_booking(client, h, room, _days_ahead(4))
    await client.post(f"/api/bookings/{cancelled['id']}/cancel", json={}, headers=h)
    no_show = await _future_booking(client, h, room, _days_ahead(5))
    await client.post(f"/api/bookings/{no_show['id']}/status", json={"status": "NO_SHOW"},
                      headers=h)
    await _future_booking(client, h, room, _days_ahead(-3))
    bus.clear()

    resp = await client.patch(
        f"/api/rooms/{room.id}", json={"is_active": False, "capacity": 4}, headers=auth(admin)
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "ROOM_HAS_FUTURE_BOOKINGS"
    assert detail["message"] == (
        "A sala tem 3 reservas ativas que ainda não aconteceram. Remaneje ou cancele essas "
        "reservas antes de desativar a sala."
    )
    assert detail["count"] == 3
    assert [b["id"] for b in detail["bookings"]] == [sooner["id"], checked_in["id"], later["id"]]
    assert detail["bookings"][0]["room_name"] == "Sala 10 - Entrevistas"  # BookingOut completo
    assert bus.envelopes == []
    resp = await client.get(f"/api/rooms/{room.id}", headers=h)
    assert resp.json()["is_active"] is True and resp.json()["capacity"] == 8  # nada mudou

    # remanejadas/canceladas, a desativação passa
    for b in (sooner, later):
        resp = await client.patch(
            f"/api/bookings/{b['id']}", json={"version": b["version"], "room_id": other.id},
            headers=h,
        )
        assert resp.status_code == 200
    resp = await client.patch(f"/api/rooms/{room.id}", json={"is_active": False},
                              headers=auth(admin))
    assert resp.status_code == 409 and resp.json()["detail"]["count"] == 1
    assert "1 reserva ativa" in resp.json()["detail"]["message"]
    resp = await client.post(f"/api/bookings/{checked_in['id']}/cancel", json={}, headers=h)
    assert resp.status_code == 200
    resp = await client.patch(f"/api/rooms/{room.id}", json={"is_active": False},
                              headers=auth(admin))
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    assert bus.types[-1] == "ROOM_UPDATED"
    # reativar não tem restrição; e quem não é admin continua vendo a sala inativa (só leitura)
    names = await _names(client, h, include_inactive="true")
    assert "Sala 10 - Entrevistas" in names


async def test_deactivation_lists_at_most_50_bookings(client, auth, admin, receptionist,
                                                      make_room, session):
    room = await make_room("Sala Lotada")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    session.add_all(
        Booking(
            room_id=room.id,
            requester_name=f"R{n:02d}",
            start_time=start + timedelta(days=n),
            end_time=start + timedelta(days=n, hours=1),
            created_by=receptionist.id,
        )
        for n in range(53)
    )
    await session.commit()
    resp = await client.patch(f"/api/rooms/{room.id}", json={"is_active": False},
                              headers=auth(admin))
    detail = resp.json()["detail"]
    assert detail["count"] == 53 and len(detail["bookings"]) == 50
    assert [b["requester_name"] for b in detail["bookings"]] == [f"R{n:02d}" for n in range(50)]


async def test_deactivation_and_concurrent_booking_are_serialized(receptionist, make_room):
    """Sem a trava de linha, uma reserva gravada no meio da desativação ficaria numa sala que some
    da grade. Com ela: a desativação espera a gravação terminar e a enxerga."""
    from app.db import SessionLocal
    from app.schemas import RoomUpdate
    from app.services import bookings as bookings_service
    from app.services import rooms as rooms_service
    from app.services.errors import BusinessError

    room = await make_room("Sala Disputada")
    async with SessionLocal() as writer, SessionLocal() as admin_session:
        await bookings_service._bookable_room(writer, room.id)  # gravação em andamento
        deactivate = asyncio.create_task(
            rooms_service.update_room(admin_session, room.id, RoomUpdate(is_active=False))
        )
        await asyncio.sleep(0.5)
        assert not deactivate.done(), "a desativação deveria esperar a gravação em andamento"
        start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
        writer.add(Booking(room_id=room.id, requester_name="Corrida", start_time=start,
                           end_time=start + timedelta(hours=1), created_by=receptionist.id))
        await writer.commit()
        with pytest.raises(BusinessError) as exc:
            await asyncio.wait_for(deactivate, 15)
        assert exc.value.code == "ROOM_HAS_FUTURE_BOOKINGS"
