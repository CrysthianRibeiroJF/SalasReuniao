"""Integração dos serviços REST com o tempo real: soft locks (regra 4/7) e eventos após o commit."""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Booking
from tests.core_support import D, booking_payload, create_booking, error_code

pytest_plugins = ("tests.core_fixtures",)


# ---------------------------------------------------------------- soft locks


async def test_other_users_lock_blocks_booking(
    client, auth, receptionist, receptionist2, video_room, lock_store, bus, session
):
    lock = lock_store.add(receptionist2, video_room.id, time(14, 30), time(15, 30))
    resp = await client.post(
        "/api/bookings", json=booking_payload(video_room), headers=auth(receptionist)
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "SLOT_LOCKED"
    assert detail["message"] == "Bia Recepção está reservando este horário agora."
    assert [lk["lock_id"] for lk in detail["locks"]] == [lock.lock_id]
    assert detail["locks"][0]["user"] == {"id": receptionist2.id, "full_name": "Bia Recepção"}
    assert detail["locks"][0]["start"] == "14:30"
    assert await session.scalar(select(Booking.id)) is None
    assert bus.envelopes == []
    assert lock.lock_id in lock_store.locks  # a trava alheia continua


async def test_message_lists_every_locking_user(
    client, auth, make_user, receptionist, receptionist2, video_room, lock_store
):
    carla = await make_user("carla", "Carla")
    lock_store.add(receptionist2, video_room.id, time(14), time(14, 30))
    lock_store.add(carla, video_room.id, time(14, 30), time(15))
    resp = await client.post(
        "/api/bookings", json=booking_payload(video_room), headers=auth(receptionist)
    )
    assert (
        resp.json()["detail"]["message"]
        == "Bia Recepção e Carla estão reservando este horário agora."
    )
    assert len(resp.json()["detail"]["locks"]) == 2


async def test_non_overlapping_locks_do_not_block(
    client, auth, receptionist, receptionist2, video_room, normal_room, lock_store
):
    lock_store.add(receptionist2, normal_room.id, time(14), time(15))  # outra sala
    lock_store.add(receptionist2, video_room.id, time(15), time(16))  # adjacente
    lock_store.add(
        receptionist2, video_room.id, time(14), time(15), d=D.replace(day=27)
    )  # outro dia
    await create_booking(client, auth(receptionist), video_room)
    assert len(lock_store.locks) == 3


async def test_own_lock_is_released_after_save(
    client, auth, receptionist, receptionist2, video_room, lock_store, bus
):
    own = lock_store.add(receptionist, video_room.id, time(14), time(15))
    other_room_own = lock_store.add(
        receptionist, video_room.id, time(9), time(10)
    )  # lock_id enviada
    elsewhere = lock_store.add(receptionist2, video_room.id, time(16), time(17))
    resp = await client.post(
        "/api/bookings",
        json=booking_payload(video_room, lock_id=other_room_own.lock_id),
        headers=auth(receptionist),
    )
    assert resp.status_code == 201, resp.text
    assert set(lock_store.locks) == {elsewhere.lock_id}

    assert bus.types == ["SLOT_UNLOCKED", "SLOT_UNLOCKED", "BOOKING_CREATED"]
    unlocked = bus.of_type("SLOT_UNLOCKED")
    assert {u["lock"]["lock_id"] for u in unlocked} == {own.lock_id, other_room_own.lock_id}
    assert {u["reason"] for u in unlocked} == {"saved"}
    assert unlocked[0]["lock"]["user"]["id"] == receptionist.id


async def test_lock_id_of_another_user_is_not_released(
    client, auth, receptionist, receptionist2, video_room, lock_store
):
    foreign = lock_store.add(receptionist2, video_room.id, time(9), time(10))
    await create_booking(client, auth(receptionist), video_room, lock_id=foreign.lock_id)
    assert foreign.lock_id in lock_store.locks


async def test_patch_respects_locks(
    client, auth, receptionist, receptionist2, video_room, normal_room, lock_store, bus
):
    b = await create_booking(client, auth(receptionist), video_room)
    bus.clear()
    # trava de outra recepcionista ligada à MESMA reserva (popover aberto): não bloqueia
    editing = lock_store.add(receptionist2, normal_room.id, time(14), time(15), booking_id=b["id"])
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "room_id": normal_room.id},
        headers=auth(receptionist),
    )
    assert resp.status_code == 200, resp.text
    assert editing.lock_id in lock_store.locks  # trava de outro usuário nunca é liberada pelo save

    # trava alheia sem booking_id no destino: bloqueia
    lock_store.add(receptionist2, video_room.id, time(16), time(17))
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 2, "room_id": video_room.id, "start": "16:00", "end": "17:00"},
        headers=auth(receptionist),
    )
    assert resp.status_code == 409 and error_code(resp) == "SLOT_LOCKED"

    # edição só de dados não consulta travas
    lock_store.add(receptionist2, normal_room.id, time(14), time(14, 30))
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 2, "title": "Só o assunto"},
        headers=auth(receptionist),
    )
    assert resp.status_code == 200


