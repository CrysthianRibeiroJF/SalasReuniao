"""Rotas Excel (contrato 5.6): downloads .xlsx e upload da planilha legada."""

from __future__ import annotations

from datetime import time, timedelta

from sqlalchemy import func, select

from app.excel.common import XLSX_MEDIA_TYPE
from app.models import Booking, BookingLog, LogAction
from app.realtime import registry
from app.timeutil import today_local, week_monday
from tests.core_support import create_booking, error_code
from tests.excel_builders import legacy, new_workbook, reload, to_bytes

pytest_plugins = ("tests.core_fixtures", "tests.excel_fixtures")

S01, S05 = "Sala 01 - Executiva", "Sala 05 - Reunião"


def upload(content: bytes, filename: str = "planilha.xlsx") -> dict:
    return {"file": (filename, content, XLSX_MEDIA_TYPE)}


def future_workbook(days_ahead: int = 3, *, past: bool = False) -> bytes:
    """Planilha com datas relativas a hoje (a API usa a data real)."""
    wb = new_workbook()
    d = today_local() + timedelta(days=days_ahead)
    sheet = legacy(wb, f"{d:%d-%m-%Y}")
    sheet.put(S01, "09:00", "10:30", "Chamado 48211 - Carla Mendes - Diretoria")
    sheet.put(S05, "14:00", None, "Pedro - RH")
    if past:
        old = today_local() - timedelta(days=2)
        legacy(wb, f"{old:%d-%m-%Y}").put(S01, "09:00", None, "Antiga")
    return to_bytes(wb)


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- exportação


async def test_export_requires_login(client):
    assert (await client.get("/api/export/day.xlsx")).status_code == 401
    assert (await client.get("/api/export/week.xlsx")).status_code == 401


async def test_export_day(client, auth, receptionist, rooms):
    await create_booking(client, auth(receptionist), rooms[S01], requester_name="Carla Mendes")
    resp = await client.get(
        "/api/export/day.xlsx", params={"date": "2026-10-26"}, headers=auth(receptionist)
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == XLSX_MEDIA_TYPE
    assert resp.headers["content-disposition"] == 'attachment; filename="agenda-2026-10-26.xlsx"'
    assert resp.headers["cache-control"] == "no-store"
    wb = reload(resp.content)
    assert wb.sheetnames == ["Seg 26-10"]
    assert wb["Seg 26-10"]["B16"].value.startswith("Chamado 48211\nCarla Mendes")


async def test_export_day_defaults_to_today(client, auth, receptionist):
    resp = await client.get("/api/export/day.xlsx", headers=auth(receptionist))
    assert resp.status_code == 200
    today = today_local()
    assert resp.headers["content-disposition"] == f'attachment; filename="agenda-{today}.xlsx"'


async def test_export_week_normalizes_to_monday(client, auth, receptionist, rooms):
    resp = await client.get(
        "/api/export/week.xlsx", params={"start_date": "2026-10-28"}, headers=auth(receptionist)
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == XLSX_MEDIA_TYPE
    assert (
        resp.headers["content-disposition"]
        == 'attachment; filename="agenda-semana-2026-10-26.xlsx"'
    )
    assert reload(resp.content).sheetnames == [
        "Seg 26-10",
        "Ter 27-10",
        "Qua 28-10",
        "Qui 29-10",
        "Sex 30-10",
    ]

    resp = await client.get("/api/export/week.xlsx", headers=auth(receptionist))
    monday = week_monday(today_local())
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="agenda-semana-{monday}.xlsx"'
    )


async def test_export_rejects_invalid_dates(client, auth, receptionist):
    for url, params in (
        ("/api/export/day.xlsx", {"date": "26/10/2026"}),
        ("/api/export/week.xlsx", {"start_date": "ontem"}),
    ):
        resp = await client.get(url, params=params, headers=auth(receptionist))
        assert resp.status_code == 422


# ---------------------------------------------------------------- importação


async def test_import_requires_admin(client, auth, receptionist, rooms):
    content = future_workbook()
    assert (await client.post("/api/import/xlsx", files=upload(content))).status_code == 401
    resp = await client.post("/api/import/xlsx", files=upload(content), headers=auth(receptionist))
    assert resp.status_code == 403
    assert error_code(resp) == "FORBIDDEN"


async def test_import_rejects_bad_uploads(client, auth, admin, rooms, session):
    headers = auth(admin)
    cases = [
        (upload(future_workbook(), "planilha.xls"), "INVALID_FILE_TYPE", ".xlsx"),
        (upload(future_workbook(), "planilha.csv"), "INVALID_FILE_TYPE", ".xlsx"),
        (upload(b"PK\x03\x04" + b"\0" * (10 * 1024 * 1024)), "FILE_TOO_LARGE", "10 MB"),
        (upload(b"nome;sala\nCarla;01"), "INVALID_FILE", "não é uma planilha .xlsx válida"),
        (upload(b"PK\x03\x04corrompido"), "INVALID_FILE", "não é uma planilha .xlsx válida"),
        (upload(b""), "INVALID_FILE", "não é uma planilha .xlsx válida"),
    ]
    for files, code, message in cases:
        resp = await client.post("/api/import/xlsx", files=files, headers=headers)
        assert resp.status_code == 422, (code, resp.text)
        assert error_code(resp) == code
        assert message in resp.json()["detail"]["message"]

    resp = await client.post("/api/import/xlsx", headers=headers)  # sem o campo "file"
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)
    assert await count(session, Booking) == 0


