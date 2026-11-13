"""Dados de demonstração (`python -m app.cli seed-demo`, somente AGENDA_ENV=development).

Credenciais de DEMONSTRAÇÃO (nunca use em produção):
    admin     / admin123      ADMIN
    recepcao1 / recepcao123   RECEPTIONIST
    recepcao2 / recepcao123   RECEPTIONIST

Idempotente: salas e usuários são encontrados pelo nome/login; uma reserva de exemplo só é criada
se o horário da sala estiver livre (rodar de novo não duplica nada).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PeriodType, Room, RoomType, User, UserRole
from app.schemas import BookingCreate, RoomCreate, UserCreate
from app.services import bookings as bookings_service
from app.services import rooms as rooms_service
from app.services import users as users_service
from app.timeutil import resolve_period, today_local, week_monday

DEMO_USERS: tuple[tuple[str, str, str, UserRole], ...] = (
    ("admin", "Administrador", "admin123", UserRole.ADMIN),
    ("recepcao1", "Ana Souza", "recepcao123", UserRole.RECEPTIONIST),
    ("recepcao2", "Beatriz Lima", "recepcao123", UserRole.RECEPTIONIST),
)

# (nome, tipo, capacidade, recursos)
DEMO_ROOMS: tuple[tuple[str, RoomType, int, list[str]], ...] = (
    ("Sala 01 - Executiva", RoomType.VIDEO, 12, ["Videoconferência", 'TV 75"', "Quadro branco"]),
    ("Sala 02 - Diretoria", RoomType.VIDEO, 10, ["Videoconferência", 'TV 65"', "Ar-condicionado"]),
    ("Sala 03 - Treinamento", RoomType.VIDEO, 20, ["Videoconferência", "Projetor", "Caixa de som"]),
    ("Sala 04 - Inovação", RoomType.VIDEO, 8, ["Videoconferência", "Tela interativa"]),
    ("Sala 05 - Reunião", RoomType.NORMAL, 6, ['TV 50"']),
    ("Sala 06 - Reunião", RoomType.NORMAL, 6, ["Quadro branco"]),
    ("Sala 07 - Atendimento", RoomType.NORMAL, 4, []),
    ("Sala 08 - Projetos", RoomType.NORMAL, 8, ["Quadro branco", "Flip chart"]),
    ("Sala 09 - Auditório", RoomType.NORMAL, 40, ["Projetor", "Microfone", "Caixa de som"]),
    ("Sala 10 - Entrevistas", RoomType.NORMAL, 3, []),
)


@dataclass(frozen=True)
class _DemoBooking:
    weekday: int  # 0 = segunda
    room: str
    period: PeriodType
    start: time | None
    end: time | None
    requester: str
    title: str | None
    ticket: str | None
    requires_video: bool = False
    attendees: int | None = None
    creator: str = "recepcao1"


def _demo(
    weekday: int,
    room: str,
    when: str | PeriodType,
    requester: str,
    title: str | None,
    ticket: str | None,
    **kw,
) -> _DemoBooking:
    """`when` = "HH:MM-HH:MM" (Horário Específico) ou um PeriodType de período fixo."""
    if isinstance(when, PeriodType):
        return _DemoBooking(weekday, room, when, None, None, requester, title, ticket, **kw)
    start, end = (time.fromisoformat(t) for t in when.split("-"))
    return _DemoBooking(
        weekday, room, PeriodType.CUSTOM, start, end, requester, title, ticket, **kw
    )


FULL_DAY, MORNING, AFTERNOON = PeriodType.FULL_DAY, PeriodType.MORNING, PeriodType.AFTERNOON

# fmt: off
DEMO_BOOKINGS: tuple[_DemoBooking, ...] = (
    # segunda
    _demo(0, "Sala 01 - Executiva", "09:00-10:30", "Carla Mendes", "Diretoria Financeira",
          "48211", requires_video=True, attendees=8),
    _demo(0, "Sala 09 - Auditório", FULL_DAY, "Marcos Silva", "Integração de novos colaboradores",
          "48190", attendees=35, creator="recepcao2"),
    _demo(0, "Sala 05 - Reunião", "14:00-15:00", "João Pereira", "RH - Entrevista", "48230"),
    # terça
    _demo(1, "Sala 02 - Diretoria", MORNING, "Fernanda Costa", "Planejamento Estratégico",
          "48250", requires_video=True, attendees=10),
    # presencial ocupando sala de vídeo: aparece como "Remanejável" (RF06)
    _demo(1, "Sala 03 - Treinamento", "13:00-15:00", "Ricardo Alves", "Treinamento Comercial",
          "48255", attendees=12, creator="recepcao2"),
    _demo(1, "Sala 06 - Reunião", "10:00-11:00", "Equipe de TI", "Reunião interna", None),
    # quarta
    _demo(2, "Sala 04 - Inovação", "08:30-09:30", "Patrícia Rocha", "Squad Produtos",
          "48262", requires_video=True),
    _demo(2, "Sala 01 - Executiva", "14:00-16:00", "Luís Fernandes", "Comitê de Investimentos",
          "48270", requires_video=True, attendees=9),
    _demo(2, "Sala 07 - Atendimento", "15:00-16:30", "Sônia Martins", "Atendimento a fornecedor",
          "48275", creator="recepcao2"),
    # quinta
    _demo(3, "Sala 02 - Diretoria", AFTERNOON, "Gustavo Nogueira", "Conselho Consultivo",
          "48281", requires_video=True, attendees=7),
    _demo(3, "Sala 08 - Projetos", "09:00-12:00", "Juliana Prado", "Workshop de Processos",
          "48284", attendees=8),
    _demo(3, "Sala 05 - Reunião", "16:00-17:00", "Diretoria Jurídica", None, "48290"),
    # sexta
    _demo(4, "Sala 01 - Executiva", "10:00-11:00", "Rafael Duarte", "Alinhamento semanal",
          "48301", attendees=5, creator="recepcao2"),
    _demo(4, "Sala 10 - Entrevistas", "11:00-11:30", "Camila Torres", "Entrevista - Estágio", None),
    _demo(4, "Sala 03 - Treinamento", "14:00-18:00", "Eduardo Ramos", "Treinamento Teams",
          "48310", requires_video=True, attendees=18),
)
# fmt: on


@dataclass
class SeedSummary:
    rooms_created: list[str] = field(default_factory=list)
    users_created: list[str] = field(default_factory=list)
    bookings_created: int = 0
    bookings_skipped: int = 0
    week_start: date | None = None


async def seed_demo(
    session: AsyncSession, *, today: date | None = None, include_admin: bool = True
) -> SeedSummary:
    summary = SeedSummary()
    users = await _ensure_users(session, summary, include_admin=include_admin)
    rooms = await _ensure_rooms(session, summary)

    monday = demo_week_monday(today or today_local())
    summary.week_start = monday
    for demo in DEMO_BOOKINGS:
        d = monday + timedelta(days=demo.weekday)
        room = rooms[demo.room]
        start_dt, end_dt, _ = resolve_period(demo.period, d, demo.start, demo.end)
        if await bookings_service.find_conflicts(session, room.id, start_dt, end_dt):
            summary.bookings_skipped += 1
            continue
        data = BookingCreate(
            room_id=room.id,
            date=d,
            period_type=demo.period,
            start=demo.start,
            end=demo.end,
            ticket_number=demo.ticket,
            title=demo.title,
            requester_name=demo.requester,
            requires_video=demo.requires_video,
            attendees=demo.attendees,
        )
        await bookings_service.create_booking(
            session, data, users[demo.creator], check_locks=False, publish_events=False
        )
        summary.bookings_created += 1
    return summary


def demo_week_monday(today: date) -> date:
    """Segunda-feira da semana de demonstração: a atual, ou a próxima quando hoje é um dia não útil
    depois do último dia útil da semana (fim de semana): quem roda o seed no sábado ou no
    domingo abre a agenda e já vê a semana que vem com reservas, em vez de uma semana encerrada."""
    monday = week_monday(today)
    if today.weekday() not in settings.work_days and today.weekday() > max(settings.work_days):
        return monday + timedelta(days=7)
    return monday


async def _ensure_users(
    session: AsyncSession, summary: SeedSummary, *, include_admin: bool = True
) -> dict[str, User]:
    result: dict[str, User] = {}
    for username, full_name, password, role in DEMO_USERS:
        if role is UserRole.ADMIN and not include_admin:
            continue
        user = await users_service.get_by_username(session, username)
        if user is None:
            user = await users_service.create_user(
                session,
                UserCreate(username=username, full_name=full_name, password=password, role=role),
            )
            summary.users_created.append(username)
        result[username] = user
    return result


async def _ensure_rooms(session: AsyncSession, summary: SeedSummary) -> dict[str, Room]:
    result: dict[str, Room] = {}
    for order, (name, room_type, capacity, resources) in enumerate(DEMO_ROOMS, start=1):
        room = await session.scalar(select(Room).where(func.lower(Room.name) == name.lower()))
        if room is None:
            created = await rooms_service.create_room(
                session,
                RoomCreate(
                    name=name,
                    room_type=room_type,
                    capacity=capacity,
                    resources=resources,
                    display_order=order,
                ),
            )
            room = await session.get(Room, created.id)
            summary.rooms_created.append(name)
        result[name] = room
    return result


__all__ = [
    "DEMO_BOOKINGS",
    "DEMO_ROOMS",
    "DEMO_USERS",
    "SeedSummary",
    "demo_week_monday",
    "seed_demo",
]
