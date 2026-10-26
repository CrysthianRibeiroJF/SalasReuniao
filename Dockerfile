# syntax=docker/dockerfile:1
#
# Agenda de Salas em Tempo Real — imagem única (API FastAPI + WebSocket + SPA React).
#
#   docker build -t agenda-salas .
#   docker compose up -d --build        (recomendado: ver docker-compose.yml e README.md)
#
# O processo roda como "agenda" (uid 10001), sem permissão de escrita em /app: arquivos gerados
# pela linha de comando vão para /tmp (ex.: export-xlsx -o /tmp/semana.xlsx + docker compose cp).
#
# Atrás de proxy corporativo: docker build --build-arg HTTP_PROXY=... --build-arg HTTPS_PROXY=... .
# (HTTP_PROXY/HTTPS_PROXY/NO_PROXY são build args pré-definidos do Docker.)

# ---------------------------------------------------------------- 1. build do frontend
FROM node:24-alpine AS frontend

WORKDIR /build/frontend

# Dependências primeiro: a camada do npm ci só é refeita quando o lockfile muda.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --fetch-timeout=120000 --fetch-retries=5

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------- 2. runtime (Python)
FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="agenda-salas" \
      org.opencontainers.image.description="Agenda de Salas em Tempo Real (FastAPI + React)" \
      org.opencontainers.image.version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN groupadd --system --gid 10001 agenda \
    && useradd --system --uid 10001 --gid agenda --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin agenda

WORKDIR /app/backend

# constraints.txt trava as versões testadas: reconstruir esta versão traz as mesmas bibliotecas.
COPY backend/requirements.txt backend/constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY --from=frontend /build/frontend/dist /app/frontend/dist

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Remove CR caso o arquivo tenha vindo com fim de linha do Windows (git autocrlf).
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

# A SPA é servida pela própria aplicação (mesma origem): CORS só para outros domínios, se preciso.
ENV AGENDA_ENV=production \
    AGENDA_FRONTEND_DIST=/app/frontend/dist \
    AGENDA_CORS_ORIGINS="[]" \
    WEB_CONCURRENCY=2

USER agenda:agenda

EXPOSE 8000

# GET /api/health: 503 (unhealthy) só com o banco fora do ar; Redis fora do ar responde 200 com
# "status": "degraded" (o REST continua funcionando e o tempo real se recupera sozinho).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

ENTRYPOINT ["docker-entrypoint.sh"]
