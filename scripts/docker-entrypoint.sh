#!/bin/sh
# Entrypoint da imagem Docker da Agenda de Salas.
#
# Sem argumentos: aplica as migrations (python -m app.cli init-db, que também cria o administrador
# inicial de AGENDA_BOOTSTRAP_ADMIN_* quando não há usuários) e inicia o uvicorn na porta 8000.
# Com argumentos: executa o comando pedido dentro de backend/, por exemplo
#   docker compose run --rm app python -m app.cli create-user --username ana --name "Ana" --role RECEPTIONIST
#
# Variáveis do próprio entrypoint (as da aplicação usam o prefixo AGENDA_, ver README.md):
#   WEB_CONCURRENCY      processos do uvicorn (padrão 2). Mais de 1 exige AGENDA_REDIS_URL.
#   UVICORN_ACCESS_LOG   1/true liga o log de acesso por requisição (padrão: desligado).
#   FORWARDED_ALLOW_IPS  IPs de proxy confiáveis para X-Forwarded-* (padrão "*": só exponha a porta
#                        8000 para o proxy reverso).
#   AGENDA_SKIP_INIT_DB  1 pula o init-db (réplicas extras quando outra já aplicou as migrations).
set -eu

cd /app/backend

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

is_true() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1 | true | yes | on) return 0 ;;
        *) return 1 ;;
    esac
}

workers="${WEB_CONCURRENCY:-2}"
case "$workers" in
    '' | *[!0-9]*)
        echo "WEB_CONCURRENCY inválido: '$workers' (use um número inteiro >= 1)." >&2
        exit 64
        ;;
esac
if [ "$workers" -lt 1 ]; then
    workers=1
fi
if [ "$workers" -gt 1 ] && [ -z "${AGENDA_REDIS_URL:-}" ]; then
    # Sem Redis as travas e os eventos ficam na memória de cada processo: com 2+ processos uma
    # recepcionista deixaria de ver as ações das outras. Melhor 1 processo correto que 2 quebrados.
    echo "Aviso: AGENDA_REDIS_URL vazio; usando 1 processo em vez de $workers (vários exigem Redis)." >&2
    workers=1
fi

if is_true "${AGENDA_SKIP_INIT_DB:-0}"; then
    echo "AGENDA_SKIP_INIT_DB ativo: migrations não serão aplicadas por esta instância."
elif ! python -m app.cli init-db; then
    # Sem isto o container só reinicia em loop ("Restarting") sem dizer onde olhar.
    echo "init-db falhou (veja o erro acima: acesso ao banco/POSTGRES_PASSWORD, senha do" \
         "administrador inicial ou AGENDA_JWT_SECRET). A aplicação não foi iniciada." >&2
    exit 1
fi

set -- uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "$workers" \
    --proxy-headers \
    --ws-per-message-deflate false \
    --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"

if ! is_true "${UVICORN_ACCESS_LOG:-0}"; then
    set -- "$@" --no-access-log
fi

echo "Iniciando a Agenda de Salas na porta 8000 ($workers processo(s))..."
exec "$@"