async def test_patch_releases_own_locks_on_old_and_new_slot(
    client, auth, receptionist, video_room, normal_room, lock_store, bus
):
    b = await create_booking(client, auth(receptionist), video_room)
    old_slot = lock_store.add(receptionist, video_room.id, time(14), time(15), booking_id=b["id"])
    new_slot = lock_store.add(receptionist, normal_room.id, time(14), time(15), booking_id=b["id"])
    bus.clear()
    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={"version": 1, "room_id": normal_room.id, "lock_id": new_slot.lock_id},
        headers=auth(receptionist),
    )
    assert resp.status_code == 200
    assert lock_store.locks == {}
    assert bus.types == ["SLOT_UNLOCKED", "SLOT_UNLOCKED", "BOOKING_UPDATED"]
    assert {e["lock"]["lock_id"] for e in bus.of_type("SLOT_UNLOCKED")} == {
        old_slot.lock_id,
        new_slot.lock_id,
    }


async def test_relocating_a_conflicting_meeting_keeps_the_composer_lock(
    client, auth, receptionist, receptionist2, video_room, normal_room, lock_store, bus
):
    """Fluxo 5.2 opção 2: Ana monta um Dia Inteiro na sala (trava 08-19, sem booking_id) e
    remaneja a reunião das 15h que estava no caminho. A trava do Dia Inteiro não pode cair."""
    meeting = await create_booking(
        client, auth(receptionist2), video_room, start="15:00", end="16:00"
    )
    composer = lock_store.add(receptionist, video_room.id, time(8), time(19))
    bus.clear()
    resp = await client.patch(
        f"/api/bookings/{meeting['id']}",
        json={"version": 1, "room_id": normal_room.id},  # o payload do "Mover para"
        headers=auth(receptionist),
    )
    assert resp.status_code == 200, resp.text
    assert composer.lock_id in lock_store.locks
    assert bus.of_type("SLOT_UNLOCKED") == []
    assert bus.types == ["BOOKING_UPDATED"]


async def test_cancelling_a_meeting_keeps_unrelated_own_locks(
    client, auth, receptionist, video_room, lock_store, bus
):
    b = await create_booking(client, auth(receptionist), video_room)
    composer = lock_store.add(receptionist, video_room.id, time(8), time(19))
    popover = lock_store.add(receptionist, video_room.id, time(14), time(15), booking_id=b["id"])
    bus.clear()
    resp = await client.post(f"/api/bookings/{b['id']}/cancel", json={}, headers=auth(receptionist))
    assert resp.status_code == 200
    assert set(lock_store.locks) == {composer.lock_id}  # só a trava ligada à reserva saiu
    assert [u["lock"]["lock_id"] for u in bus.of_type("SLOT_UNLOCKED")] == [popover.lock_id]


async def test_patch_releases_own_selection_over_the_new_position(
    client, auth, receptionist, receptionist2, video_room, normal_room, lock_store, bus
):
    b = await create_booking(client, auth(receptionist), video_room)
    target = lock_store.add(receptionist, normal_room.id, time(14), time(15))  # seleção no destino
    other_booking = lock_store.add(
        receptionist, normal_room.id, time(14), time(15), booking_id="outra-reserva"
    )
    foreign = lock_store.add(receptionist2, normal_room.id, time(16), time(17))
    bus.clear()
    resp = await client.patch(
        f"/api/bookings/{b['id']}", json={"version": 1, "room_id": normal_room.id},
        headers=auth(receptionist),
    )
    assert resp.status_code == 200
    assert set(lock_store.locks) == {other_booking.lock_id, foreign.lock_id}
    assert [u["lock"]["lock_id"] for u in bus.of_type("SLOT_UNLOCKED")] == [target.lock_id]


