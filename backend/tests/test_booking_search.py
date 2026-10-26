"""Busca instantânea por chamado ou solicitante/assunto (5.4, GET /api/bookings/search)."""

from __future__ import annotations

from datetime import timedelta

from app.services.bookings import normalize_text
from app.timeutil import today_local
from tests.core_support import create_booking


async def _search(client, headers, q: str, **params) -> list[dict]:
    resp = await client.get("/api/bookings/search", params={"q": q, **params}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_normalize_text():
    assert normalize_text("JOÃO Conceição Ângela Müller ñ") == "joao conceicao angela muller n"


async def test_search_by_ticket_with_or_without_hash(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room, ticket_number="48211")
    inc = await create_booking(
        client, h, normal_room, ticket_number="INC0012345", start="16:00", end="17:00"
    )
    for q in ("48211", "#48211", " # 482", "48"):
        assert [x["id"] for x in await _search(client, h, q)] == [b["id"]], q
    assert [x["id"] for x in await _search(client, h, "inc0012")] == [inc["id"]]
    assert await _search(client, h, "211") == []  # chamado casa por prefixo, não "contém"


async def test_search_by_requester_and_title_ignoring_accents_and_case(
    client, auth, receptionist, normal_room
):
    h = auth(receptionist)
    joao = await create_booking(
        client, h, normal_room, requester_name="João Conceição", ticket_number=None
    )
    angela = await create_booking(
        client,
        h,
        normal_room,
        requester_name="ÂNGELA SOUZA",
        title="Diretoria Jurídica",
        start="16:00",
        end="17:00",
        ticket_number=None,
    )
    assert [x["id"] for x in await _search(client, h, "joao")] == [joao["id"]]
    assert [x["id"] for x in await _search(client, h, "CONCEICAO")] == [joao["id"]]
    assert [x["id"] for x in await _search(client, h, "conceição")] == [joao["id"]]
    assert [x["id"] for x in await _search(client, h, "angela")] == [angela["id"]]
    assert [x["id"] for x in await _search(client, h, "juridica")] == [angela["id"]]
    assert [x["id"] for x in await _search(client, h, "jurídica")] == [angela["id"]]


async def test_short_queries_and_wildcards(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    await create_booking(client, h, normal_room, requester_name="Ana 100% Silva")
    assert await _search(client, h, "") == []
    assert await _search(client, h, "a") == []
    assert await _search(client, h, "  a  ") == []
    assert await _search(client, h, "__") == []  # curingas do LIKE são escapados
    assert len(await _search(client, h, "0%")) == 1


async def test_cancelled_bookings_are_not_returned(client, auth, receptionist, normal_room):
    h = auth(receptionist)
    b = await create_booking(client, h, normal_room, ticket_number="5555")
    await client.post(f"/api/bookings/{b['id']}/cancel", json={}, headers=h)
    assert await _search(client, h, "5555") == []


async def test_ordering_upcoming_first_then_most_recent_past(
    client, auth, receptionist, normal_room
):
    h = auth(receptionist)
    today = today_local()

    async def make(days: int, start: str = "10:00", end: str = "11:00") -> str:
        b = await create_booking(
            client,
            h,
            normal_room,
            date=(today + timedelta(days=days)).isoformat(),
            start=start,
            end=end,
            requester_name="Marina Prado",
        )
        return b["id"]

    past_far = await make(-20)
    future_far = await make(15)
    today_late = await make(0, "17:00", "18:00")
    past_recent = await make(-1)
    today_early = await make(0, "08:00", "08:30")
    future_near = await make(1)

    result = [x["id"] for x in await _search(client, h, "marina")]
    assert result == [today_early, today_late, future_near, future_far, past_recent, past_far]
    assert [x["id"] for x in await _search(client, h, "marina", limit=2)] == [
        today_early,
        today_late,
    ]


async def test_search_params_validation(client, auth, receptionist):
    resp = await client.get(
        "/api/bookings/search", params={"q": "ab", "limit": 0}, headers=auth(receptionist)
    )
    assert resp.status_code == 422
    resp = await client.get("/api/bookings/search", params={"q": "ab"})
    assert resp.status_code == 401
