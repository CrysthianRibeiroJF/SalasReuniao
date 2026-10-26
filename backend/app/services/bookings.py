"""Reservas: regras de escrita e consultas (contrato, seção 5.4).

A constraint `bookings_no_overlap` é a verdade (hard lock). A pré-checagem só existe para responder
de forma amigável (reservas conflitantes + salas alternativas); se ela passar e o INSERT/UPDATE
perder a corrida, a violação 23P01 vira o mesmo 409 BOOKING_CONFLICT.

Tempo real somente via app.realtime.registry.get_lock_store() e app.realtime.events.publish(),
sempre DEPOIS do commit.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import (
    Booking,
    BookingLog,
    BookingStatus,
    LogAction,
    PeriodType,
    Room,
    RoomType,
    User,
)
from app.realtime.events import EventType, publish
from app.realtime.registry import LockInfo, get_lock_store
from app.schemas import (
    BookingCancel,
    BookingCreate,
    BookingOut,
    BookingStatusIn,
    BookingUpdate,
    BookingWriteResult,
    BusinessWarning,
)
from app.serializers import booking_out, booking_snapshot, room_brief
from app.services.availability import alternative_rooms, free_rooms
from app.services.errors import BusinessError, conflict, not_found, unprocessable
from app.timeutil import (
    calendar_day_bounds,
    fmt_hhmm,
    is_minute_aligned,
    resolve_period,
    to_local,
    today_local,
    within_operating_hours,
)

log = logging.getLogger(__name__)

OVERLAP_CONSTRAINT = "bookings_no_overlap"
EXCLUSION_VIOLATION = "23P01"
DEADLOCK_DETECTED = "40P01"
WRITE_ATTEMPTS = 3

_RELATIONS = (
    selectinload(Booking.room),
    selectinload(Booking.creator),
    selectinload(Booking.updater),
)

# Normalização sem acentos idêntica no SQL e no Python (unaccent não é garantida no servidor).
# Inclui maiúsculas acentuadas: o lower() do Postgres pode ignorar não-ASCII (depende do LC_CTYPE).
_ACCENTED = "áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ"
_PLAIN = "aaaaaeeeeiiiiooooouuuucnaaaaaeeeeiiiiooooouuuucn"
_UNACCENT = str.maketrans(_ACCENTED, _PLAIN)


# ---------------------------------------------------------------- consultas


async def get_booking(session: AsyncSession, booking_id: uuid.UUID | str) -> BookingOut:
    return booking_out(await _load(session, booking_id))


async def list_bookings(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    include_cancelled: bool = False,
) -> list[BookingOut]:
    """Reservas que COMEÇAM nos dias locais [start_date, end_date], por início e sala."""
    start_dt, _ = calendar_day_bounds(start_date)
    _, end_dt = calendar_day_bounds(end_date)
    stmt = (
        select(Booking)
        .where(Booking.start_time >= start_dt, Booking.start_time < end_dt)
        .options(*_RELATIONS)
        .order_by(Booking.start_time, Booking.room_id)
    )
    if not include_cancelled:
        stmt = stmt.where(Booking.status != BookingStatus.CANCELLED)
    return [booking_out(b) for b in (await session.scalars(stmt)).all()]


async def search_bookings(session: AsyncSession, q: str, limit: int = 20) -> list[BookingOut]:
    """Busca por chamado (prefixo, ignorando '#' e espaços) ou solicitante/assunto (contém,
    sem maiúsculas nem acentos). Hoje e futuras primeiro (mais próximas), depois as passadas."""
    q = (q or "").replace("\x00", "").strip()  # NUL: o PostgreSQL recusa (seria um 500)
    if len(q) < 2:
        return []
    limit = max(1, min(limit, 100))

    clauses = []
    ticket_term = re.sub(r"[#\s]", "", q).lower()
    if ticket_term:
        ticket_col = func.lower(func.regexp_replace(Booking.ticket_number, r"[#\s]", "", "g"))
        clauses.append(ticket_col.like(_like_escape(ticket_term) + "%", escape="\\"))
    text_term = "%" + _like_escape(normalize_text(q)) + "%"
    for col in (Booking.requester_name, Booking.title):
        clauses.append(_unaccent_sql(col).like(text_term, escape="\\"))

    today_start, _ = calendar_day_bounds(today_local())
    upcoming = Booking.start_time >= today_start
    stmt = (
        select(Booking)
        .where(Booking.status != BookingStatus.CANCELLED, or_(*clauses))
        .options(*_RELATIONS)
        .order_by(
            case((upcoming, 0), else_=1),
            case((upcoming, Booking.start_time), else_=None).asc().nulls_last(),
            Booking.start_time.desc(),
            Booking.room_id,
        )
        .limit(limit)
    )
    return [booking_out(b) for b in (await session.scalars(stmt)).all()]


async def find_conflicts(
    session: AsyncSession,
    room_id: int,
    start_dt: datetime,
    end_dt: datetime,
    *,
    exclude_booking_id: uuid.UUID | str | None = None,
) -> list[Booking]:
    """Reservas ativas da sala que se sobrepõem a [start_dt, end_dt) (com relações carregadas)."""
    stmt = (
        select(Booking)
        .where(
            Booking.room_id == room_id,
            Booking.status != BookingStatus.CANCELLED,
            Booking.start_time < end_dt,
            Booking.end_time > start_dt,
        )
        .options(*_RELATIONS)
        .order_by(Booking.start_time)
        .execution_options(populate_existing=True)
    )
    if exclude_booking_id is not None:
        stmt = stmt.where(Booking.id != _as_uuid(exclude_booking_id))
    return list((await session.scalars(stmt)).all())


def normalize_text(value: str) -> str:
    """Minúsculas e sem acentos (mesma regra aplicada no SQL da busca)."""
    return value.lower().translate(_UNACCENT)


# ---------------------------------------------------------------- escrita


async def create_booking(
    session: AsyncSession,
    data: BookingCreate,
    user: User,
    *,
    action: LogAction = LogAction.CREATED,
    check_locks: bool = True,
    enforce_hours: bool = True,
    publish_events: bool = True,
    import_key: str | None = None,
) -> BookingWriteResult:
    """Cria a reserva (regras 1–8 da seção 5.4).

    `import_key` (só importação) identifica a célula de origem na planilha legada.

    `enforce_hours=False` relaxa o horário de funcionamento (importação). Com
    `publish_events=False` nenhuma trava é liberada e nenhum evento é publicado (quem chama
    publica um evento agregado, ex.: BOOKINGS_IMPORTED).
    """
    user_id = user.id
    start_dt, end_dt, is_full_day = _resolve_times(
        data.period_type, data.date, data.start, data.end
    )
    room = await _bookable_room(session, data.room_id)
    if enforce_hours:
        _ensure_operating_hours(start_dt, end_dt)
    if check_locks:
        await _ensure_not_locked(room.id, start_dt, end_dt, user_id=user_id)
    slot = _Slot(room.id, start_dt, end_dt, data.requires_video)
    await _ensure_no_conflict(session, slot)

    booking = Booking(
        id=uuid.uuid4(),
        room_id=room.id,
        room=room,
        ticket_number=_clean_ticket(data.ticket_number),
        title=data.title,
        requester_name=data.requester_name,
        notes=data.notes,
        attendees=data.attendees,
        start_time=start_dt,
        end_time=end_dt,
        period_type=data.period_type,
        is_full_day=is_full_day,
        requires_video=data.requires_video,
        status=BookingStatus.CONFIRMED,
        version=1,
        created_by=user_id,
        import_key=import_key,
    )
    await _write(session, slot, lambda: session.add(booking))
    await _log_and_commit(session, booking, user_id, action, previous=None)

    out = booking_out(await _load(session, booking.id))
    if publish_events:
        await _release_own_locks(user_id, lock_id=data.lock_id, slot=(room.id, start_dt, end_dt))
        await publish(EventType.BOOKING_CREATED, {"booking": _json(out)})
    return BookingWriteResult(booking=out, warnings=await _warnings(session, out))


async def update_booking(
    session: AsyncSession,
    booking_id: uuid.UUID | str,
    data: BookingUpdate,
    user: User,
) -> BookingWriteResult:
    """Edição/remanejamento. Só os campos enviados (model_fields_set) mudam; null limpa campos
    opcionais. Campos obrigatórios enviados com null são ignorados. Enviar start/end sem
    period_type numa reserva de período fixo a transforma em Horário Específico. Os horários de um
    período fixo só são recalculados (com a configuração atual) quando period_type ou date vêm na
    edição: mudar só dados ou só a sala mantém o horário gravado."""
    user_id = user.id
    booking = await _load(session, booking_id, for_update=True)
    _ensure_editable(booking)
    _ensure_version(booking, data.version)
    fields = data.model_fields_set - {"version", "lock_id"}

    previous_state = booking_snapshot(booking)
    previous_slot = _slot_ref(booking)
    old_room_id, old_start, old_end = booking.room_id, booking.start_time, booking.end_time

    room = booking.room
    if "room_id" in fields and data.room_id is not None and data.room_id != booking.room_id:
        room = await _bookable_room(session, data.room_id)
    period, start_dt, end_dt, is_full_day = _target_times(booking, data, fields)
    room_changed = room.id != old_room_id
    time_changed = start_dt != old_start or end_dt != old_end

    changes: dict[str, object] = {}
    for attr in ("title", "notes", "attendees"):
        if attr in fields:
            changes[attr] = getattr(data, attr)
    if "ticket_number" in fields:
        changes["ticket_number"] = _clean_ticket(data.ticket_number)
    if "requester_name" in fields and data.requester_name is not None:
        requester = data.requester_name.strip()
        if not requester:
            raise unprocessable("INVALID_REQUESTER", "Informe o nome do solicitante.")
        changes["requester_name"] = requester
    if "requires_video" in fields and data.requires_video is not None:
        changes["requires_video"] = data.requires_video
    changes = {k: v for k, v in changes.items() if getattr(booking, k) != v}
    period_changed = period != booking.period_type

    if not (room_changed or time_changed or period_changed or changes):
        # Nada mudou: sem log e sem nova versão (regra 6).
        out = booking_out(booking)
        await _release_own_locks(
            user_id,
            lock_id=data.lock_id,
            booking_id=str(booking.id),
            slot=(old_room_id, old_start, old_end),
        )
        return BookingWriteResult(booking=out, warnings=await _warnings(session, out))

    requires_video = bool(changes.get("requires_video", booking.requires_video))
    slot = _Slot(room.id, start_dt, end_dt, requires_video, exclude_booking_id=booking.id)
    if room_changed or time_changed:
        if not room.is_active:
            raise _room_inactive()
        if time_changed:
            _ensure_operating_hours(start_dt, end_dt)
        await _ensure_not_locked(
            room.id, start_dt, end_dt, user_id=user_id, booking_id=str(booking.id)
        )
        await _ensure_no_conflict(session, slot)

    booking_uuid, new_version = booking.id, booking.version + 1

    def apply() -> None:
        # Só atribui valores já calculados: pode rodar de novo após um deadlock (ver _write).
        if room_changed:
            booking.room = room
            booking.room_id = room.id
        booking.start_time, booking.end_time = start_dt, end_dt
        booking.period_type, booking.is_full_day = period, is_full_day
        for attr, value in changes.items():
            setattr(booking, attr, value)
        booking.version = new_version
        booking.updated_by = user_id

    async def reload() -> None:
        await _load(session, booking_uuid, for_update=True)

    if room_changed:
        action = LogAction.MOVED_ROOM
    elif time_changed:
        action = LogAction.RESCHEDULED
    else:
        action = LogAction.UPDATED
    await _write(session, slot, apply, on_retry=reload)
    await _log_and_commit(session, booking, user_id, action, previous=previous_state)

    out = booking_out(await _load(session, booking.id))
    # Só a posição NOVA: travas do usuário sobre a posição antiga podem ser de outra seleção
    # (ex.: o Dia Inteiro que ele está montando enquanto remaneja esta reunião, fluxo 5.2).
    await _release_own_locks(
        user_id,
        lock_id=data.lock_id,
        booking_id=str(booking.id),
        slot=(room.id, start_dt, end_dt),
    )
    await publish(EventType.BOOKING_UPDATED, {"booking": _json(out), "previous": previous_slot})
    return BookingWriteResult(booking=out, warnings=await _warnings(session, out))


async def cancel_booking(
    session: AsyncSession,
    booking_id: uuid.UUID | str,
    data: BookingCancel,
    user: User,
) -> BookingOut:
    user_id = user.id
    booking = await _load(session, booking_id, for_update=True)
    if booking.status == BookingStatus.CANCELLED:
        raise conflict("BOOKING_NOT_EDITABLE", "Esta reserva já foi cancelada.")
    _ensure_version(booking, data.version)

    previous_state = booking_snapshot(booking)
    reason = (data.reason or "").strip() or None
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = datetime.now(timezone.utc)
    booking.cancelled_by = user_id
    booking.cancel_reason = reason
    booking.version += 1
    booking.updated_by = user_id
    await session.flush()
    await _log_and_commit(session, booking, user_id, LogAction.CANCELLED, previous=previous_state)

    out = booking_out(await _load(session, booking.id))
    # Cancelar não ocupa horário: só as travas ligadas a esta reserva saem.
    await _release_own_locks(user_id, booking_id=str(booking.id))
    await publish(EventType.BOOKING_CANCELLED, {"booking": _json(out)})
    return out


async def set_booking_status(
    session: AsyncSession,
    booking_id: uuid.UUID | str,
    data: BookingStatusIn,
    user: User,
) -> BookingOut:
    """Check-in / não compareceu / reconfirmar. Mesmo status = nada muda."""
    user_id = user.id
    booking = await _load(session, booking_id, for_update=True)
    _ensure_editable(booking)
    _ensure_version(booking, data.version)
    new_status = BookingStatus(data.status)
    if new_status == booking.status:
        return booking_out(booking)

    previous_state = booking_snapshot(booking)
    previous_slot = _slot_ref(booking)
    booking.status = new_status
    booking.version += 1
    booking.updated_by = user_id
    await session.flush()
    await _log_and_commit(
        session, booking, user_id, LogAction.STATUS_CHANGED, previous=previous_state
    )

    out = booking_out(await _load(session, booking.id))
    await publish(EventType.BOOKING_UPDATED, {"booking": _json(out), "previous": previous_slot})
    return out


# ---------------------------------------------------------------- internos


@dataclass(frozen=True)
class _Slot:
    room_id: int
    start_dt: datetime
    end_dt: datetime
    requires_video: bool
    exclude_booking_id: uuid.UUID | None = None


async def _load(
    session: AsyncSession, booking_id: uuid.UUID | str, *, for_update: bool = False
) -> Booking:
    try:
        bid = _as_uuid(booking_id)
    except ValueError:
        raise _booking_not_found() from None
    stmt = (
        select(Booking)
        .where(Booking.id == bid)
        .options(*_RELATIONS)
        .execution_options(populate_existing=True)
    )
    if for_update:
        # Serializa escritas concorrentes na mesma reserva: a segunda enxerga a versão nova.
        stmt = stmt.with_for_update(of=Booking)
    booking = await session.scalar(stmt)
    if booking is None:
        raise _booking_not_found()
    return booking


async def _bookable_room(session: AsyncSession, room_id: int) -> Room:
    # FOR SHARE até o commit: uma desativação simultânea da sala (FOR UPDATE em rooms.update_room)
    # espera esta gravação e a enxerga na contagem de reservas futuras, ou esta espera a
    # desativação e recebe ROOM_INACTIVE. Gravações na mesma sala não se bloqueiam por isso.
    room = await session.get(
        Room, room_id, with_for_update={"read": True}, populate_existing=True
    )
    if room is None:
        raise not_found("ROOM_NOT_FOUND", "Sala não encontrada.")
    if not room.is_active:
        raise _room_inactive()
    return room


def _resolve_times(
    period: PeriodType, d: date, start: time | None, end: time | None
) -> tuple[datetime, datetime, bool]:
    """Regra 1 (presets ignoram start/end) + validação de alinhamento e intervalo."""
    if period == PeriodType.CUSTOM:
        if start is None or end is None:
            raise unprocessable("INVALID_TIME", "Informe início e fim para Horário Específico.")
        if not (is_minute_aligned(start) and is_minute_aligned(end)):
            raise unprocessable(
                "INVALID_TIME", "Use horários em múltiplos de 5 minutos (ex.: 14:00, 14:05)."
            )
    try:
        return resolve_period(period, d, start, end)
    except ValueError:
        raise unprocessable(
            "INVALID_INTERVAL", "O horário de término deve ser depois do início."
        ) from None


def _target_times(
    booking: Booking, data: BookingUpdate, fields: set[str]
) -> tuple[PeriodType, datetime, datetime, bool]:
    """Período e horários depois da edição.

    Nada de período/data/horário enviado (só dados ou só a sala): mantém os horários gravados,
    mesmo que a configuração de Manhã/Tarde/Dia Inteiro tenha mudado depois da criação. Com
    `period_type` ou `date` enviados, um período fixo é recalculado pela configuração atual (uma
    reserva de Dia Inteiro movida para outro dia ocupa o dia inteiro de lá).
    """
    current_start, current_end = to_local(booking.start_time), to_local(booking.end_time)

    def sent(name: str, default=None):
        value = getattr(data, name) if name in fields else None
        return default if value is None else value

    times_sent = sent("start") is not None or sent("end") is not None
    if not times_sent and sent("period_type") is None and sent("date") is None:
        return booking.period_type, booking.start_time, booking.end_time, booking.is_full_day
    period = sent("period_type", PeriodType.CUSTOM if times_sent else booking.period_type)
    d = sent("date", current_start.date())
    start = sent("start", current_start.time())
    end = sent("end", current_end.time())
    return (period, *_resolve_times(period, d, start, end))


def _ensure_operating_hours(start_dt: datetime, end_dt: datetime) -> None:
    if not within_operating_hours(start_dt, end_dt):
        raise unprocessable(
            "OUTSIDE_OPERATING_HOURS",
            f"Fora do horário de funcionamento "
            f"({fmt_hhmm(settings.day_start)}–{fmt_hhmm(settings.day_end)}).",
        )


def _ensure_editable(booking: Booking) -> None:
    if booking.status == BookingStatus.CANCELLED:
        raise conflict("BOOKING_NOT_EDITABLE", "Reservas canceladas não podem ser alteradas.")


def _ensure_version(booking: Booking, version: int | None) -> None:
    if version is not None and version != booking.version:
        raise conflict(
            "STALE_VERSION",
            "Esta reserva foi alterada por outra pessoa. Confira os dados atualizados.",
            current=_json(booking_out(booking)),
        )


async def _ensure_not_locked(
    room_id: int,
    start_dt: datetime,
    end_dt: datetime,
    *,
    user_id: int,
    booking_id: str | None = None,
) -> None:
    """Regra 4: travas de OUTROS usuários bloqueiam; as próprias e as da reserva em edição não."""
    try:
        locks = await get_lock_store().find_overlapping(room_id, start_dt, end_dt)
    except Exception:
        # Soft lock é só experiência de uso; o hard lock no banco continua garantindo consistência.
        log.exception("Falha ao consultar soft locks; seguindo sem verificação de travas")
        return
    blocking = [
        lk
        for lk in locks
        if lk.user_id != user_id and not (booking_id is not None and lk.booking_id == booking_id)
    ]
    if not blocking:
        return
    names = list(dict.fromkeys(lk.user_name for lk in blocking))
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]
    verb = "está" if len(names) == 1 else "estão"
    raise conflict(
        "SLOT_LOCKED",
        f"{who} {verb} reservando este horário agora.",
        locks=[_json(lk.to_out()) for lk in blocking],
    )


async def _ensure_no_conflict(session: AsyncSession, slot: _Slot) -> None:
    conflicts = await find_conflicts(
        session,
        slot.room_id,
        slot.start_dt,
        slot.end_dt,
        exclude_booking_id=slot.exclude_booking_id,
    )
    if conflicts:
        raise await _conflict_error(session, slot, conflicts)


async def _write(
    session: AsyncSession,
    slot: _Slot,
    apply: Callable[[], None],
    *,
    on_retry: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Aplica as mudanças e faz o INSERT/UPDATE dentro de um SAVEPOINT: perder a corrida para a
    constraint desfaz só esta escrita (a sessão e os objetos já carregados seguem utilizáveis).

    As mudanças são aplicadas DENTRO do savepoint porque begin_nested() faz autoflush antes do
    SAVEPOINT: alterações feitas antes seriam gravadas fora dele.

    Duas escritas conflitantes simultâneas podem se bloquear mutuamente na exclusion constraint
    (cada uma espera a linha ainda não confirmada da outra); o Postgres aborta uma delas com
    deadlock (40P01). Repetir resolve: a outra já seguiu, e a nova tentativa grava ou recebe 23P01.
    `on_retry` restaura o estado ORM expirado pelo rollback do savepoint antes de repetir.
    """
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            async with session.begin_nested():
                apply()
                await session.flush()
            return
        except IntegrityError as exc:
            if not _is_overlap_violation(exc):
                raise
            raise await _conflict_error(session, slot) from exc
        except DBAPIError as exc:
            if _sqlstate(exc) != DEADLOCK_DETECTED or attempt == WRITE_ATTEMPTS:
                raise
            log.info("Deadlock entre escritas concorrentes (sala %s); repetindo", slot.room_id)
            if on_retry is not None:
                await on_retry()


