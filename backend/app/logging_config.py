"""Logs da aplicação (loggers "app.*") visíveis com o logging padrão do uvicorn.

O uvicorn configura só os loggers dele ("uvicorn", "uvicorn.error", "uvicorn.access"): sem isto os
registros INFO de "app.*" (ex.: "Tempo real iniciado (backend redis, instância app-1)") somem e
WARNING/ERROR saem crus, sem horário, nível nem origem. Se o logging já foi configurado por quem
roda a aplicação (handler no root, --log-config, gunicorn), nada é adicionado: sem linhas
duplicadas.
"""

from __future__ import annotations

import logging

APP_LOGGER = "app"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _AppLogHandler(logging.StreamHandler):
    """Marca o handler instalado aqui (configure_app_logging é idempotente)."""


def configure_app_logging() -> None:
    logger = logging.getLogger(APP_LOGGER)
    if logger.level == logging.NOTSET:
        logger.setLevel(_server_level())
    if any(isinstance(h, _AppLogHandler) for h in logger.handlers):
        return
    if logger.hasHandlers():  # o root (ou o próprio "app") já tem quem escreva os registros
        return
    handler = _AppLogHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)


def _server_level() -> int:
    """Segue o --log-level do uvicorn (padrão info); fora dele, INFO."""
    level = logging.getLogger("uvicorn.error").level
    return level if level != logging.NOTSET else logging.INFO
