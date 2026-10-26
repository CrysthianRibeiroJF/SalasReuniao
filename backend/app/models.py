"""Modelos ORM. O DDL oficial está em alembic/versions/0001_initial.py (inclui a constraint GiST)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(enum.StrEnum):
    ADMIN = "ADMIN"
    RECEPTIONIST = "RECEPTIONIST"


class RoomType(enum.StrEnum):
    VIDEO = "VIDEO"
    NORMAL = "NORMAL"


class VideoStatus(enum.StrEnum):
    OPERATIONAL = "OPERATIONAL"
    MAINTENANCE = "MAINTENANCE"


class BookingStatus(enum.StrEnum):
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class PeriodType(enum.StrEnum):
    CUSTOM = "CUSTOM"  # Horário Específico
    FULL_DAY = "FULL_DAY"  # Dia Inteiro
    MORNING = "MORNING"  # Manhã Toda
    AFTERNOON = "AFTERNOON"  # Tarde Toda


class LogAction(enum.StrEnum):
    CREATED = "CREATED"
    IMPORTED = "IMPORTED"  # criada pela importação da planilha legada
    UPDATED = "UPDATED"  # mudou apenas dados (chamado, solicitante, assunto, vídeo...)
    MOVED_ROOM = "MOVED_ROOM"  # mudou de sala (pode ter mudado horário também)
    RESCHEDULED = "RESCHEDULED"  # mudou horário/data na mesma sala
    STATUS_CHANGED = "STATUS_CHANGED"  # check-in / não compareceu / reconfirmada
    CANCELLED = "CANCELLED"


def _enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    # VARCHAR + CHECK (criado na migration) em vez de ENUM nativo: evoluir valores fica trivial.
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=20,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        _enum(UserRole, "user_role"), nullable=False, default=UserRole.RECEPTIONIST
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Incrementado ao trocar a senha: invalida tokens já emitidos (claim "tv" do JWT).
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint(
            "room_type = 'VIDEO' OR video_status = 'OPERATIONAL'",
            name="ck_rooms_maintenance_only_video",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    room_type: Mapped[RoomType] = mapped_column(_enum(RoomType, "room_type"), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Recursos fixos: ["TV 65\"", "Quadro branco", ...]
    resources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    video_status: Mapped[VideoStatus] = mapped_column(
        _enum(VideoStatus, "video_status"), nullable=False, default=VideoStatus.OPERATIONAL
    )
    maintenance_ticket_id: Mapped[str | None] = mapped_column(String(50))
    maintenance_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def effective_type(self) -> RoomType:
        """Sala VIDEO com vídeo em manutenção é tratada como NORMAL na grade (RF02)."""
        if self.room_type == RoomType.VIDEO and self.video_status == VideoStatus.OPERATIONAL:
            return RoomType.VIDEO
        return RoomType.NORMAL


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_bookings_interval"),
        # RNF02: impede fisicamente sobreposição na mesma sala (exceto canceladas).
        ExcludeConstraint(
            ("room_id", "="),
            (func.tstzrange(text("start_time"), text("end_time"), text("'[)'")), "&&"),
            name="bookings_no_overlap",
            using="gist",
            where=text("status <> 'CANCELLED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    ticket_number: Mapped[str | None] = mapped_column(String(50), index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    requester_name: Mapped[str] = mapped_column(String(150), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    attendees: Mapped[int | None] = mapped_column(Integer)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_type: Mapped[PeriodType] = mapped_column(
        _enum(PeriodType, "period_type"), nullable=False, default=PeriodType.CUSTOM
    )
    is_full_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus, "booking_status"), nullable=False, default=BookingStatus.CONFIRMED
    )
    # Concorrência otimista: toda alteração incrementa; PATCH exige a versão que o cliente viu.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    # Impressão digital da célula de origem na planilha legada (só reservas IMPORTED). Persiste
    # mesmo após mover/cancelar, para que reimportar a planilha não ressuscite a reserva.
    import_key: Mapped[str | None] = mapped_column(String(100))

    room: Mapped[Room] = relationship(lazy="raise")
    creator: Mapped[User] = relationship(foreign_keys=[created_by], lazy="raise")
    updater: Mapped[User | None] = relationship(foreign_keys=[updated_by], lazy="raise")


class BookingLog(Base):
    __tablename__ = "booking_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[LogAction] = mapped_column(_enum(LogAction, "log_action"), nullable=False)
    previous_state: Mapped[dict | None] = mapped_column(JSONB)
    new_state: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User | None] = relationship(lazy="raise")