async def test_import_dry_run_by_default_then_commit(client, auth, admin, rooms, bus, session):
    headers = auth(admin)
    content = future_workbook()

    resp = await client.post("/api/import/xlsx", files=upload(content), headers=headers)
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["dry_run"] is True
    assert (report["total_found"], report["total_imported"], report["total_skipped"]) == (2, 2, 0)
    assert report["sheets"][0]["status"] == "DRY_RUN"
    assert await count(session, Booking) == 0
    assert bus.of_type("BOOKINGS_IMPORTED") == []

    resp = await client.post(
        "/api/import/xlsx", params={"dry_run": "false"}, files=upload(content), headers=headers
    )
    report = resp.json()
    assert report["dry_run"] is False and report["total_imported"] == 2
    assert report["sheets"][0]["status"] == "IMPORTED"
    d = (today_local() + timedelta(days=3)).isoformat()
    assert bus.of_type("BOOKINGS_IMPORTED") == [{"dates": [d], "count": 2}]

    listed = (await client.get("/api/bookings", params={"date": d}, headers=headers)).json()
    assert [(b["start"], b["end"], b["ticket_number"], b["requester_name"]) for b in listed] == [
        ("09:00", "10:30", "48211", "Carla Mendes"),
        ("14:00", "14:30", None, "Pedro"),
    ]
    logs = (await client.get(f"/api/bookings/{listed[0]['id']}/logs", headers=headers)).json()
    assert [log["action"] for log in logs] == ["IMPORTED"]
    assert logs[0]["user"]["id"] == admin.id
    assert (
        await session.scalar(
            select(func.count())
            .select_from(BookingLog)
            .where(BookingLog.action == LogAction.IMPORTED)
        )
        == 2
    )

    # de novo: idempotente, nada novo e nenhum evento a mais
    resp = await client.post(
        "/api/import/xlsx", params={"dry_run": "false"}, files=upload(content), headers=headers
    )
    assert (resp.json()["total_imported"], resp.json()["total_skipped"]) == (0, 2)
    assert len(bus.of_type("BOOKINGS_IMPORTED")) == 1


async def test_import_include_past_and_conflict_with_grid(client, auth, admin, rooms):
    headers = auth(admin)
    d = today_local() + timedelta(days=3)
    await create_booking(
        client,
        headers,
        rooms[S05],
        date=d.isoformat(),
        start="14:00",
        end="15:00",
        requester_name="Já na grade",
    )
    content = future_workbook(past=True)

    report = (await client.post("/api/import/xlsx", files=upload(content), headers=headers)).json()
    statuses = [s["status"] for s in report["sheets"]]
    assert statuses == ["DRY_RUN", "SKIPPED_PAST"]
    assert (report["total_found"], report["total_imported"], report["total_skipped"]) == (2, 1, 1)
    assert any("Já na grade" in w for w in report["sheets"][0]["warnings"])

    report = (
        await client.post(
            "/api/import/xlsx",
            params={"include_past": "true"},
            files=upload(content),
            headers=headers,
        )
    ).json()
    assert [s["status"] for s in report["sheets"]] == ["DRY_RUN", "DRY_RUN"]
    assert report["total_found"] == 3


async def test_exported_file_can_be_uploaded_back(client, auth, admin, rooms, session):
    headers = auth(admin)
    d = today_local() + timedelta(days=7)
    await create_booking(client, headers, rooms[S01], date=d.isoformat())
    exported = await client.get(
        "/api/export/day.xlsx", params={"date": d.isoformat()}, headers=headers
    )
    filename = exported.headers["content-disposition"].split('filename="')[1].rstrip('"')
    resp = await client.post(
        "/api/import/xlsx", files=upload(exported.content, filename), headers=headers
    )
    assert resp.status_code == 200
    assert (resp.json()["total_found"], resp.json()["total_skipped"]) == (1, 1)  # já existe
    assert await count(session, Booking) == 1


async def test_import_skips_slots_being_selected_in_the_grid(
    client, auth, admin, receptionist, rooms, session
):
    """A importação pela tela usa as travas do próprio servidor (soft locks das telas abertas)."""
    d = today_local() + timedelta(days=3)
    store = registry.get_lock_store()
    assert not isinstance(store, registry.NullLockStore)
    lock, _ = await store.acquire(
        room_id=rooms[S01].id,
        date=d,
        start=time(9, 0),
        end=time(9, 30),
        user_id=receptionist.id,
        user_name=receptionist.full_name,
    )
    assert lock is not None
    resp = await client.post(
        "/api/import/xlsx",
        params={"dry_run": "false"},
        files=upload(future_workbook()),
        headers=auth(admin),
    )
    report = resp.json()
    assert (report["total_imported"], report["total_skipped"]) == (1, 1)
    assert any(
        w.endswith("Ana Recepção está reservando este horário agora na grade — pulada.")
        for w in report["sheets"][0]["warnings"]
    )
    assert await count(session, Booking) == 1
