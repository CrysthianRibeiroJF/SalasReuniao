"""Reservas via API: criação, validações, edição (PATCH), cancelamento e status (contrato 5.4)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import time, timedelta

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import Booking, BookingLog, RoomType
from tests.core_support import D, booking_payload, create_booking, error_code


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- criação


async def test_create_custom_booking(client, auth, receptionist, normal_room):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, attendees=4, notes="Café para 4"),
        headers=auth(receptionist),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    b = body["booking"]
    assert body["warnings"] == []
    assert b["room_id"] == normal_room.id
    assert b["room_name"] == "Sala 05"
    assert (b["date"], b["start"], b["end"]) == ("2026-10-26", "14:00", "15:00")
    assert b["start_time"] == "2026-10-26T14:00:00-03:00"
    assert b["period_type"] == "CUSTOM" and b["is_full_day"] is False
    assert b["ticket_number"] == "48211"
    assert b["ticket_url"] == "https://chamados.example.com/ticket/48211"
    assert b["status"] == "CONFIRMED" and b["version"] == 1
    assert b["created_by"] == {"id": receptionist.id, "full_name": "Ana Recepção"}
    assert b["updated_by"] is None
    assert b["attendees"] == 4 and b["notes"] == "Café para 4"
    assert b["is_relocatable"] is False and b["video_unavailable"] is False


@pytest.mark.parametrize(
    ("period", "start", "end", "full_day"),
    [
        ("MORNING", "08:00", "12:00", False),
        ("AFTERNOON", "13:00", "19:00", False),
        ("FULL_DAY", "08:00", "19:00", True),
    ],
)
async def test_presets_ignore_sent_times(
    client, auth, receptionist, normal_room, period, start, end, full_day
):
    b = await create_booking(
        client, auth(receptionist), normal_room, period_type=period, start="10:00", end="10:30"
    )
    assert (b["start"], b["end"], b["period_type"], b["is_full_day"]) == (
        start,
        end,
        period,
        full_day,
    )


async def test_preset_without_times(client, auth, receptionist, normal_room):
    payload = booking_payload(normal_room, period_type="MORNING")
    del payload["start"], payload["end"]
    resp = await client.post("/api/bookings", json=payload, headers=auth(receptionist))
    assert resp.status_code == 201
    assert resp.json()["booking"]["end"] == "12:00"


async def test_custom_requires_start_and_end(client, auth, receptionist, normal_room):
    payload = booking_payload(normal_room)
    del payload["end"]
    resp = await client.post("/api/bookings", json=payload, headers=auth(receptionist))
    assert resp.status_code == 422
    assert error_code(resp) == "INVALID_TIME"


@pytest.mark.parametrize(
    ("start", "end"), [("07:30", "08:30"), ("18:30", "19:30"), ("06:00", "07:00")]
)
async def test_outside_operating_hours(client, auth, receptionist, normal_room, start, end):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, start=start, end=end),
        headers=auth(receptionist),
    )
    assert resp.status_code == 422
    assert error_code(resp) == "OUTSIDE_OPERATING_HOURS"
    assert "08:00" in resp.json()["detail"]["message"]


async def test_operating_hours_edges_are_allowed(client, auth, receptionist, normal_room):
    await create_booking(client, auth(receptionist), normal_room, start="08:00", end="08:30")
    await create_booking(client, auth(receptionist), normal_room, start="18:30", end="19:00")


@pytest.mark.parametrize(
    ("start", "end"), [("14:03", "15:00"), ("14:00", "14:58"), ("14:00:30", "15:00")]
)
async def test_five_minute_alignment(client, auth, receptionist, normal_room, start, end):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, start=start, end=end),
        headers=auth(receptionist),
    )
    assert resp.status_code == 422
    assert error_code(resp) == "INVALID_TIME"


async def test_five_minute_steps_accepted(client, auth, receptionist, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room, start="14:05", end="14:35")
    assert (b["start"], b["end"]) == ("14:05", "14:35")


@pytest.mark.parametrize(("start", "end"), [("15:00", "14:00"), ("14:00", "14:00")])
async def test_end_must_be_after_start(client, auth, receptionist, normal_room, start, end):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, start=start, end=end),
        headers=auth(receptionist),
    )
    assert resp.status_code == 422
    assert error_code(resp) == "INVALID_INTERVAL"


async def test_room_not_found_and_inactive(client, auth, receptionist, make_room):
    inactive = await make_room("Sala Antiga", is_active=False)
    resp = await client.post(
        "/api/bookings", json=booking_payload(inactive), headers=auth(receptionist)
    )
    assert resp.status_code == 422 and error_code(resp) == "ROOM_INACTIVE"
    payload = booking_payload(inactive, room_id=9999)
    resp = await client.post("/api/bookings", json=payload, headers=auth(receptionist))
    assert resp.status_code == 404 and error_code(resp) == "ROOM_NOT_FOUND"


async def test_schema_validation_keeps_fastapi_format(client, auth, receptionist, normal_room):
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(normal_room, requester_name="   "),
        headers=auth(receptionist),
    )
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)


async def test_ticket_hash_prefix_is_normalized(client, auth, receptionist, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room, ticket_number=" #48299 ")
    assert b["ticket_number"] == "48299"
    assert b["ticket_url"].endswith("/48299")


async def test_ticket_is_optional(client, auth, receptionist, normal_room):
    payload = booking_payload(normal_room)
    del payload["ticket_number"]
    resp = await client.post("/api/bookings", json=payload, headers=auth(receptionist))
    assert resp.status_code == 201
    assert resp.json()["booking"]["ticket_number"] is None
    assert resp.json()["booking"]["ticket_url"] is None


async def test_requires_authentication(client, normal_room):
    resp = await client.post("/api/bookings", json=booking_payload(normal_room))
    assert resp.status_code == 401
    resp = await client.get(
        "/api/bookings", params={"date": D.isoformat()}, headers={"Authorization": "Bearer xyz"}
    )
    assert resp.status_code == 401
    assert error_code(resp) == "UNAUTHORIZED"


# ---------------------------------------------------------------- consulta


async def test_get_booking_and_not_found(client, auth, receptionist, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room)
    resp = await client.get(f"/api/bookings/{b['id']}", headers=auth(receptionist))
    assert resp.status_code == 200 and resp.json()["id"] == b["id"]
    resp = await client.get(
        "/api/bookings/00000000-0000-0000-0000-000000000000", headers=auth(receptionist)
    )
    assert resp.status_code == 404 and error_code(resp) == "BOOKING_NOT_FOUND"
    resp = await client.get("/api/bookings/nao-e-uuid", headers=auth(receptionist))
    assert resp.status_code == 422


async def test_list_by_day_and_range(client, auth, receptionist, video_room, normal_room):
    h = auth(receptionist)
    late = await create_booking(client, h, video_room, start="16:00", end="17:00")
    early_normal = await create_booking(client, h, normal_room, start="09:00", end="10:00")
    early_video = await create_booking(client, h, video_room, start="09:00", end="10:00")
    next_day = await create_booking(client, h, video_room, date=(D + timedelta(days=1)).isoformat())
    cancelled = await create_booking(client, h, normal_room, start="11:00", end="12:00")
    resp = await client.post(f"/api/bookings/{cancelled['id']}/cancel", json={}, headers=h)
    assert resp.status_code == 200

    resp = await client.get("/api/bookings", params={"date": D.isoformat()}, headers=h)
    assert resp.status_code == 200
    # ordem: start_time, room_id (video_room foi criada antes => id menor)
    assert [b["id"] for b in resp.json()] == [early_video["id"], early_normal["id"], late["id"]]

    resp = await client.get(
        "/api/bookings", params={"date": D.isoformat(), "include_cancelled": "true"}, headers=h
    )
    assert cancelled["id"] in [b["id"] for b in resp.json()]

    resp = await client.get(
        "/api/bookings",
        params={"start_date": D.isoformat(), "end_date": (D + timedelta(days=1)).isoformat()},
        headers=h,
    )
    assert [b["id"] for b in resp.json()][-1] == next_day["id"]
    assert len(resp.json()) == 4


async def test_list_range_validation(client, auth, receptionist):
    h = auth(receptionist)
    resp = await client.get(
        "/api/bookings",
        params={"start_date": D.isoformat(), "end_date": (D + timedelta(days=30)).isoformat()},
        headers=h,
    )
    assert resp.status_code == 200  # 31 dias inclusivos
    resp = await client.get(
        "/api/bookings",
        params={"start_date": D.isoformat(), "end_date": (D + timedelta(days=31)).isoformat()},
        headers=h,
    )
    assert resp.status_code == 422 and error_code(resp) == "DATE_RANGE_TOO_LARGE"
    resp = await client.get(
        "/api/bookings",
        params={"start_date": D.isoformat(), "end_date": (D - timedelta(days=1)).isoformat()},
        headers=h,
    )
    assert resp.status_code == 422 and error_code(resp) == "INVALID_DATE_RANGE"
    resp = await client.get("/api/bookings", headers=h)
    assert resp.status_code == 422 and error_code(resp) == "INVALID_DATE_RANGE"


# ---------------------------------------------------------------- edição


async def test_patch_reschedule_bumps_version(
    client, auth, receptionist, receptionist2, normal_room
):
    b = await create_booking(client, auth(receptionist), normal_room)
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "start": "16:00", "end": "17:30"},
        headers=auth(receptionist2),
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()["booking"]
    assert (updated["start"], updated["end"], updated["version"]) == ("16:00", "17:30", 2)
    assert updated["updated_by"] == {"id": receptionist2.id, "full_name": "Bia Recepção"}
    assert updated["created_by"]["id"] == receptionist.id
    assert updated["ticket_number"] == "48211"  # campos não enviados não mudam


async def test_patch_stale_version(client, auth, receptionist, receptionist2, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room)
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 1, "title": "Novo"}, headers=auth(receptionist)
    )
    assert resp.status_code == 200
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "title": "Outro"},
        headers=auth(receptionist2),
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STALE_VERSION"
    assert detail["current"]["version"] == 2 and detail["current"]["title"] == "Novo"
    assert "outra pessoa" in detail["message"]


async def test_concurrent_patches_with_same_version(
    client, auth, receptionist, receptionist2, normal_room
):
    """A segunda escrita espera a primeira (SELECT ... FOR UPDATE) e enxerga a versão nova."""
    b = await create_booking(client, auth(receptionist), normal_room)
    responses = await asyncio.gather(
        client.patch(
            f"/api/bookings/{b['id']}",
            json={"version": 1, "title": "A"},
            headers=auth(receptionist),
        ),
        client.patch(
            f"/api/bookings/{b['id']}",
            json={"version": 1, "title": "B"},
            headers=auth(receptionist2),
        ),
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert error_code(loser) == "STALE_VERSION"


async def test_noop_patch_does_not_log_or_bump(client, auth, receptionist, normal_room, session):
    b = await create_booking(client, auth(receptionist), normal_room)
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={
            "version": 1,
            "room_id": normal_room.id,
            "date": D.isoformat(),
            "start": "14:00",
            "end": "15:00",
            "ticket_number": "#48211",
            "title": "Reunião de alinhamento",
            "requires_video": False,
        },
        headers=auth(receptionist),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["booking"]["version"] == 1
    assert resp.json()["booking"]["updated_by"] is None
    assert await _count(session, BookingLog) == 1  # só o CREATED


async def test_patch_null_clears_optional_fields(client, auth, receptionist, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room, attendees=5, notes="obs")
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "ticket_number": None, "title": None, "attendees": None, "notes": "  "},
        headers=auth(receptionist),
    )
    assert resp.status_code == 200
    u = resp.json()["booking"]
    assert u["ticket_number"] is None and u["ticket_url"] is None
    assert u["title"] is None and u["attendees"] is None and u["notes"] is None
    assert u["requester_name"] == "Fulano de Tal"
    assert u["version"] == 2


async def test_patch_required_fields(client, auth, receptionist, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room)
    # null em campo obrigatório = não muda
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "requester_name": None, "room_id": None, "requires_video": None},
        headers=auth(receptionist),
    )
    assert resp.status_code == 200 and resp.json()["booking"]["version"] == 1
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "requester_name": "   "},
        headers=auth(receptionist),
    )
    assert resp.status_code == 422 and error_code(resp) == "INVALID_REQUESTER"
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "requester_name": " Ciclano "},
        headers=auth(receptionist),
    )
    assert resp.json()["booking"]["requester_name"] == "Ciclano"


async def test_patch_validates_new_times(client, auth, receptionist, normal_room):
    b = await create_booking(client, auth(receptionist), normal_room)
    h = auth(receptionist)
    url = f"/api/bookings/{b['id']}"
    resp = await client.patch(url, json={"version": 1, "end": "19:30"}, headers=h)
    assert error_code(resp) == "OUTSIDE_OPERATING_HOURS"
    resp = await client.patch(url, json={"version": 1, "start": "14:02"}, headers=h)
    assert error_code(resp) == "INVALID_TIME"
    resp = await client.patch(url, json={"version": 1, "start": "15:30"}, headers=h)
    assert error_code(resp) == "INVALID_INTERVAL"


async def test_patch_period_changes(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room)
    url = f"/api/bookings/{b['id']}"
    resp = await client.patch(url, json={"version": 1, "period_type": "FULL_DAY"}, headers=h)
    u = resp.json()["booking"]
    assert (u["start"], u["end"], u["is_full_day"], u["period_type"]) == (
        "08:00",
        "19:00",
        True,
        "FULL_DAY",
    )
    # mudar só a data mantém o preset
    resp = await client.patch(url, json={"version": 2, "date": "2026-10-27"}, headers=h)
    u = resp.json()["booking"]
    assert (u["date"], u["period_type"], u["start"]) == ("2026-10-27", "FULL_DAY", "08:00")
    # start/end sem period_type numa reserva de período fixo => Horário Específico
    resp = await client.patch(url, json={"version": 3, "start": "09:00", "end": "10:00"}, headers=h)
    u = resp.json()["booking"]
    assert (u["period_type"], u["is_full_day"], u["start"], u["end"]) == (
        "CUSTOM",
        False,
        "09:00",
        "10:00",
    )
    # preset explícito ignora start/end enviados
    resp = await client.patch(
        url,
        json={"version": 4, "period_type": "AFTERNOON", "start": "09:00", "end": "10:00"},
        headers=h,
    )
    u = resp.json()["booking"]
    assert (u["period_type"], u["start"], u["end"]) == ("AFTERNOON", "13:00", "19:00")


# ---------------------------------------------------------------- horários dos períodos fixos (R4)


@pytest.fixture
def longer_mornings_shorter_days(monkeypatch):
    """A recepção muda o horário depois que as reservas foram criadas (README, seção 5)."""

    def apply() -> None:
        monkeypatch.setattr(settings, "day_end", time(18, 0))
        monkeypatch.setattr(settings, "morning_end", time(12, 30))

    return apply


async def test_data_or_room_only_edit_keeps_preset_times_after_hours_change(
    client, auth, receptionist, normal_room, video_room, session, longer_mornings_shorter_days
):
    h = auth(receptionist)
    full = await create_booking(client, h, normal_room, period_type="FULL_DAY")
    morning = await create_booking(
        client, h, normal_room, period_type="MORNING", date="2026-10-27"
    )
    longer_mornings_shorter_days()

    resp = await client.patch(
        f"/api/bookings/{full['id']}", json={"version": 1, "title": "Assunto corrigido"},
        headers=h,
    )
    u = resp.json()["booking"]
    assert (u["start"], u["end"], u["is_full_day"], u["period_type"]) == (
        "08:00", "19:00", True, "FULL_DAY"
    )
    resp = await client.patch(
        f"/api/bookings/{full['id']}", json={"version": 2, "room_id": video_room.id}, headers=h
    )  # o payload do arrastar/"Mover para" da grade
    u = resp.json()["booking"]
    assert (u["room_id"], u["start"], u["end"]) == (video_room.id, "08:00", "19:00")
    resp = await client.patch(
        f"/api/bookings/{morning['id']}", json={"version": 1, "requester_name": "Bianca"},
        headers=h,
    )
    assert (resp.json()["booking"]["start"], resp.json()["booking"]["end"]) == ("08:00", "12:00")

    actions = (
        await session.scalars(
            select(BookingLog.action)
            .where(BookingLog.booking_id.in_([uuid.UUID(full["id"]), uuid.UUID(morning["id"])]))
            .order_by(BookingLog.id)
        )
    ).all()
    assert actions == ["CREATED", "CREATED", "UPDATED", "MOVED_ROOM", "UPDATED"]


async def test_data_only_edit_does_not_conflict_with_neighbour_after_hours_change(
    client, auth, receptionist, normal_room, monkeypatch
):
    h = auth(receptionist)
    morning = await create_booking(client, h, normal_room, period_type="MORNING")
    await create_booking(client, h, normal_room, start="12:00", end="12:30")
    monkeypatch.setattr(settings, "morning_end", time(12, 30))
    resp = await client.patch(
        f"/api/bookings/{morning['id']}", json={"version": 1, "notes": "trazer projetor"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["booking"]["end"] == "12:00"


async def test_changing_date_or_period_uses_the_current_hours(
    client, auth, receptionist, normal_room, longer_mornings_shorter_days
):
    """Com period_type ou date na edição, o período fixo é recalculado pela configuração atual."""
    h = auth(receptionist)
    full = await create_booking(client, h, normal_room, period_type="FULL_DAY")
    longer_mornings_shorter_days()
    url = f"/api/bookings/{full['id']}"
    resp = await client.patch(url, json={"version": 1, "date": "2026-10-28"}, headers=h)
    u = resp.json()["booking"]
    assert (u["date"], u["start"], u["end"], u["period_type"]) == (
        "2026-10-28", "08:00", "18:00", "FULL_DAY"
    )
    resp = await client.patch(url, json={"version": 2, "period_type": "MORNING"}, headers=h)
    u = resp.json()["booking"]
    assert (u["start"], u["end"], u["is_full_day"]) == ("08:00", "12:30", False)


async def test_patch_move_room(client, auth, receptionist, normal_room, video_room, make_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room)
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 1, "room_id": video_room.id}, headers=h
    )
    assert resp.status_code == 200
    u = resp.json()["booking"]
    assert (u["room_id"], u["room_name"]) == (video_room.id, "Sala 01 - Executiva")
    assert u["is_relocatable"] is True  # sem vídeo ocupando sala de vídeo

    inactive = await make_room("Desativada", is_active=False)
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 2, "room_id": inactive.id}, headers=h
    )
    assert resp.status_code == 422 and error_code(resp) == "ROOM_INACTIVE"
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 2, "room_id": 4242}, headers=h
    )
    assert resp.status_code == 404 and error_code(resp) == "ROOM_NOT_FOUND"


async def test_patch_unknown_booking(client, auth, receptionist):
    resp = await client.patch(
        "/api/bookings/00000000-0000-0000-0000-000000000001",
        json={"version": 1, "title": "x"},
        headers=auth(receptionist),
    )
    assert resp.status_code == 404 and error_code(resp) == "BOOKING_NOT_FOUND"


async def test_data_only_edit_in_deactivated_room(
    client, auth, admin, receptionist, normal_room, session
):
    b = await create_booking(client, auth(receptionist), normal_room)
    # a API não desativa sala com reservas futuras (409 ROOM_HAS_FUTURE_BOOKINGS): este é o caso
    # de dados antigos (sala desativada antes dessa regra existir)
    normal_room.is_active = False
    await session.commit()
    url = f"/api/bookings/{b['id']}"
    resp = await client.patch(
        url, json={"version": 1, "title": "Só dados"}, headers=auth(receptionist)
    )
    assert resp.status_code == 200
    resp = await client.patch(
        url, json={"version": 2, "start": "15:00", "end": "16:00"}, headers=auth(receptionist)
    )
    assert resp.status_code == 422 and error_code(resp) == "ROOM_INACTIVE"


# ---------------------------------------------------------------- cancelamento e status


async def test_cancel_then_rebook_same_slot(
    client, auth, receptionist, receptionist2, normal_room, session
):
    b = await create_booking(client, auth(receptionist), normal_room)
    resp = await client.post(
        f"/api/bookings/{b['id']}/cancel",
        json={"version": 1, "reason": " Reunião adiada "},
        headers=auth(receptionist2),
    )
    assert resp.status_code == 200
    c = resp.json()
    assert c["status"] == "CANCELLED" and c["version"] == 2
    assert c["cancel_reason"] == "Reunião adiada" and c["cancelled_at"] is not None
    assert c["is_relocatable"] is False

    again = await create_booking(client, auth(receptionist2), normal_room)
    assert again["id"] != b["id"]
    assert await _count(session, Booking) == 2


async def test_cancel_rules(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room)
    resp = await client.post(f"/api/bookings/{b['id']}/cancel", json={"version": 7}, headers=h)
    assert resp.status_code == 409 and error_code(resp) == "STALE_VERSION"
    resp = await client.post(f"/api/bookings/{b['id']}/cancel", headers=h)  # corpo opcional
    assert resp.status_code == 200
    resp = await client.post(f"/api/bookings/{b['id']}/cancel", json={}, headers=h)
    assert resp.status_code == 409 and error_code(resp) == "BOOKING_NOT_EDITABLE"
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 2, "title": "x"}, headers=h
    )
    assert resp.status_code == 409 and error_code(resp) == "BOOKING_NOT_EDITABLE"
    resp = await client.post(
        f"/api/bookings/{b['id']}/status", json={"status": "CHECKED_IN"}, headers=h
    )
    assert resp.status_code == 409 and error_code(resp) == "BOOKING_NOT_EDITABLE"


async def test_status_changes(client, auth, receptionist, normal_room, session):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room)
    url = f"/api/bookings/{b['id']}/status"
    resp = await client.post(url, json={"version": 1, "status": "CHECKED_IN"}, headers=h)
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["version"]) == ("CHECKED_IN", 2)
    resp = await client.post(
        url, json={"status": "CHECKED_IN"}, headers=h
    )  # mesmo status: nada muda
    assert resp.json()["version"] == 2
    resp = await client.post(url, json={"version": 1, "status": "NO_SHOW"}, headers=h)
    assert resp.status_code == 409 and error_code(resp) == "STALE_VERSION"
    resp = await client.post(url, json={"version": 2, "status": "NO_SHOW"}, headers=h)
    assert resp.json()["status"] == "NO_SHOW"
    resp = await client.post(url, json={"status": "CONFIRMED"}, headers=h)
    assert resp.json()["status"] == "CONFIRMED" and resp.json()["version"] == 4
    resp = await client.post(url, json={"status": "CANCELLED"}, headers=h)
    assert resp.status_code == 422  # cancelamento só pela rota própria
    assert await _count(session, BookingLog) == 4


async def test_video_room_in_maintenance_flags(client, auth, receptionist, make_room):
    room = await make_room("Sala Vídeo", RoomType.VIDEO)
    h = auth(receptionist)
    b = await create_booking(client, h, room, requires_video=True)
    assert b["video_unavailable"] is False
    resp = await client.put(
        f"/api/rooms/{room.id}/maintenance", json={"ticket_id": "TI-1"}, headers=h
    )
    assert resp.status_code == 200
    resp = await client.get(f"/api/bookings/{b['id']}", headers=h)
    assert resp.json()["video_unavailable"] is True
