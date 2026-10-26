"""Erros de negócio dos serviços.

Viram HTTP `{"detail": {"code": ..., "message": ..., **extra}}` pelo handler registrado em
app/main.py. `extra` contém somente dados JSON (schemas já serializados com mode="json"), para que
o mesmo erro possa ser logado, reenviado por WebSocket ou exibido num relatório de importação.
"""

from __future__ import annotations

from typing import Any


class BusinessError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra: dict[str, Any] = extra or {}
        self.headers = headers  # cabeçalhos HTTP extras da resposta (ex.: Retry-After)

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}

    def __repr__(self) -> str:
        return f"BusinessError({self.status_code}, {self.code!r}, {self.message!r})"


def not_found(code: str, message: str) -> BusinessError:
    return BusinessError(404, code, message)


def conflict(code: str, message: str, **extra: Any) -> BusinessError:
    return BusinessError(409, code, message, extra)


def unprocessable(code: str, message: str, **extra: Any) -> BusinessError:
    return BusinessError(422, code, message, extra)


def unauthorized(code: str, message: str) -> BusinessError:
    return BusinessError(401, code, message)
