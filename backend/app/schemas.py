"""Schemas Pydantic = contrato JSON da API (fonte da verdade para o frontend: src/api/types.ts).

Convenções:
- Datas locais: "YYYY-MM-DD"; horas locais: "HH:MM" (fuso settings.timezone).
- start_time/end_time: ISO 8601 com offset (ex.: "2026-10-26T14:00:00-03:00").
- Erros de negócio: HTTP 4xx com corpo {"detail": {"code": "...", "message": "...", ...}}.
- Entrada (InputModel, LocalDate, LocalTime, DbId): textos sem caractere NUL, ids dentro do INTEGER
  do PostgreSQL, datas entre 2000 e 2100 e horas sem fuso. Fora disso: 422 padrão do FastAPI (em
  vez de um 500 vindo do banco ou da aritmética de datas).
"""

from __future__ import annotations

from datetime import date as Date, datetime, time
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from app.models import BookingStatus, LogAction, PeriodType, RoomType, UserRole, VideoStatus

MAX_DB_INT = 2**31 - 1  # INTEGER do PostgreSQL
MIN_DATE = Date(2000, 1, 1)
MAX_DATE = Date(2100, 12, 31)


def check_date_range(value: Date) -> Date:
    if not MIN_DATE <= value <= MAX_DATE:
        raise ValueError(
            f"Data fora do intervalo aceito ({MIN_DATE:%d/%m/%Y} a {MAX_DATE:%d/%m/%Y})."
        )
    return value


