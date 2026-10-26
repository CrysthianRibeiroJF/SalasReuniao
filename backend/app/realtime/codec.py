"""Serialização JSON do tempo real: envelopes dos clientes e metadados de travas no Redis."""

from __future__ import annotations

import enum
import json
import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.realtime.registry import LockInfo

log = logging.getLogger(__name__)


def _default(value: Any) -> Any:
    # Os serviços publicam dicts que podem conter modelos Pydantic, datas ou UUIDs: o JSON final
    # precisa ser idêntico ao da API REST, venha o evento da memória ou do Redis.
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return list(value)
    raise TypeError(f"Tipo não serializável em evento de tempo real: {type(value).__name__}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default, ensure_ascii=False, separators=(",", ":"))


def encode_lock(info: LockInfo) -> str:
    return dumps(
        {
            "lock_id": info.lock_id,
            "room_id": info.room_id,
            "date": info.date,
            "start": info.start,
            "end": info.end,
            "user_id": info.user_id,
            "user_name": info.user_name,
            "expires_at": info.expires_at,
            "conn_id": info.conn_id,
            "booking_id": info.booking_id,
            "slot_keys": list(info.slot_keys),
        }
    )


def decode_lock(raw: str | bytes | None) -> LockInfo | None:
    """Metadados ilegíveis (versão antiga, corrompidos) contam como trava inexistente."""
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return LockInfo(
            lock_id=data["lock_id"],
            room_id=int(data["room_id"]),
            date=date.fromisoformat(data["date"]),
            start=time.fromisoformat(data["start"]),
            end=time.fromisoformat(data["end"]),
            user_id=int(data["user_id"]),
            user_name=data["user_name"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            conn_id=data.get("conn_id"),
            booking_id=data.get("booking_id"),
            slot_keys=tuple(data.get("slot_keys") or ()),
        )
    except (ValueError, KeyError, TypeError):
        log.warning("Metadados de trava ilegíveis ignorados: %.200r", raw)
        return None