async def test_status_change_touches_no_locks(
    client, auth, receptionist, video_room, lock_store, bus
):
    b = await create_booking(client, auth(receptionist), video_room)
    mine = lock_store.add(receptionist, video_room.id, time(14), time(15), booking_id=b["id"])
    bus.clear()
    resp = await client.post(
        f"/api/bookings/{b['id']}/status", json={"status": "CHECKED_IN"}, headers=auth(receptionist)
    )
    assert resp.status_code == 200
    assert mine.lock_id in lock_store.locks and bus.types == ["BOOKING_UPDATED"]


async def test_lock_store_failure_does_not_block_booking(
    client, auth, receptionist, video_room, lock_store, bus
):
    lock_store.fail = True
    resp = await client.post(
        "/api/bookings", json=booking_payload(video_room), headers=auth(receptionist)
    )
    assert resp.status_code == 201
    assert bus.types == ["BOOKING_CREATED"]


# ---------------------------------------------------------------- eventos


async def test_booking_events_payloads(client, auth, receptionist, video_room, normal_room, bus):
    h = auth(receptionist)
    b = await create_booking(client, h, video_room)
    (created,) = bus.of_type("BOOKING_CREATED")
    assert set(created) == {"booking"}
    assert created["booking"] == b  # mesmo JSON da resposta REST
    envelope = bus.envelopes[0]
    assert set(envelope) == {"type", "data", "ts"}

    resp = await client.patch(
        f"/api/bookings/{b['id']}",
        json={
            "version": 1,
            "room_id": normal_room.id,
            "date": "2026-10-27",
            "start": "09:00",
            "end": "10:00",
        },
        headers=h,
    )
    (updated,) = bus.of_type("BOOKING_UPDATED")
    assert updated["booking"] == resp.json()["booking"]
    assert updated["previous"] == {
        "room_id": video_room.id,
        "date": "2026-10-26",
        "start": "14:00",
        "end": "15:00",
    }

    resp = await client.post(
        f"/api/bookings/{b['id']}/status", json={"status": "CHECKED_IN"}, headers=h
    )
    status_event = bus.of_type("BOOKING_UPDATED")[-1]
    assert status_event["booking"]["status"] == "CHECKED_IN"
    assert status_event["previous"] == {
        "room_id": normal_room.id,
        "date": "2026-10-27",
        "start": "09:00",
        "end": "10:00",
    }

    resp = await client.post(f"/api/bookings/{b['id']}/cancel", json={}, headers=h)
    (cancelled,) = bus.of_type("BOOKING_CANCELLED")
    assert cancelled == {"booking": resp.json()}
    assert bus.types == [
        "BOOKING_CREATED",
        "BOOKING_UPDATED",
        "BOOKING_UPDATED",
        "BOOKING_CANCELLED",
    ]


async def test_events_are_published_only_after_commit(client, auth, receptionist, video_room, bus):
    seen: list[bool] = []

    async def check_committed(envelope):
        # outra conexão só enxerga a reserva se a transação já foi confirmada
        booking_id = uuid.UUID(envelope["data"]["booking"]["id"])
        async with SessionLocal() as other:
            seen.append(await other.get(Booking, booking_id) is not None)

    bus.on_publish = check_committed
    await create_booking(client, auth(receptionist), video_room)
    assert seen == [True]


async def test_no_events_on_failures_or_noop(
    client, auth, receptionist, receptionist2, video_room, bus
):
    h = auth(receptionist)
    b = await create_booking(client, h, video_room)
    bus.clear()
    failures = [
        client.post("/api/bookings", json=booking_payload(video_room), headers=auth(receptionist2)),
        client.post("/api/bookings", json=booking_payload(video_room, start="07:00"), headers=h),
        client.patch(f"/api/bookings/{b['id']}", json={"version": 9, "title": "x"}, headers=h),
        client.patch(
            f"/api/bookings/{b['id']}", json={"version": 1, "title": b["title"]}, headers=h
        ),
        client.post(f"/api/bookings/{b['id']}/status", json={"status": "CONFIRMED"}, headers=h),
        client.post(f"/api/bookings/{b['id']}/cancel", json={"version": 3}, headers=h),
    ]
    for request in failures:
        await request
    assert bus.envelopes == []


async def test_publish_failure_does_not_break_request(client, auth, receptionist, video_room, bus):
    async def boom(envelope):
        raise RuntimeError("bus fora do ar")

    bus.on_publish = boom
    resp = await client.post(
        "/api/bookings", json=booking_payload(video_room), headers=auth(receptionist)
    )
    assert resp.status_code == 201
