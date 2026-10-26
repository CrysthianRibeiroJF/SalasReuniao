"""Mantém o JWT do WebSocket fora dos logs.

O navegador não envia cabeçalhos no handshake do WebSocket, por isso o token vai na URL
(`/ws?token=<jwt>`), e o uvicorn registra o caminho completo: no log de acesso ("uvicorn.access",
requisições HTTP) e no "WebSocket /ws?token=... [accepted]" ("uvicorn.error"). Quem lê os logs não
pode sair com uma sessão válida: o filtro troca o valor por `***` nos argumentos e na mensagem,
mantendo a estrutura dos argumentos (o AccessFormatter do uvicorn desempacota a tupla).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

UVICORN_LOGGERS = ("uvicorn.access", "uvicorn.error")
REDACTED = "***"

_TOKEN_RE = re.compile(r"([?&]token=)[^&#\s\"']+", re.IGNORECASE)


def redact_token(text: str) -> str:
    """`/ws?token=eyJ...&x=1` -> `/ws?token=***&x=1`."""
    if "token=" not in text.lower():
        return text
    return _TOKEN_RE.sub(rf"\g<1>{REDACTED}", text)


def _redact(value: Any) -> Any:
    return redact_token(value) if isinstance(value, str) else value


class TokenRedactingFilter(logging.Filter):
    """Filtro de logger: nunca descarta registros, só reescreve o token."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_token(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact(arg) for arg in args)
        elif isinstance(args, Mapping):
            record.args = {key: _redact(value) for key, value in args.items()}
        return True


def install_token_redaction(logger_names: Iterable[str] = UVICORN_LOGGERS) -> None:
    """Instala o filtro nos loggers do uvicorn. Idempotente (create_app e lifespan chamam)."""
    for name in logger_names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactingFilter) for f in logger.filters):
            logger.addFilter(TokenRedactingFilter())