async def _log_and_commit(
    session: AsyncSession,
    booking: Booking,
    user_id: int,
    action: LogAction,
    *,
    previous: dict | None,
) -> None:
    session.add(
        BookingLog(
            booking_id=booking.id,
            user_id=user_id,
            action=action,
            previous_state=previous,
            new_state=booking_snapshot(booking),
        )
    )
    await session.commit()


async def _conflict_error(
    session: AsyncSession, slot: _Slot, conflicts: list[Booking] | None = None
) -> BusinessError:
    if conflicts is None:
        conflicts = await find_conflicts(
            session,
            slot.room_id,
            slot.start_dt,
            slot.end_dt,
            exclude_booking_id=slot.exclude_booking_id,
        )
    alternatives = await alternative_rooms(
        session,
        slot.start_dt,
        slot.end_dt,
        requires_video=slot.requires_video,
        exclude_room_id=slot.room_id,
        exclude_booking_id=slot.exclude_booking_id,
    )
    if not conflicts:
        message = "Este horário acabou de ser reservado por outra pessoa."
    elif len(conflicts) == 1:
        b = conflicts[0]
        message = (
            f"A sala já está reservada para {b.requester_name} "
            f"({fmt_hhmm(b.start_time)}–{fmt_hhmm(b.end_time)})."
        )
    else:
        message = f"A sala já tem {len(conflicts)} reservas nesse período."
    return conflict(
        "BOOKING_CONFLICT",
        message,
        conflicts=[_json(booking_out(b)) for b in conflicts],
        alternatives=[_json(room_brief(r)) for r in alternatives],
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(
        getattr(exc.orig, "__cause__", None), "sqlstate", None
    )


def _is_overlap_violation(exc: IntegrityError) -> bool:
    if _sqlstate(exc) == EXCLUSION_VIOLATION:
        return True
    cause = getattr(exc.orig, "__cause__", None)
    if getattr(cause, "constraint_name", None) == OVERLAP_CONSTRAINT:
        return True
    return OVERLAP_CONSTRAINT in str(exc.orig)


async def _release_own_locks(
    user_id: int,
    *,
    lock_id: str | None = None,
    booking_id: str | None = None,
    slot: tuple[int, datetime, datetime] | None = None,
) -> None:
    """Regra 7: libera, entre as travas do PRÓPRIO usuário,
    (a) a `lock_id` enviada pelo cliente;
    (b) as ligadas à reserva (`booking_id`), onde quer que estejam;
    (c) as SEM booking_id que se sobrepõem a `slot` (sala, início, fim da posição gravada).
    Travas sobre a posição antiga de uma reserva movida não entram: podem ser de outra seleção.
    """
    store = get_lock_store()
    try:
        own: dict[str, LockInfo] = {}
        if lock_id:
            lk = await store.get(lock_id)
            if lk is not None and lk.user_id == user_id:
                own[lk.lock_id] = lk
        if booking_id is not None:
            for lk in await store.list_active():
                if lk.user_id == user_id and lk.booking_id == booking_id:
                    own[lk.lock_id] = lk
        if slot is not None:
            for lk in await store.find_overlapping(*slot):
                if lk.user_id == user_id and lk.booking_id is None:
                    own[lk.lock_id] = lk
        for lid in own:
            released = await store.release(lid, user_id=user_id)
            if released is not None:
                await publish(
                    EventType.SLOT_UNLOCKED, {"lock": _json(released.to_out()), "reason": "saved"}
                )
    except Exception:
        # A reserva já foi gravada; uma falha aqui não pode virar erro para o usuário.
        log.exception("Falha ao liberar soft locks após salvar a reserva")


async def _warnings(session: AsyncSession, out: BookingOut) -> list[BusinessWarning]:
    """Regra 8: avisos não bloqueantes sobre o estado salvo."""
    if out.status == BookingStatus.CANCELLED:
        return []
    room = await session.get(Room, out.room_id)
    if room is None:  # pragma: no cover - FK garante
        return []
    result: list[BusinessWarning] = []
    if not out.requires_video and room.effective_type == RoomType.VIDEO:
        normal = await free_rooms(
            session, out.start_time, out.end_time, RoomType.NORMAL, exclude_booking_id=out.id
        )
        if normal:
            result.append(
                BusinessWarning(
                    code="VIDEO_ROOM_NOT_NEEDED",
                    message=(
                        "Esta reunião não precisa de videoconferência e há salas normais livres "
                        "nesse horário. Considere liberar a sala de vídeo."
                    ),
                    data={"free_normal_rooms": [_json(room_brief(r)) for r in normal]},
                )
            )
    if out.requires_video and room.effective_type != RoomType.VIDEO:
        video = await free_rooms(
            session, out.start_time, out.end_time, RoomType.VIDEO, exclude_booking_id=out.id
        )
        if room.room_type == RoomType.VIDEO:
            message = f"O vídeo da sala {room.name} está em manutenção."
        else:
            message = f"A sala {room.name} não tem videoconferência."
        result.append(
            BusinessWarning(
                code="VIDEO_UNAVAILABLE",
                message=message,
                data={"free_video_rooms": [_json(room_brief(r)) for r in video]},
            )
        )
    if out.attendees and room.capacity > 0 and out.attendees > room.capacity:
        result.append(
            BusinessWarning(
                code="OVER_CAPACITY",
                message=(
                    f"A sala comporta {room.capacity} pessoas e a reunião terá "
                    f"{out.attendees} participantes."
                ),
                data={"capacity": room.capacity, "attendees": out.attendees},
            )
        )
    return result


def _slot_ref(booking: Booking) -> dict:
    """`previous` do BOOKING_UPDATED: onde a reserva estava antes."""
    s, e = to_local(booking.start_time), to_local(booking.end_time)
    return {
        "room_id": booking.room_id,
        "date": s.date().isoformat(),
        "start": s.strftime("%H:%M"),
        "end": e.strftime("%H:%M"),
    }


def _clean_ticket(ticket: str | None) -> str | None:
    """ "#48211" e "48211" são o mesmo chamado (o '#' quebraria o link do sistema de chamados)."""
    if ticket is None:
        return None
    ticket = ticket.strip().lstrip("#").strip()
    return ticket or None


def _unaccent_sql(column):
    return func.translate(func.lower(column), _ACCENTED, _PLAIN)


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _json(model) -> dict:
    return model.model_dump(mode="json")


def _booking_not_found() -> BusinessError:
    return not_found("BOOKING_NOT_FOUND", "Reserva não encontrada.")


def _room_inactive() -> BusinessError:
    return unprocessable("ROOM_INACTIVE", "Esta sala está desativada e não aceita reservas.")
