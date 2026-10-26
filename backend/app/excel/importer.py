"""Importação da planilha legada (RF08, contrato seção 8; formato em docs/IMPORTACAO.md).

Fluxo: lê o cadastro de salas -> interpreta a planilha numa thread (reader.read_workbook) ->
para cada reserva candidata, na ordem das abas: pula repetidas/conflitantes dentro da própria
planilha, pula as que já existem no banco ou que já foram importadas antes (idempotência pela
marca `import_key`, mesmo que a reserva tenha sido movida ou cancelada depois), as que
conflitam com reservas existentes e as que alguém está reservando na grade agora (soft lock),
e cria as demais pelo serviço do núcleo (log IMPORTED). Na simulação (`dry_run`) nada é
gravado, mas as mesmas checagens rodam, então o relatório é o mesmo da importação real.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.excel.common import fold
from app.excel.parsing import RoomRef, room_key
from app.excel.reader import (
    Candidate,
    InvalidWorkbookError,
    SheetPlan,
    format_refs,
    read_workbook,
)
from app.models import Booking, LogAction, PeriodType, RoomType, User
from app.realtime.events import EventType, publish
from app.realtime.registry import LockInfo, LockStore, get_lock_store
from app.schemas import BookingCreate, ImportReport, ImportSheetReport
from app.services import bookings as bookings_service
from app.services import rooms as rooms_service
from app.services.errors import BusinessError
from app.timeutil import fmt_hhmm, resolve_period, today_local

log = logging.getLogger(__name__)

__all__ = ["InvalidWorkbookError", "import_key", "import_workbook", "import_workbook_or_raise"]

IMPORT_KEY_VERSION = "v1"
_KEY_CHUNK = 1000


async def import_workbook(
    session: AsyncSession,
    source: bytes,
    *,
    user: User,
    dry_run: bool,
    include_past: bool,
    today: date | None = None,
    lock_store: LockStore | None = None,
) -> ImportReport:
    """Importa (ou simula) a planilha. Arquivo ilegível vira `errors` no relatório (CLI)."""
    try:
        return await import_workbook_or_raise(
            session,
            source,
            user=user,
            dry_run=dry_run,
            include_past=include_past,
            today=today,
            lock_store=lock_store,
        )
    except InvalidWorkbookError as exc:
        return ImportReport(
            dry_run=dry_run,
            sheets=[],
            total_found=0,
            total_imported=0,
            total_skipped=0,
            errors=[exc.message],
        )


async def import_workbook_or_raise(
    session: AsyncSession,
    source: bytes,
    *,
    user: User,
    dry_run: bool,
    include_past: bool,
    today: date | None = None,
    lock_store: LockStore | None = None,
) -> ImportReport:
    """Igual a `import_workbook`, mas levanta InvalidWorkbookError se o arquivo não abrir (API).

    `lock_store`: onde procurar as travas de seleção das telas abertas (padrão: o do processo,
    registrado pelo servidor; a CLI passa um RedisLockStore próprio — ver app/excel/locks.py).
    """
    today = today or today_local()
    rooms = [
        RoomRef(
            id=room.id,
            name=room.name,
            is_active=room.is_active,
            is_video=room.effective_type == RoomType.VIDEO,
            capacity=room.capacity,
        )
        for room in await rooms_service.list_rooms(session, include_inactive=True)
    ]
    plan = await asyncio.to_thread(
        read_workbook, source, rooms, today=today, include_past=include_past
    )

    run = _ImportRun(
        session, user, dry_run=dry_run, lock_store=lock_store or get_lock_store()
    )
    await run.load_import_keys(c for sheet in plan.sheets for c in sheet.candidates)
    sheets = [await run.sheet(sheet) for sheet in plan.sheets]

    unknown: dict[str, str] = {}
    for sheet in plan.sheets:
        for name in sheet.unknown_rooms:
            unknown.setdefault(fold(name), name)

    report = ImportReport(
        dry_run=dry_run,
        sheets=sheets,
        total_found=sum(s.bookings_found for s in sheets),
        total_imported=sum(s.bookings_imported for s in sheets),
        total_skipped=sum(s.bookings_skipped for s in sheets),
        unknown_rooms=list(unknown.values()),
        errors=plan.errors + run.errors,
    )
    if not dry_run and run.imported_dates:
        await publish(
            EventType.BOOKINGS_IMPORTED,
            {
                "dates": sorted(d.isoformat() for d in run.imported_dates),
                "count": report.total_imported,
            },
        )
    log.info(
        "Importação %s: %d encontradas, %d %s, %d puladas",
        "simulada" if dry_run else "concluída",
        report.total_found,
        report.total_imported,
        "importáveis" if dry_run else "importadas",
        report.total_skipped,
    )
    return report


# ---------------------------------------------------------------- execução


@dataclass(frozen=True)
class _Accepted:
    """Reserva da planilha já aceita nesta execução (para achar conflitos internos)."""

    start: datetime
    end: datetime
    ticket: str | None
    requester: str
    where: str


class _ImportRun:
    def __init__(
        self, session: AsyncSession, user: User, *, dry_run: bool, lock_store: LockStore
    ) -> None:
        self.session = session
        self.user = user
        self.user_id = user.id
        self.dry_run = dry_run
        self.lock_store = lock_store
        self.lock_failure = False  # travas inacessíveis (Redis fora): avisado uma vez
        self.lock_failure_reported = False
        self.accepted: dict[int, list[_Accepted]] = defaultdict(list)
        self.imported_dates: set[date] = set()
        self.known_keys: set[str] = set()
        self.errors: list[str] = []

    async def load_import_keys(self, candidates: Iterable[Candidate]) -> None:
        """Marcas `import_key` que já existem no banco (qualquer status, inclusive canceladas
        ou remanejadas): uma consulta por lote em vez de uma por reserva."""
        keys = sorted({import_key(c) for c in candidates if not c.error})
        for i in range(0, len(keys), _KEY_CHUNK):
            chunk = keys[i : i + _KEY_CHUNK]
            stmt = select(Booking.import_key).where(Booking.import_key.in_(chunk))
            self.known_keys.update(k for k in (await self.session.scalars(stmt)).all() if k)

    async def sheet(self, plan: SheetPlan) -> ImportSheetReport:
        if plan.status != "READY":
            return ImportSheetReport(
                sheet_name=plan.name, date=plan.day, status=plan.status, warnings=plan.warnings
            )
        warnings = list(plan.warnings)
        imported, skipped = 0, plan.ignored
        existing: list[str] = []
        before: list[str] = []
        repeated: list[str] = []
        for candidate in plan.candidates:
            warnings.extend(candidate.warnings)
            outcome, message = await self._candidate(candidate)
            if outcome == "imported":
                imported += 1
                continue
            skipped += 1
            if outcome == "exists":
                existing.append(candidate.ref)
            elif outcome == "imported_before":
                before.append(candidate.ref)
            elif outcome == "repeated":
                repeated.append(candidate.ref)
            else:
                warnings.append(f"{candidate.where}: {message} — pulada.")
        if self.lock_failure and not self.lock_failure_reported:
            self.lock_failure_reported = True
            warnings.append(
                "Não foi possível consultar as travas de seleção das telas abertas (tempo real "
                "indisponível); as reservas foram conferidas só contra o banco de dados."
            )
        if existing:
            warnings.append(
                f"{len(existing)} reserva(s) já cadastrada(s) no sistema (mesma sala, horário e "
                f"chamado) — pulada(s): {format_refs(existing)}."
            )
        if before:
            warnings.append(
                f"{len(before)} reserva(s) já importada(s) anteriormente (pode ter sido movida ou "
                f"cancelada no sistema) — pulada(s): {format_refs(before)}."
            )
        if repeated:
            warnings.append(
                f"{len(repeated)} reserva(s) repetida(s) dentro da própria planilha — "
                f"pulada(s): {format_refs(repeated)}."
            )
        return ImportSheetReport(
            sheet_name=plan.name,
            date=plan.day,
            status="DRY_RUN" if self.dry_run else "IMPORTED",
            bookings_found=len(plan.candidates) + plan.ignored,
            bookings_imported=imported,
            bookings_skipped=skipped,
            warnings=warnings,
        )

    async def _candidate(self, c: Candidate) -> tuple[str, str | None]:
        """("imported" | "exists" | "imported_before" | "repeated" | "skipped", motivo)."""
        if c.error:
            return "skipped", c.error
        try:
            start_dt, end_dt, _ = resolve_period(c.period, c.day, c.start, c.end)
        except ValueError:
            return "skipped", "horário inválido"
        ticket = _clean_ticket(c.ticket)
        key = import_key(c)

        for other in self.accepted[c.room.id]:
            if other.start < end_dt and start_dt < other.end:
                if (other.start, other.end) == (start_dt, end_dt) and _same_booking(
                    other.ticket, other.requester, ticket, c.requester
                ):
                    return "repeated", None
                return "skipped", f"conflito com {other.where}, da própria planilha"

        try:
            conflicts = await bookings_service.find_conflicts(
                self.session, c.room.id, start_dt, end_dt
            )
            if any(_is_same(b, start_dt, end_dt, ticket, c.requester) for b in conflicts):
                return "exists", None
            if key in self.known_keys:
                # Já veio desta mesma célula numa importação anterior e depois foi movida,
                # editada ou cancelada no sistema: reimportar ressuscitaria/duplicaria a reserva.
                return "imported_before", None
            if conflicts:
                return "skipped", _conflict_message(conflicts)
            locks = await self._locks(c.room.id, start_dt, end_dt)
            if locks:
                return "skipped", _lock_message(locks)
            if not self.dry_run:
                await bookings_service.create_booking(
                    self.session,
                    _booking_create(c),
                    self.user,
                    action=LogAction.IMPORTED,
                    check_locks=False,  # conferidas acima, inclusive as de quem importa
                    enforce_hours=False,
                    publish_events=False,
                    import_key=key,
                )
        except BusinessError as exc:
            return "skipped", exc.message.rstrip(".")
        except ValidationError:
            return "skipped", "dados inválidos para uma reserva"
        except SQLAlchemyError:
            log.exception("Falha ao gravar reserva importada (%s)", c.where)
            await self._recover()
            self.errors.append(f"{c.where}: erro ao gravar no banco de dados.")
            return "skipped", "erro ao gravar no banco de dados"

        self.accepted[c.room.id].append(
            _Accepted(start_dt, end_dt, ticket, c.requester, f'{c.ref} (aba "{c.sheet}")')
        )
        self.imported_dates.add(c.day)
        return "imported", None

    async def _locks(self, room_id: int, start_dt: datetime, end_dt: datetime) -> list[LockInfo]:
        """Travas de seleção de QUALQUER pessoa (inclusive de quem importa) sobre o horário."""
        if self.lock_failure:
            return []
        try:
            return await self.lock_store.find_overlapping(room_id, start_dt, end_dt)
        except Exception as exc:
            # Soft lock é só experiência de uso; o banco continua garantindo os conflitos.
            log.warning("Travas de seleção inacessíveis na importação (%s)", type(exc).__name__)
            self.lock_failure = True
            return []

    async def _recover(self) -> None:
        # Uma falha inesperada deixa a transação abortada: desfaz só a reserva atual (as
        # anteriores já foram confirmadas) e recarrega o autor, expirado pelo rollback.
        await self.session.rollback()
        self.user = await self.session.get(User, self.user_id)


def import_key(c: Candidate) -> str:
    """Identidade estável da reserva na planilha de origem: data da aba, sala, horário e texto
    da célula (normalizados). Gravada em bookings.import_key; uma reimportação nunca recria
    uma marca que já existe (qualquer status)."""
    raw = "|".join(
        (
            c.day.isoformat(),
            room_key(c.room.name),
            f"{c.start:%H:%M}",
            f"{c.end:%H:%M}",
            fold(c.source_text),
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
    return f"{IMPORT_KEY_VERSION}:{digest}"


def _lock_message(locks: list[LockInfo]) -> str:
    names = list(dict.fromkeys(lk.user_name for lk in locks))
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]
    verb = "está" if len(names) == 1 else "estão"
    return f"{who} {verb} reservando este horário agora na grade"


def _booking_create(c: Candidate) -> BookingCreate:
    custom = c.period == PeriodType.CUSTOM
    return BookingCreate(
        room_id=c.room.id,
        date=c.day,
        period_type=c.period,
        start=c.start if custom else None,
        end=c.end if custom else None,
        ticket_number=c.ticket,
        title=c.title,
        requester_name=c.requester,
        requires_video=c.requires_video,
        attendees=c.attendees,
        notes=c.notes,
    )


def _clean_ticket(ticket: str | None) -> str | None:
    """Mesma normalização do serviço de reservas ("#48211" == "48211")."""
    if ticket is None:
        return None
    return ticket.strip().lstrip("#").strip() or None


def _same_booking(
    ticket_a: str | None, requester_a: str, ticket_b: str | None, requester_b: str
) -> bool:
    """Mesmo chamado; sem chamado dos dois lados, mesmo solicitante."""
    if ticket_a or ticket_b:
        return ticket_a == ticket_b
    return fold(requester_a) == fold(requester_b)


def _is_same(
    b: Booking, start_dt: datetime, end_dt: datetime, ticket: str | None, requester: str
) -> bool:
    return (
        b.start_time == start_dt
        and b.end_time == end_dt
        and _same_booking(b.ticket_number, b.requester_name, ticket, requester)
    )


def _conflict_message(conflicts: list[Booking]) -> str:
    def describe(b: Booking) -> str:
        ticket = f", chamado {b.ticket_number}" if b.ticket_number else ""
        return f"{b.requester_name} ({fmt_hhmm(b.start_time)}–{fmt_hhmm(b.end_time)}{ticket})"

    if len(conflicts) == 1:
        return f"conflito com a reserva já cadastrada de {describe(conflicts[0])}"
    listed = "; ".join(describe(b) for b in conflicts[:3])
    more = f" e mais {len(conflicts) - 3}" if len(conflicts) > 3 else ""
    return f"conflito com {len(conflicts)} reservas já cadastradas: {listed}{more}"
