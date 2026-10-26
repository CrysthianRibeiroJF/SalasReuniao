"""Endpoint WebSocket `/ws?token=<jwt>` (contrato, seção 6)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID

import jwt
from fastapi import APIRouter, WebSocket

from app.config import settings
from app.db import SessionLocal
from app.realtime.events import ClientMessage, EventType, make_envelope
from app.realtime.hub import Connection
from app.realtime.registry import LockInfo
from app.realtime.rooms import RoomDirectory
from app.realtime.runtime import (
    ACCESS_REVOKED_REASON,
    CLOSE_FORBIDDEN,
    CLOSE_UNAUTHORIZED,
    SESSION_EXPIRED_REASON,
    SESSION_REVOKED_REASON,
    RealtimeRuntime,
    lock_payload,
)
from app.security import AuthFailure, check_token
from app.timeutil import fmt_hhmm, is_minute_aligned, local_dt, within_operating_hours

log = logging.getLogger(__name__)

router = APIRouter()

CLOSE_INTERNAL_ERROR = 1011
CLOSE_UNAVAILABLE = 1013
MAX_MESSAGE_CHARS = 16 * 1024
_MAX_REQUEST_ID = 100
_MAX_DB_INT = 2**31 - 1
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HHMM_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

Handler = Callable[[RealtimeRuntime, Connection, dict[str, Any], str | None], Awaitable[None]]


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    runtime: RealtimeRuntime | None = getattr(websocket.app.state, "realtime", None)
    if runtime is None or not runtime.accepting:
        await _refuse(websocket, CLOSE_UNAVAILABLE, "Tempo real indisponível. Tente novamente.")
        return
    try:
        user = await _authenticate(websocket.query_params.get("token"), runtime.rooms)
    except Exception:
        log.exception("Falha ao autenticar conexão WebSocket")
        await _refuse(websocket, CLOSE_INTERNAL_ERROR, "Erro interno. Tente novamente.")
        return
    if isinstance(user, AuthFailure):
        await _refuse(websocket, *_REFUSALS[user])
        return

    await websocket.accept()
    try:
        conn = await runtime.open_connection(
            websocket,
            user_id=user.id,
            user_name=user.name,
            expires_at=user.expires_at,
            token_version=user.token_version,
        )
    except Exception:
        log.exception("Falha ao abrir a conexão de tempo real do usuário %s", user.id)
        await _close_quietly(websocket, CLOSE_INTERNAL_ERROR, "Erro interno. Tente novamente.")
        return
    try:
        await _receive_loop(runtime, conn, websocket)
    finally:
        await runtime.close_connection(conn)


@dataclass(frozen=True)
class Identity:
    id: int
    name: str
    expires_at: datetime | None  # vencimento do JWT: o socket é fechado (4401) nesse instante
    token_version: int = 0  # claim "tv": a revalidação fecha (4401) se a senha for trocada


# Recusa no handshake: sessão inválida/vencida/revogada -> 4401 (volta ao login);
# conta desativada ou removida -> 4403.
_REFUSALS: dict[AuthFailure, tuple[int, str]] = {
    AuthFailure.UNAUTHORIZED: (CLOSE_UNAUTHORIZED, SESSION_EXPIRED_REASON),
    AuthFailure.SESSION_REVOKED: (CLOSE_UNAUTHORIZED, SESSION_REVOKED_REASON),
    AuthFailure.ACCOUNT_DISABLED: (CLOSE_FORBIDDEN, ACCESS_REVOKED_REASON),
}


async def _authenticate(token: str | None, rooms: RoomDirectory) -> Identity | AuthFailure:
    # Sessão curta: o socket vive horas e não pode segurar uma conexão do pool.
    if not token:
        return AuthFailure.UNAUTHORIZED
    async with SessionLocal() as session:
        result = await check_token(token, session)
        user, claims = result.user, result.claims
        if user is None or claims is None:
            return result.failure or AuthFailure.UNAUTHORIZED
        identity = Identity(user.id, user.full_name, claims.expires_at, claims.token_version)
        if rooms.stale:
            # Aproveita a conexão já aberta: o primeiro clique não precisa ir ao banco.
            try:
                await rooms.load(session)
            except Exception:
                log.warning("Falha ao carregar as salas; nova tentativa no LOCK_SLOT",
                            exc_info=True)
        return identity


def token_expiry(token: str) -> datetime | None:
    """`exp` de um token JÁ validado por check_token (por isso sem checar assinatura)."""
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
        return datetime.fromtimestamp(float(exp), timezone.utc) if exp is not None else None
    except (jwt.PyJWTError, TypeError, ValueError, OverflowError, OSError):
        return None


async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
    # Fechar antes do accept vira HTTP 403 e o navegador só vê 1006: aceita e fecha com o código.
    try:
        await websocket.accept()
    except Exception:
        return
    await _close_quietly(websocket, code, reason)


async def _close_quietly(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass


async def _receive_loop(runtime: RealtimeRuntime, conn: Connection, websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None:
            try:
                text = (message.get("bytes") or b"").decode("utf-8")
            except UnicodeDecodeError:
                conn.send(_error("INVALID_JSON", "Mensagem inválida: JSON malformado."))
                continue
        await handle_message(runtime, conn, text)


async def handle_message(runtime: RealtimeRuntime, conn: Connection, text: str) -> None:
    """Processa uma mensagem do cliente. Erros viram ERROR; o socket nunca cai por causa delas."""
    if len(text) > MAX_MESSAGE_CHARS:
        conn.send(_error("MESSAGE_TOO_LARGE", "Mensagem muito grande."))
        return
    try:
        payload = json.loads(text)
    except ValueError:
        conn.send(_error("INVALID_JSON", "Mensagem inválida: JSON malformado."))
        return
    if not isinstance(payload, dict):
        conn.send(_error("INVALID_MESSAGE", 'Mensagem inválida: esperado um objeto com "type".'))
        return
    request_id = _request_id(payload.get("request_id"))
    msg_type, data = payload.get("type"), payload.get("data")
    if data is None:
        data = {}
    if not isinstance(msg_type, str) or not isinstance(data, dict):
        conn.send(
            _error(
                "INVALID_MESSAGE",
                'Mensagem inválida: esperado {"type": texto, "data": objeto}.',
                request_id,
            )
        )
        return
    handler = _HANDLERS.get(msg_type)
    if handler is None:
        conn.send(
            _error("UNKNOWN_TYPE", f"Tipo de mensagem desconhecido: {msg_type[:50]}.", request_id)
        )
        return
    try:
        await handler(runtime, conn, data, request_id)
    except Exception:
        log.exception("Falha ao processar %s da conexão %s", msg_type, conn.conn_id)
        conn.send(
            _error("INTERNAL_ERROR", "Erro interno ao processar a mensagem. Tente novamente.",
                   request_id)
        )


# ---------------------------------------------------------------------- LOCK_SLOT


@dataclass(frozen=True)
class LockRequest:
    room_id: int
    date: date
    start: time
    end: time
    booking_id: str | None = None


class InvalidLockRequest(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_lock_request(data: dict[str, Any]) -> LockRequest:
    """Valida o pedido de trava (formato e regras de calendário). Mensagens em pt-BR."""
    room_id = data.get("room_id")
    if isinstance(room_id, bool) or not isinstance(room_id, int) or not 0 < room_id <= _MAX_DB_INT:
        raise InvalidLockRequest("INVALID_MESSAGE", "Sala inválida.")
    d = _parse_date(data.get("date"))
    start, end = _parse_hhmm(data.get("start")), _parse_hhmm(data.get("end"))
    booking_id = _parse_booking_id(data.get("booking_id"))
    if end <= start:
        raise InvalidLockRequest(
            "INVALID_INTERVAL", "O horário de término deve ser depois do início."
        )
    if not (is_minute_aligned(start) and is_minute_aligned(end)):
        raise InvalidLockRequest("INVALID_TIME", "Os horários devem ser múltiplos de 5 minutos.")
    if not within_operating_hours(local_dt(d, start), local_dt(d, end)):
        raise InvalidLockRequest(
            "OUTSIDE_OPERATING_HOURS",
            f"Fora do horário de funcionamento "
            f"({fmt_hhmm(settings.day_start)}–{fmt_hhmm(settings.day_end)}).",
        )
    return LockRequest(room_id=room_id, date=d, start=start, end=end, booking_id=booking_id)


def _parse_date(value: Any) -> date:
    if isinstance(value, str) and _DATE_RE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise InvalidLockRequest("INVALID_DATE", "Data inválida. Use o formato AAAA-MM-DD.")


def _parse_hhmm(value: Any) -> time:
    if isinstance(value, str) and _HHMM_RE.fullmatch(value):
        return time.fromisoformat(value)
    raise InvalidLockRequest("INVALID_TIME", "Horário inválido. Use o formato HH:MM.")


def _parse_booking_id(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise InvalidLockRequest("INVALID_MESSAGE", "Reserva inválida.") from None


def locked_by_message(conflicts: list[LockInfo]) -> str:
    names = list(dict.fromkeys(lock.user_name for lock in conflicts))
    if not names:
        return "Este horário está sendo selecionado agora. Tente novamente."
    if len(names) == 1:
        return f"{names[0]} está reservando este horário agora."
    return f"{', '.join(names[:-1])} e {names[-1]} estão reservando este horário agora."


async def _lock_slot(
    runtime: RealtimeRuntime, conn: Connection, data: dict[str, Any], request_id: str | None
) -> None:
    try:
        req = parse_lock_request(data)
    except InvalidLockRequest as exc:
        conn.send(_denied(exc.code, exc.message, [], request_id))
        return
    try:
        active = await runtime.rooms.is_active(req.room_id)
    except Exception:
        log.exception("Falha ao consultar a sala %s", req.room_id)
        conn.send(
            _denied("UNAVAILABLE", "Não foi possível validar a sala agora. Tente novamente.", [],
                    request_id)
        )
        return
    if active is None:
        conn.send(_denied("ROOM_NOT_FOUND", "Sala não encontrada.", [], request_id))
        return
    if not active:
        conn.send(_denied("ROOM_INACTIVE", "Esta sala está desativada.", [], request_id))
        return

    result = await runtime.store.acquire_detailed(
        room_id=req.room_id,
        date=req.date,
        start=req.start,
        end=req.end,
        user_id=conn.user_id,
        user_name=conn.user_name,
        conn_id=conn.conn_id,
        booking_id=req.booking_id,
    )
    for previous in result.replaced:
        await runtime.announce_unlocked(previous, "released")
    if result.lock is None:
        reason = "LOCKED_BY_OTHER" if result.conflicts else "LOCK_BUSY"
        message = locked_by_message(result.conflicts)
        conn.send(_denied(reason, message, result.conflicts, request_id))
        return
    acquired = {"lock": lock_payload(result.lock)}
    conn.send(make_envelope(EventType.LOCK_ACQUIRED, acquired, request_id))
    await runtime.announce_locked(result.lock)


# ---------------------------------------------------------------------- UNLOCK / REFRESH / PING


async def _unlock_slot(
    runtime: RealtimeRuntime, conn: Connection, data: dict[str, Any], request_id: str | None
) -> None:
    lock_id = _lock_id(data)
    if lock_id is None:
        conn.send(_error("INVALID_MESSAGE", "Informe o lock_id da seleção.", request_id))
        return
    lock = await runtime.store.get(lock_id)
    if lock is not None and lock.user_id != conn.user_id:
        conn.send(
            _error("NOT_LOCK_OWNER", "Somente quem fez a seleção pode liberá-la.", request_id)
        )
        return
    released = await runtime.store.release(lock_id, user_id=conn.user_id) if lock else None
    if released is None:
        conn.send(_error("LOCK_NOT_FOUND", "Esta seleção expirou ou já foi liberada.", request_id))
        return
    await runtime.announce_unlocked(released, "released")


async def _refresh_lock(
    runtime: RealtimeRuntime, conn: Connection, data: dict[str, Any], request_id: str | None
) -> None:
    lock_id = _lock_id(data)
    if lock_id is None:
        conn.send(_error("INVALID_MESSAGE", "Informe o lock_id da seleção.", request_id))
        return
    refreshed = await runtime.store.refresh(lock_id, user_id=conn.user_id)
    if refreshed is None:
        current = await runtime.store.get(lock_id)
        if current is not None and current.user_id != conn.user_id:
            conn.send(
                _error("NOT_LOCK_OWNER", "Somente quem fez a seleção pode renová-la.", request_id)
            )
        else:
            conn.send(
                _error("LOCK_NOT_FOUND", "Esta seleção expirou ou já foi liberada.", request_id)
            )
        return
    conn.send(
        make_envelope(EventType.LOCK_REFRESHED, {"lock": lock_payload(refreshed)}, request_id)
    )
    await runtime.announce_locked(refreshed)


async def _ping(
    runtime: RealtimeRuntime, conn: Connection, data: dict[str, Any], request_id: str | None
) -> None:
    conn.send(make_envelope(EventType.PONG, {}, request_id))


_HANDLERS: dict[str, Handler] = {
    ClientMessage.LOCK_SLOT: _lock_slot,
    ClientMessage.UNLOCK_SLOT: _unlock_slot,
    ClientMessage.REFRESH_LOCK: _refresh_lock,
    ClientMessage.PING: _ping,
}


# ---------------------------------------------------------------------- utilitários


def _lock_id(data: dict[str, Any]) -> str | None:
    value = data.get("lock_id")
    if isinstance(value, str) and 0 < len(value) <= 100:
        return value
    return None


def _request_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return value[:_MAX_REQUEST_ID]
    return None


def _error(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return make_envelope(EventType.ERROR, {"code": code, "message": message}, request_id)


def _denied(
    reason: str, message: str, conflicts: list[LockInfo], request_id: str | None
) -> dict[str, Any]:
    return make_envelope(
        EventType.LOCK_DENIED,
        {"reason": reason, "message": message, "conflicts": [lock_payload(c) for c in conflicts]},
        request_id,
    )