def check_naive_time(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("Informe a hora local sem fuso horário (ex.: 14:00).")
    return value


def check_no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("O texto contém um caractere inválido (NUL).")
    return value


# Tipos de entrada reutilizados pelos routers (query/path) e schemas.
LocalDate = Annotated[Date, AfterValidator(check_date_range)]
LocalTime = Annotated[time, AfterValidator(check_naive_time)]
DbId = Annotated[int, Field(gt=0, le=MAX_DB_INT)]
HHMM = Annotated[
    time,
    AfterValidator(check_naive_time),
    PlainSerializer(lambda t: t.strftime("%H:%M"), return_type=str),
]


def _strip_or_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class InputModel(APIModel):
    """Corpo de requisição: nenhum texto (nem item de lista) pode ter o caractere NUL, que o
    PostgreSQL recusa."""

    @field_validator("*", mode="after")
    @classmethod
    def _reject_nul(cls, value: Any) -> Any:
        if isinstance(value, str):
            check_no_nul(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    check_no_nul(item)
        return value


# ---------------------------------------------------------------- erros


class ErrorDetail(APIModel):
    code: str
    message: str


class BusinessWarning(APIModel):
    """Aviso não bloqueante retornado junto de criação/edição."""

    # VIDEO_ROOM_NOT_NEEDED: reunião sem vídeo em sala VIDEO e há salas normais livres (RF06)
    # VIDEO_UNAVAILABLE: reunião precisa de vídeo mas a sala não tem vídeo operacional
    # OVER_CAPACITY: participantes > capacidade
    code: Literal["VIDEO_ROOM_NOT_NEEDED", "VIDEO_UNAVAILABLE", "OVER_CAPACITY"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- usuários / auth


class UserBrief(APIModel):
    id: int
    full_name: str


class UserOut(APIModel):
    id: int
    username: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class LoginIn(InputModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(APIModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut


class UserCreate(InputModel):
    username: str = Field(min_length=2, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=150)
    password: str = Field(min_length=6, max_length=200)
    role: UserRole = UserRole.RECEPTIONIST


class UserUpdate(InputModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    password: str | None = Field(default=None, min_length=6, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------- salas


class RoomBrief(APIModel):
    id: int
    name: str
    room_type: RoomType
    effective_type: RoomType
    capacity: int


class RoomOut(APIModel):
    id: int
    name: str
    room_type: RoomType  # tipo original
    effective_type: RoomType  # VIDEO só se room_type=VIDEO e vídeo OPERATIONAL
    capacity: int
    resources: list[str]
    video_status: VideoStatus
    maintenance_ticket_id: str | None = None
    maintenance_ticket_url: str | None = None
    maintenance_since: datetime | None = None
    display_order: int
    is_active: bool


class RoomCreate(InputModel):
    name: str = Field(min_length=1, max_length=100)
    room_type: RoomType
    capacity: int = Field(default=0, ge=0, le=10_000)
    resources: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    display_order: int | None = Field(default=None, ge=0, le=100_000)  # None = fim do grupo
    is_active: bool = True

    _strip_name = field_validator("name", mode="before")(lambda v: v.strip() if isinstance(v, str) else v)


class RoomUpdate(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    room_type: RoomType | None = None
    capacity: int | None = Field(default=None, ge=0, le=10_000)
    resources: list[Annotated[str, Field(max_length=100)]] | None = Field(
        default=None, max_length=50
    )
    display_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None


class MaintenanceIn(InputModel):
    ticket_id: str = Field(min_length=1, max_length=50)

    _strip = field_validator("ticket_id", mode="before")(lambda v: v.strip() if isinstance(v, str) else v)


# ---------------------------------------------------------------- reservas


class BookingCreate(InputModel):
    room_id: DbId
    date: LocalDate
    period_type: PeriodType = PeriodType.CUSTOM
    start: HHMM | None = None  # obrigatório se CUSTOM; ignorado nos presets
    end: HHMM | None = None
    ticket_number: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=200)
    requester_name: str = Field(min_length=1, max_length=150)
    requires_video: bool = False
    attendees: int | None = Field(default=None, gt=0, le=10_000)
    notes: str | None = Field(default=None, max_length=2000)
    # Soft lock que o cliente segura para esta seleção (liberado automaticamente ao salvar)
    lock_id: str | None = Field(default=None, max_length=100)

    _strip = field_validator("ticket_number", "title", "notes", mode="before")(_strip_or_none)
    _strip_req = field_validator("requester_name", mode="before")(
        lambda v: v.strip() if isinstance(v, str) else v
    )


class BookingUpdate(InputModel):
    """Edição/remanejamento. Envie só o que muda + `version` que o cliente tem."""

    version: int
    room_id: DbId | None = None
    date: LocalDate | None = None
    period_type: PeriodType | None = None
    start: HHMM | None = None
    end: HHMM | None = None
    ticket_number: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=200)
    requester_name: str | None = Field(default=None, min_length=1, max_length=150)
    requires_video: bool | None = None
    attendees: int | None = Field(default=None, gt=0, le=10_000)
    notes: str | None = Field(default=None, max_length=2000)
    lock_id: str | None = Field(default=None, max_length=100)

    _strip = field_validator("ticket_number", "title", "notes", mode="before")(_strip_or_none)


class BookingCancel(InputModel):
    version: int | None = None
    reason: str | None = Field(default=None, max_length=500)


class BookingStatusIn(InputModel):
    version: int | None = None
    status: Literal["CONFIRMED", "CHECKED_IN", "NO_SHOW"]


class BookingOut(APIModel):
    id: str
    room_id: int
    room_name: str
    ticket_number: str | None = None
    ticket_url: str | None = None
    title: str | None = None
    requester_name: str
    notes: str | None = None
    attendees: int | None = None
    date: Date  # data local
    start: HHMM  # hora local
    end: HHMM
    start_time: datetime
    end_time: datetime
    period_type: PeriodType
    is_full_day: bool
    requires_video: bool
    status: BookingStatus
    version: int
    # Selo "Reunião Presencial — Remanejável": sem vídeo ocupando sala VIDEO operacional (RF06/5.3)
    is_relocatable: bool
    # Precisa de vídeo mas a sala não tem vídeo operacional (ex.: entrou em manutenção)
    video_unavailable: bool
    created_by: UserBrief
    updated_by: UserBrief | None = None
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None


class BookingWriteResult(APIModel):
    booking: BookingOut
    warnings: list[BusinessWarning] = Field(default_factory=list)


class BookingLogOut(APIModel):
    id: int
    booking_id: str
    user: UserBrief | None = None
    action: LogAction
    previous_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    created_at: datetime


class AuditEntryOut(BookingLogOut):
    """Linha da tela de auditoria: log + resumo da reserva."""

    booking_ticket_number: str | None = None
    booking_requester_name: str | None = None
    booking_room_name: str | None = None
    booking_date: Date | None = None


# ---------------------------------------------------------------- soft locks / tempo real


class LockOut(APIModel):
    lock_id: str
    room_id: int
    date: Date
    start: HHMM
    end: HHMM
    user: UserBrief
    expires_at: datetime
    booking_id: str | None = None  # preenchido quando a trava é para mover/editar uma reserva


class OnlineUser(APIModel):
    id: int
    full_name: str
    connections: int = 1


# ---------------------------------------------------------------- configuração


class PeriodWindow(APIModel):
    start: HHMM
    end: HHMM


class ConfigOut(APIModel):
    timezone: str
    today: Date
    day_start: HHMM
    day_end: HHMM
    slot_minutes: int
    morning: PeriodWindow
    afternoon: PeriodWindow
    work_days: list[int]
    lock_ttl_seconds: int
    ticket_url_template: str | None = None
    realtime_backend: Literal["redis", "memory"]


# ---------------------------------------------------------------- importação


class ImportSheetReport(APIModel):
    sheet_name: str
    date: Date | None = None
    status: Literal["IMPORTED", "DRY_RUN", "SKIPPED_PAST", "SKIPPED_UNRECOGNIZED", "ERROR"]
    bookings_found: int = 0
    bookings_imported: int = 0
    bookings_skipped: int = 0
    warnings: list[str] = Field(default_factory=list)


class ImportReport(APIModel):
    dry_run: bool
    sheets: list[ImportSheetReport]
    total_found: int
    total_imported: int
    total_skipped: int
    unknown_rooms: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
