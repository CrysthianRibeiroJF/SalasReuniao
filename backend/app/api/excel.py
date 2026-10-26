"""Importação/exportação Excel (contrato 5.6).

GET  /api/export/day.xlsx?date=YYYY-MM-DD          (padrão: hoje)
GET  /api/export/week.xlsx?start_date=YYYY-MM-DD   (qualquer dia -> segunda; padrão: esta semana)
POST /api/import/xlsx?dry_run=true&include_past=false   (admin, multipart "file")
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import Response

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.excel import exporter, importer
from app.excel.common import XLSX_MEDIA_TYPE
from app.schemas import ImportReport
from app.services.errors import BusinessError, unprocessable
from app.timeutil import today_local, week_monday

router = APIRouter(prefix="/api", tags=["excel"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_ZIP_SIGNATURE = b"PK\x03\x04"  # .xlsx é um pacote zip


@router.get("/export/day.xlsx", response_class=Response)
async def export_day(
    session: SessionDep,
    _: CurrentUser,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> Response:
    d = day or today_local()
    content = await exporter.export_day(session, d)
    return _xlsx(content, f"agenda-{d.isoformat()}.xlsx")


@router.get("/export/week.xlsx", response_class=Response)
async def export_week(
    session: SessionDep, _: CurrentUser, start_date: date | None = None
) -> Response:
    monday = week_monday(start_date or today_local())
    content = await exporter.export_week(session, monday)
    return _xlsx(content, f"agenda-semana-{monday.isoformat()}.xlsx")


@router.post("/import/xlsx", response_model=ImportReport)
async def import_xlsx(
    session: SessionDep,
    user: AdminUser,
    file: Annotated[UploadFile, File(description="Planilha .xlsx (até 10 MB)")],
    dry_run: bool = True,
    include_past: bool = False,
) -> ImportReport:
    """Simula (padrão) ou importa a planilha legada. Só administradores."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise unprocessable(
            "INVALID_FILE_TYPE",
            "Envie um arquivo .xlsx (Excel). Arquivos .xls antigos precisam ser salvos como "
            ".xlsx antes.",
        )
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise _too_large()
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise _too_large()
    if not content.startswith(_ZIP_SIGNATURE):
        raise _invalid_file("O arquivo enviado não é uma planilha .xlsx válida.")
    try:
        return await importer.import_workbook_or_raise(
            session, content, user=user, dry_run=dry_run, include_past=include_past
        )
    except importer.InvalidWorkbookError as exc:
        raise _invalid_file(exc.message) from None


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _too_large() -> BusinessError:
    return unprocessable(
        "FILE_TOO_LARGE",
        f"O arquivo passa do limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Remova abas "
        "antigas da planilha e tente de novo.",
    )


def _invalid_file(message: str) -> BusinessError:
    return unprocessable("INVALID_FILE", message)
