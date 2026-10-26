# Agenda de Salas em Tempo Real

Aplicação web para a recepção agendar as salas de reunião — de **videoconferência** e
**normais** — substituindo a planilha compartilhada de uma aba por dia. A tela continua parecida
com a planilha (salas em colunas, horários em linhas, abas de dias embaixo), mas agora:

- todas as recepcionistas veem as alterações umas das outras **na hora**, sem recarregar;
- o banco de dados **impede fisicamente** duas reservas na mesma sala no mesmo horário;
- quem está reservando um horário aparece para as colegas como **"Reservando: Fulana"**;
- cada reserva leva o **número do chamado**, com link direto para o sistema de chamados;
- salas de vídeo usadas sem necessidade ficam marcadas como **remanejáveis**;
- tudo o que acontece fica registrado no **histórico** (quem criou, alterou, moveu ou cancelou).

Documentos do projeto:

| Documento | Para quem | Conteúdo |
| --- | --- | --- |
| [docs/MANUAL_RECEPCAO.md](docs/MANUAL_RECEPCAO.md) | Recepção | Guia prático da tela, atalhos e situações do dia a dia |
| [docs/OPERACAO.md](docs/OPERACAO.md) | Administração / TI | Virada da planilha (go-live), rotinas, problemas comuns, plano de retorno |
| [docs/IMPORTACAO.md](docs/IMPORTACAO.md) | Administração / TI | Formato da planilha legada, importação e exportação Excel |
| [docs/PLANEJAMENTO.md](docs/PLANEJAMENTO.md) | Todos | Planejamento funcional original (requisitos RF/RNF, fluxos, UX, fases) |
| [docs/CONTRATO.md](docs/CONTRATO.md) | Desenvolvimento | Contrato técnico: modelo de dados, API REST, WebSocket, regras |
| [frontend/README.md](frontend/README.md) | Desenvolvimento | Organização e regras do frontend |

## Sumário

1. [Funcionalidades e requisitos](#1-funcionalidades-e-requisitos)
2. [Arquitetura](#2-arquitetura)
3. [Início rápido no Windows (desenvolvimento)](#3-início-rápido-no-windows-desenvolvimento)
4. [Produção com Docker Compose](#4-produção-com-docker-compose)
5. [Configuração](#5-configuração)
6. [Operação](#6-operação)
7. [Proxy reverso e WebSocket](#7-proxy-reverso-e-websocket)
8. [Escala](#8-escala)
9. [Segurança](#9-segurança)
10. [Testes](#10-testes)
11. [Estrutura do projeto](#11-estrutura-do-projeto)

---

## 1. Funcionalidades e requisitos

Requisitos do [planejamento](docs/PLANEJAMENTO.md) e como cada um é atendido:

| Requisito | Como é atendido |
| --- | --- |
| **RF01 — Tipos de sala** | *Administração → Salas*: nome, tipo (Videoconferência ou Normal), capacidade, recursos fixos (aparecem na dica do cabeçalho), ordem da coluna e ativa/inativa (salas nunca são apagadas, para preservar o histórico; só dá para desativar uma sala sem reservas futuras — o sistema lista as que precisam ser remanejadas). Na grade, o bloco **Videoconferência** fica à esquerda, com destaque e ícone de câmera, e o bloco **Salas Normais** à direita. |
| **RF02 — Vídeo em manutenção** | Qualquer usuário marca, pelo ícone no cabeçalho da sala, que o vídeo está em manutenção, informando o chamado de TI. A sala passa para o bloco das normais com o selo *"Vídeo em manutenção · Chamado X"* (com link) e deixa de ser oferecida para reuniões com vídeo até ser liberada. |
| **RF03 — Grade diária em abas** | Abas `Seg 26/10` … `Sex 30/10` na barra inferior, com o número de reservas de cada dia, setas de semana, botão **Hoje** e **+ Calendário** (sábado/domingo viram aba extra). O dia exibido fica na URL (`?date=2026-10-26`). Linha da hora atual e horários passados esmaecidos. |
| **RF04 — Horário e Dia Inteiro** | No modal, *Tipo de Período*: **Horário Específico**, **Dia Inteiro** (08:00–19:00), **Manhã** (08:00–12:00) ou **Tarde** (13:00–19:00), com 1 clique ou `Alt+1..4`. O bloco mescla as linhas que ocupa. Conflito no Dia Inteiro mostra as duas saídas do fluxo 5.2: escolher outra sala livre o dia todo ou remanejar a reunião que está no caminho, com 1 clique cada. |
| **RF05 — Chamados** | *Nº do Chamado* é o primeiro campo do modal e vira link (`AGENDA_TICKET_URL_TEMPLATE`). A busca instantânea (`/` ou `Ctrl+K`) encontra por chamado, solicitante ou assunto, sem diferenciar acentos, e leva direto ao dia e à reserva, destacada. |
| **RF06 — Otimização de vídeo** | Switch *"Precisa de Videoconferência?"* (padrão **Não**). Reunião sem vídeo numa sala de vídeo gera aviso com as salas normais livres no mesmo horário (clique troca a sala) e o bloco recebe o selo *"Reunião Presencial — Remanejável"*; basta arrastá-lo para uma sala normal livre. Reunião que precisa de vídeo numa sala sem vídeo funcionando recebe *"⚠ Precisa de vídeo"*. |
| **RF07 — Histórico e auditoria** | Toda escrita grava em `booking_logs`, **na mesma transação**, quem fez, quando e o estado anterior e o novo (criou, importou, editou, trocou de sala, mudou o horário, mudou o status, cancelou). Aba *Histórico* em cada reserva e *Administração → Auditoria* com filtros por dia, pessoa e ação. |
| **RF08 — Excel** | Importação tolerante da planilha legada com **simulação** e relatório detalhado (tela ou linha de comando) e exportação do dia ou da semana em `.xlsx` no formato espelho da grade. Importar de novo não duplica nem traz de volta reservas canceladas ou remanejadas no sistema, e horários que alguém está selecionando na grade são pulados. Detalhes em [docs/IMPORTACAO.md](docs/IMPORTACAO.md). |
| **RNF01 — Tempo real < 200 ms** | WebSocket `/ws`: cada gravação publica um evento depois do commit, entregue a todas as telas conectadas; a seleção de células vira uma trava visível (*"Reservando: Fulana"*, 120 s, renovada enquanto o modal está aberto). Com Redis, os eventos atravessam processos e servidores (pub/sub `agenda:events`). O cliente reconecta sozinho (até ~10 s entre tentativas) e recarrega o dia ao voltar. |
| **RNF02 — Consistência atômica** | Constraint `bookings_no_overlap` (`EXCLUDE USING gist` com `tstzrange`) no PostgreSQL: duas reservas sobrepostas na mesma sala são impossíveis, mesmo disparadas no mesmo milissegundo. A segunda recebe HTTP 409 `BOOKING_CONFLICT` com as alternativas livres. Coberto por `backend/tests/test_booking_concurrency.py`. Edição concorrente usa versão otimista (409 `STALE_VERSION`). |
| **RNF03 — Regra dos 3 cliques** | Clique na célula livre (**1**) → o modal abre com o foco no chamado → digite o chamado, `Tab`, o solicitante → `Enter` ou **Salvar** (**2**). Só com teclado: setas até a célula, `Enter`, digitar, `Enter`. |

## 2. Arquitetura

```
 +----------------------------- Navegador (SPA React + TypeScript + Tailwind) -----------------------------+
 |  grade de salas · modal rápido · abas de dias · busca · administração                                    |
 +--------------------+-----------------------------------------------------------+------------------------+
                      | REST /api/* (JSON, Authorization: Bearer <JWT>)           | WebSocket /ws?token=<JWT>
                      |                                                           | travas, eventos, presença
 +--------------------v-----------------------------------------------------------v------------------------+
 |  FastAPI + uvicorn (1..N processos)                                                                      |
 |  app/api (rotas) -> app/services (regras, auditoria) -> app/realtime (WebSocket, travas, eventos)        |
 |  app/excel (importação/exportação) · serve também o build da SPA em "/" (AGENDA_FRONTEND_DIST)           |
 +--------------------+-----------------------------------------------------------+------------------------+
                      | SQL (asyncpg)                                             | SET NX + TTL, ZSET de expiração,
                      |                                                           | presença, pub/sub agenda:events
 +--------------------v-------------------------+            +--------------------v------------------------+
 |  PostgreSQL 16                               |            |  Redis 7                                     |
 |  users · rooms · bookings · booking_logs     |            |  soft locks (lock:room_{id}:{dia}:{HH:MM})   |
 |  bookings_no_overlap (GiST) = "hard lock"    |            |  opcional com 1 processo: sem Redis, tudo    |
 |  migrations Alembic                          |            |  fica na memória do processo                 |
 +----------------------------------------------+            +----------------------------------------------+
```

Uma reserva, do clique ao aparecimento em todas as telas:

1. A recepcionista clica (ou arrasta) em células livres → `LOCK_SLOT` pelo WebSocket → o servidor
   grava a trava (uma chave por slot, tudo-ou-nada, TTL de 120 s) e envia `SLOT_LOCKED` a todos:
   as colegas veem o hachurado *"Reservando: Fulana"*.
2. Ela salva → `POST /api/bookings` → o serviço valida horário, travas de outras pessoas e
   conflitos; o `INSERT` é protegido pela constraint GiST; o log de auditoria entra na mesma transação.
3. Depois do commit: `SLOT_UNLOCKED` (motivo `saved`) e `BOOKING_CREATED` para todas as conexões,
   de todos os processos (via Redis). Fechar o modal, desconectar ou deixar a trava vencer também a
   libera (`released`, `disconnected`, `expired`).

Tecnologias: Python 3.13, FastAPI, SQLAlchemy 2 (async) + asyncpg, Alembic, Pydantic 2, PyJWT,
redis-py, openpyxl · React 19, TypeScript, Vite, Tailwind CSS 4, zustand, lucide-react ·
PostgreSQL 16 (`btree_gist`) · Redis 7.

## 3. Início rápido no Windows (desenvolvimento)

Pré-requisitos: Windows 10/11, **Python 3.13** (3.12 ou mais novo), **Node.js 24.15 ou mais novo**
(a linha 22 também serve a partir da 22.22.2 — é o mínimo das dependências travadas no
`package-lock.json`, como o jsdom e o react-router) e acesso à internet (direto ou pelo proxy da
empresa nas variáveis `HTTP_PROXY`/`HTTPS_PROXY`).
Docker não é necessário: o PostgreSQL de desenvolvimento é portátil e baixado pelo próprio script.

```powershell
.\scripts\dev.ps1 -Seed
```

O comando:

1. cria `backend\.venv` e instala `backend\requirements-dev.txt` nas versões travadas em
   `backend\constraints.txt`, se ainda não existir;
2. baixa (na 1ª vez) e inicia o PostgreSQL 16 portátil em `127.0.0.1:5433` com o banco `agenda`
   (`scripts\dev-postgres.ps1`; os dados ficam em `%LOCALAPPDATA%\agenda-salas-dev`);
3. copia `backend\.env.example` para `backend\.env`, se ainda não existir;
4. instala as dependências do frontend (`npm install`) se faltar `frontend\node_modules`;
5. aplica as migrations (`init-db`) e, com `-Seed`, cria salas, usuárias e reservas de exemplo;
6. abre uma janela para o backend (uvicorn com recarga automática) e outra para o frontend (Vite);
7. espera os dois responderem e mostra os endereços.

Acesse **http://127.0.0.1:5173** e entre com uma das credenciais de **demonstração** (somente
desenvolvimento): `admin` / `admin123` (administração), `recepcao1` / `recepcao123` ou
`recepcao2` / `recepcao123` (recepção). Para ver o tempo real, abra duas janelas do navegador com
usuárias diferentes.

| Parâmetro | Padrão | Para quê |
| --- | --- | --- |
| `-Seed` | – | roda `seed-demo` (10 salas, 3 usuários e reservas na semana atual; só com `AGENDA_ENV=development`) |
| `-BackendPort` / `-FrontendPort` | `8000` / `5173` | portas do uvicorn e do Vite (o Vite repassa `/api` e `/ws` para o backend) |
| `-DbName` / `-PgPort` | `agenda` / `5433` | banco e porta do PostgreSQL; quando informados, têm prioridade sobre o `backend\.env` |
| `-Background` | – | processos ocultos, com log em `.devtools\logs\backend-<porta>.log` e `frontend-<porta>.log` |
| `-NoFrontend` | – | só o backend |
| `-NoPostgres` | – | não mexe no PostgreSQL portátil (use se o `backend\.env` aponta para outro servidor) |
| `-Install` | – | reinstala as dependências (pip e npm) |
| `-TimeoutSeconds` | `120` | espera máxima para os serviços responderem |
| `-Status` | – | mostra o que está rodando |
| `-Stop` | – | encerra os processos iniciados pelo script (todas as instâncias, ou só a de `-BackendPort`); o PostgreSQL continua ativo |

Exemplos:

```powershell
.\scripts\dev.ps1                         # sobe tudo (sem dados de exemplo)
.\scripts\dev.ps1 -Background -Seed       # sem janelas, com logs em .devtools\logs
.\scripts\dev.ps1 -Status
.\scripts\dev.ps1 -Stop
.\scripts\dev-postgres.ps1 -Stop          # para o PostgreSQL portátil
```

> Se o Windows recusar o script ("a execução de scripts foi desabilitada neste sistema"), rode
> `powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 -Seed` ou, uma vez,
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Arquivos baixados como .zip podem precisar
> de `Get-ChildItem -Recurse scripts | Unblock-File`.

### Passo a passo manual

O mesmo que o `dev.ps1` faz, em dois terminais:

```powershell
# terminal 1 — banco e backend
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt -c constraints.txt
..\scripts\dev-postgres.ps1                      # PostgreSQL portátil em 127.0.0.1:5433 + banco "agenda"
Copy-Item .env.example .env                      # ajuste se quiser (ver seção 5)
.\.venv\Scripts\python -m app.cli init-db        # migrations + admin inicial (admin/admin123 do .env.example)
.\.venv\Scripts\python -m app.cli seed-demo      # opcional: dados de demonstração
.\.venv\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --port 8000

# terminal 2 — frontend
cd frontend
npm install --fetch-timeout=120000 --fetch-retries=5
npm run dev                                      # http://127.0.0.1:5173
```

O virtualenv vem antes do `dev-postgres.ps1` porque ele usa o Python de `backend\.venv` para criar o
banco. Com o backend rodando, a documentação interativa da API fica em http://127.0.0.1:8000/docs.

Para testar o build de produção sem o Vite: `cd frontend; npm run build` e suba o backend com
`$env:AGENDA_FRONTEND_DIST = "$PWD\dist"` (ou defina `AGENDA_FRONTEND_DIST` no `backend\.env`):
a SPA passa a ser servida pelo próprio backend em http://127.0.0.1:8000.

## 4. Produção com Docker Compose

O `Dockerfile` gera **uma imagem só**: compila o frontend (Node 24) e o coloca dentro da imagem
Python 3.13 junto com o backend, que serve a API, o WebSocket e a SPA na **porta 8000**. O
`docker-compose.yml` sobe três serviços:

| Serviço | Imagem | Papel |
| --- | --- | --- |
| `db` | `postgres:16-alpine` | dados no volume `pgdata`; healthcheck `pg_isready`; não publica porta no host |
| `redis` | `redis:7-alpine` | travas, presença e eventos entre processos; sem persistência (tudo é efêmero) |
| `app` | build do `Dockerfile` | sobe só com `db` e `redis` saudáveis; aplica as migrations ao iniciar; usuário não-root; healthcheck em `/api/health`; `restart: unless-stopped` |

### Primeira subida

```bash
cp .env.docker.example .env          # PowerShell: Copy-Item .env.docker.example .env
# edite o .env (o modelo não traz nenhum segredo preenchido). Para gerar valores:
python -c "import secrets; print(secrets.token_urlsafe(32))"   # ou: openssl rand -hex 32

docker compose up -d --build
docker compose ps                    # "app" deve aparecer como (healthy)
docker compose logs -f app
```

O que preencher no `.env`:

| Variável | Regra |
| --- | --- |
| `POSTGRES_PASSWORD` | **obrigatória** (vazia, o compose não sobe); só letras, números, `-` e `_` |
| `AGENDA_JWT_SECRET` | **obrigatória**, com **32 caracteres ou mais** (vazia, o compose não sobe; curta, a aplicação não sobe) |
| `AGENDA_BOOTSTRAP_ADMIN_PASSWORD` | senha do administrador inicial — veja abaixo |

**Administrador inicial.** O `init-db` só cria um administrador quando o banco ainda não tem
nenhum usuário. Escolha uma das opções:

- **Senha no `.env`**: preencha `AGENDA_BOOTSTRAP_ADMIN_PASSWORD` com uma senha forte e exclusiva —
  em produção ela é **recusada** se tiver menos de 12 caracteres, for igual ao usuário, for uma
  senha de exemplo ou de demonstração (`admin123`, `senha123`, `123456`, `password`, os valores
  antigos dos modelos…) ou começar com `troque`. Recusada, o container para e o log
  (`docker compose logs app`) diz o motivo. Gere uma com
  `python -c "import secrets; print(secrets.token_urlsafe(16))"`.
- **Senha digitada no terminal** (não fica em arquivo nenhum): deixe
  `AGENDA_BOOTSTRAP_ADMIN_PASSWORD` vazio, suba o sistema (o log avisa que não há usuários) e crie
  o administrador:

  ```bash
  docker compose exec app python -m app.cli create-user --username admin --name "Administrador" --role ADMIN
  ```

Depois do primeiro acesso em `http://servidor:8000`, apague a senha do `.env` (ela não é mais
usada), cadastre as salas e as usuárias da recepção. Para a virada da planilha, siga o
[runbook de operação](docs/OPERACAO.md).

Atrás de proxy corporativo, o build precisa dele para o `npm ci` e o `pip install`:

```bash
docker compose build --build-arg HTTP_PROXY=http://proxy:8080 --build-arg HTTPS_PROXY=http://proxy:8080
docker compose up -d
```

### Variáveis do Compose e do entrypoint

Ficam no `.env` da raiz (modelo comentado em [.env.docker.example](.env.docker.example)). As de
prefixo `AGENDA_` são repassadas à aplicação (seção 5); `AGENDA_DATABASE_URL`, `AGENDA_REDIS_URL`,
`AGENDA_ENV=production` e `AGENDA_FRONTEND_DIST` são definidas pelo compose/Dockerfile, e a imagem
já vem com `AGENDA_CORS_ORIGINS=[]` (a SPA é servida na mesma origem).

| Variável | Padrão | Significado |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | **obrigatória** | senha do usuário `agenda` do PostgreSQL (só letras, números, `-` e `_`, pois entra na URL). Vale na criação do volume: depois, trocar aqui não muda a senha gravada no banco (e o `app` passa a reiniciar sem parar: OPERACAO, seção 4). |
| `AGENDA_JWT_SECRET` | **obrigatória** | segredo dos tokens de login (32+ caracteres aleatórios) |
| `HTTP_PORT` | `8000` | porta publicada no host |
| `HTTP_BIND` | `0.0.0.0` | interface do host; use `127.0.0.1` com proxy reverso na mesma máquina |
| `WEB_CONCURRENCY` | `2` | processos do uvicorn. Mais de 1 exige Redis: sem `AGENDA_REDIS_URL` o entrypoint usa 1 processo e avisa no log |
| `UVICORN_ACCESS_LOG` | `0` | `1` liga o log de cada requisição (o token do WebSocket é mascarado) |
| `FORWARDED_ALLOW_IPS` | `*` | IPs autorizados a enviar `X-Forwarded-For/Proto`; `*` só é seguro se a porta 8000 não estiver acessível pela rede (quem a alcança direto pode forjar o IP e escapar do limite de login por IP) |
| `AGENDA_SKIP_INIT_DB` | `0` | `1` não aplica migrations ao iniciar (réplicas extras; ver seção 8) |

O entrypoint (`scripts/docker-entrypoint.sh`) roda `python -m app.cli init-db` e então
`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers $WEB_CONCURRENCY --proxy-headers
--forwarded-allow-ips=... --no-access-log`. Com argumentos, executa o comando pedido dentro de
`/app/backend`:

```bash
docker compose exec app python -m app.cli create-user --username ana.souza --name "Ana Souza" --role RECEPTIONIST

# arquivos entram e saem do container com "docker compose cp", sempre por /tmp
docker compose exec app python -m app.cli export-xlsx --week -o /tmp/semana.xlsx
docker compose cp app:/tmp/semana.xlsx ./semana.xlsx
docker compose cp "Agenda 2026.xlsx" app:/tmp/agenda.xlsx
docker compose exec app python -m app.cli import-xlsx /tmp/agenda.xlsx          # simulação
```

O processo do container (usuário `agenda`) não pode gravar em `/app/backend`: use `-o /tmp/...`.
Sem `-o`, o `export-xlsx` grava na pasta temporária (`/tmp`) e mostra o caminho em
`Planilha gerada: ...`. Se a importação disser `não foi possível ler o arquivo /tmp/agenda.xlsx:
Permission denied`, ajuste a permissão do arquivo copiado com
`docker compose exec -u root app chmod 644 /tmp/agenda.xlsx`.

Só a imagem, sem o compose (PostgreSQL e Redis já existentes):

```bash
docker build -t agenda-salas .
docker run -d --name agenda -p 8000:8000 \
  -e AGENDA_DATABASE_URL=postgresql+asyncpg://agenda:SENHA@db.interno:5432/agenda \
  -e AGENDA_REDIS_URL=redis://redis.interno:6379/0 \
  -e AGENDA_JWT_SECRET=... \
  -e AGENDA_BOOTSTRAP_ADMIN_USERNAME=admin -e AGENDA_BOOTSTRAP_ADMIN_PASSWORD=... \
  agenda-salas
```

Troque cada `...` pelo valor real: o segredo com 32+ caracteres aleatórios e a senha inicial com as
regras acima (ou omita a senha e use `docker exec -it agenda python -m app.cli create-user ...`).

O banco precisa da extensão `btree_gist` (vem no PostgreSQL oficial; a migration a cria com
`CREATE EXTENSION IF NOT EXISTS`, o que exige ser dono do banco).

### Servidor Windows sem Docker

Também funciona: PostgreSQL 16 oficial (instalador do postgresql.org), Python 3.13 e o build do
frontend (`npm ci; npm run build`). Instale o backend com
`.\.venv\Scripts\python -m pip install -r requirements.txt -c constraints.txt`. Configure
`backend\.env` com `AGENDA_ENV=production`, `AGENDA_DATABASE_URL`, `AGENDA_JWT_SECRET` (32+
caracteres), `AGENDA_FRONTEND_DIST=<caminho>\frontend\dist` e `AGENDA_CORS_ORIGINS=[]` (nunca a
partir do `backend\.env.example`, que é de desenvolvimento), rode
`.\.venv\Scripts\python -m app.cli init-db` e registre como serviço
do Windows (por exemplo com NSSM ou o Agendador de Tarefas, "ao iniciar o sistema") o comando
`.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
--no-access-log`, com o diretório de trabalho em `backend\`, atrás do IIS (seção 7). Sem Redis
(não há Redis oficial para Windows; o Memurai é compatível), use **um** processo.

## 5. Configuração

A aplicação lê variáveis de ambiente com prefixo `AGENDA_` e, em seguida, o arquivo `.env` do
diretório de trabalho (em desenvolvimento, `backend\.env`; modelo em
[backend/.env.example](backend/.env.example)). Variáveis de ambiente têm prioridade sobre o arquivo.
Listas usam JSON (`[0,1,2,3,4]`), horários `HH:MM`. Fonte: `backend/app/config.py`.

| Variável | Padrão | Significado |
| --- | --- | --- |
| `AGENDA_ENV` | `development` | `development`, `production` ou `test`. Em `production` a aplicação recusa o `AGENDA_JWT_SECRET` padrão ou com menos de 32 caracteres, o `init-db` recusa senha fraca para o administrador inicial, o `seed-demo` fica bloqueado e o log avisa sobre configurações arriscadas (sem Redis, CORS com origens locais). |
| `AGENDA_DATABASE_URL` | `postgresql+asyncpg://postgres@127.0.0.1:5433/agenda` | conexão com o PostgreSQL (driver `asyncpg`). Cada processo abre até 30 conexões (pool 10 + 20). |
| `AGENDA_REDIS_URL` | *(vazio)* | Redis para travas, presença e eventos entre processos (`redis://host:6379/0`, ou `rediss://` com TLS). Vazio = memória do processo: **somente 1 processo/instância**. Configurado e inacessível = a aplicação não sobe. |
| `AGENDA_JWT_SECRET` | `dev-only-insecure-secret-change-me-…` | segredo HS256 dos tokens. **Obrigatório trocar em produção**, com 32 caracteres ou mais. Trocar derruba todas as sessões. |
| `AGENDA_JWT_EXPIRE_MINUTES` | `720` | duração da sessão (12 h = um turno); depois disso a pessoa entra de novo. Trocar a senha de alguém (ou desativá-lo) encerra as sessões dessa pessoa na hora. |
| `AGENDA_PASSWORD_HASH_ITERATIONS` | `310000` | iterações do PBKDF2-SHA256 das senhas (só reduza em testes) |
| `AGENDA_LOGIN_MAX_FAILURES` | `5` | falhas de login seguidas (por usuário e por IP) que bloqueiam novas tentativas |
| `AGENDA_LOGIN_FAILURE_WINDOW_SECONDS` | `900` | intervalo máximo entre uma falha e a seguinte para contarem como "seguidas" (15 min) |
| `AGENDA_LOGIN_BLOCK_SECONDS` | `[60,300,900]` | duração dos bloqueios sucessivos (1, 5 e 15 min; o último se repete). Com Redis, o estado fica em `agenda:login:user:<login>` e `agenda:login:ip:<ip>` e vale para todos os processos |
| `AGENDA_HEALTH_DB_TIMEOUT_SECONDS` | `3` | prazo do teste do banco em `GET /api/health`; estourou = 503 |
| `AGENDA_TIMEZONE` | `America/Sao_Paulo` | fuso das datas e horários da grade |
| `AGENDA_DAY_START` | `08:00` | abertura da recepção (início da grade e do Dia Inteiro/Manhã) |
| `AGENDA_DAY_END` | `19:00` | fechamento (fim da grade e do Dia Inteiro/Tarde) |
| `AGENDA_MORNING_END` | `12:00` | fim do período "Manhã Toda" |
| `AGENDA_AFTERNOON_START` | `13:00` | início do período "Tarde Toda" |
| `AGENDA_SLOT_MINUTES` | `30` | altura de cada linha da grade, em minutos; precisa dividir 60, e os quatro horários acima precisam estar alinhados a ela |
| `AGENDA_WORK_DAYS` | `[0,1,2,3,4]` | dias com aba fixa (0 = segunda … 6 = domingo); os demais abrem pelo calendário |
| `AGENDA_LOCK_TTL_SECONDS` | `120` | validade da trava "Reservando: Fulana" sem renovação (o modal aberto renova a cada ⅓ desse tempo, entre 5 e 45 s: 40 s com 120) |
| `AGENDA_TICKET_URL_TEMPLATE` | *(vazio)* | link do sistema de chamados; `{ticket}` vira o número (ex.: `https://chamados.empresa.com.br/ticket/{ticket}`). Vazio = número sem link. |
| `AGENDA_CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | origens de outros domínios autorizadas a chamar a API. A SPA servida pelo backend (produção) e o Vite (desenvolvimento) usam a mesma origem e não precisam disso; em produção use `[]`. |
| `AGENDA_FRONTEND_DIST` | *(vazio)* | pasta do build do frontend; definida, o backend serve a SPA em `/` (na imagem Docker: `/app/frontend/dist`) |
| `AGENDA_BOOTSTRAP_ADMIN_USERNAME` | *(vazio)* | login do administrador criado pelo `init-db` quando o banco não tem nenhum usuário |
| `AGENDA_BOOTSTRAP_ADMIN_PASSWORD` | *(vazio)* | senha desse administrador. Em `production`: 12+ caracteres, diferente do usuário, não pode ser senha de exemplo/demonstração nem começar com `troque` (seção 4); vazio = nenhum administrador é criado (use `create-user`) |
| `AGENDA_BOOTSTRAP_ADMIN_NAME` | `Administrador` | nome exibido desse administrador |
| `AGENDA_INSTANCE_ID` | *(aleatório)* | identificação do processo nos logs e no `HELLO` do WebSocket |

Outras variáveis: `AGENDA_TEST_DATABASE_URL` (banco dos testes do backend), `AGENDA_BACKEND_URL`
(destino do proxy do Vite em desenvolvimento, padrão `http://127.0.0.1:8000`) e `E2E_*` (testes
ponta a ponta, ver `frontend/playwright.config.ts`).

## 6. Operação

O dia a dia da administração e a virada da planilha estão em [docs/OPERACAO.md](docs/OPERACAO.md).
Resumo dos comandos (de dentro de `backend\` com `.\.venv\Scripts\python -m app.cli`, ou no Docker
com `docker compose exec app python -m app.cli`):

| Comando | O que faz |
| --- | --- |
| `init-db` | aplica as migrations (idempotente) e cria o administrador inicial se não houver usuários (seção 4) |
| `create-user --username ana.souza --name "Ana Souza" --role RECEPTIONIST` | cria um usuário (`ADMIN` ou `RECEPTIONIST`); sem `--password`, a senha é pedida no terminal |
| `reset-password --username ana.souza` | define uma nova senha (pedida no terminal, ou `--password`) e **encerra as sessões abertas** dessa pessoa |
| `import-xlsx planilha.xlsx [--commit] [--include-past] [--user admin]` | importa a planilha legada; **sem `--commit` só simula** |
| `export-xlsx [--date 2026-10-26] [--week] [-o arquivo.xlsx]` | exporta o dia (ou a semana) para Excel. Sem `-o`: `agenda-AAAA-MM-DD.xlsx` na pasta atual (ou na temporária, se a atual não aceitar gravação). **No Docker, use `-o /tmp/arquivo.xlsx`** e depois `docker compose cp app:/tmp/arquivo.xlsx .` |
| `seed-demo` | dados de demonstração (somente `AGENDA_ENV=development`; no fim de semana, na semana seguinte) |

**Usuários.** Crie e edite em *Administração → Usuários* (nome, papel, nova senha, ativar/desativar;
usuários não são apagados, para preservar o histórico). Trocar a senha de alguém ou desativá-lo
encerra as sessões abertas dessa pessoa (inclusive a sua, se trocar a própria senha: entre de novo).
Se o único administrador perder a senha: `reset-password --username admin` pela linha de comando
(ou `create-user --role ADMIN` para criar outro).

**Backup do banco (Docker).** O formato `-Fc` (custom) é compacto e restaurável com `pg_restore`:

```bash
docker compose exec -T db pg_dump -U agenda -d agenda -Fc -f /tmp/agenda.dump
docker compose cp db:/tmp/agenda.dump ./agenda-2026-10-26.dump
```

Agende diariamente (cron no Linux, Agendador de Tarefas no Windows) e guarde cópias fora do
servidor, com acesso restrito (os backups têm nomes de pessoas). Redis não precisa de backup:
guarda só travas que vencem em 2 minutos e contadores de tentativas de login.

**Teste de restauração (não afeta produção).** Faça uma vez antes da virada e depois todo mês:
restaure o backup num banco **separado**, confira e apague. O sistema continua no ar.

```bash
docker compose cp ./agenda-2026-10-26.dump db:/tmp/agenda.dump
docker compose exec -T db dropdb -U agenda --if-exists agenda_restore_test
docker compose exec -T db createdb -U agenda agenda_restore_test
docker compose exec -T db pg_restore -U agenda -d agenda_restore_test --no-owner --single-transaction /tmp/agenda.dump
docker compose exec -T db psql -U agenda -d agenda_restore_test -c "select version_num from alembic_version" -c "select count(*) as reservas, max(updated_at) as ultima_alteracao from bookings"
docker compose exec -T db dropdb -U agenda agenda_restore_test
```

O teste passou se o `pg_restore` terminou sem erro, a versão (`version_num`) é a esperada e o
número de reservas e a data da última alteração batem com o dia do backup.

**Recuperação de desastre / retorno de versão** (sobrescreve o banco de produção — perde tudo o
que foi gravado depois do backup). O banco é **recriado vazio** antes da restauração: restaurar
por cima (`pg_restore --clean`) deixaria para trás tabelas e colunas de migrations mais novas e
misturaria dados antigos e novos.

```bash
# 1. guarde o estado atual, mesmo que esteja ruim (dá para voltar atrás; se o banco nem
#    responder e o pg_dump falhar, siga em frente)
docker compose exec -T db pg_dump -U agenda -d agenda -Fc -f /tmp/agenda-atual.dump
docker compose cp db:/tmp/agenda-atual.dump ./agenda-antes-da-restauracao.dump
# 2. pare a aplicação e recrie o banco vazio
docker compose stop app
docker compose exec -T db dropdb -U agenda --force agenda
docker compose exec -T db createdb -U agenda agenda
# 3. restaure (tudo ou nada: com --single-transaction, qualquer erro desfaz a restauração inteira)
docker compose cp ./agenda-2026-10-26.dump db:/tmp/agenda.dump
docker compose exec -T db pg_restore -U agenda -d agenda --no-owner --single-transaction /tmp/agenda.dump
# 4. confira a versão do banco e suba a aplicação
docker compose exec -T db psql -U agenda -d agenda -c "select version_num from alembic_version"
docker compose start app
```

Se o passo 3 falhar, o banco fica vazio e nada foi misturado: corrija a causa (arquivo errado,
disco cheio) e repita os passos 2 e 3. Para voltar a **versão** do sistema junto, veja a seção 5.2
do [runbook](docs/OPERACAO.md). Sem Docker, use o `pg_dump`/`dropdb`/`createdb`/`pg_restore`/`psql`
da instalação do PostgreSQL com as mesmas opções (`-h servidor -U usuário`).

**Atualização de versão e migrations.** Faça backup, **guarde a imagem em uso** com uma etiqueta
própria (o build sobrescreve `agenda-salas:latest`) e então atualize:

```bash
docker compose exec -T db pg_dump -U agenda -d agenda -Fc -f /tmp/agenda.dump
docker compose cp db:/tmp/agenda.dump ./agenda-antes-da-atualizacao.dump
docker tag agenda-salas:latest agenda-salas:anterior
# atualize o código da pasta (git pull / nova versão) e então:
docker compose build --pull
docker compose up -d                 # o entrypoint aplica as migrations novas antes de subir
docker compose logs app | grep -i -e migration -e "banco de dados"
```

As bibliotecas Python são instaladas nas versões exatas de `backend/constraints.txt`, então
reconstruir uma versão antiga do código traz de volta as mesmas bibliotecas. Fora do Docker:
atualize o código, `pip install -r requirements.txt -c constraints.txt`, `npm ci; npm run build`,
`python -m app.cli init-db` e reinicie o serviço. Migrations ficam em `backend/alembic/versions`
(Alembic): `python -m alembic current` (no Docker: `docker compose exec app python -m alembic
current`) mostra a versão do banco, por exemplo `0002_token_version_import_key (head)`; para criar
uma nova durante o desenvolvimento, `python -m alembic revision --autogenerate -m "descrição"`
(revise o arquivo gerado; nunca altere uma migration já publicada).

**Logs.** `docker compose logs -f app` (arquivos rotacionados: 5 × 10 MB por serviço). As mensagens
da aplicação (`app.*`) saem no nível do uvicorn (`--log-level`, padrão INFO), no formato
`2026-10-26 08:00:00,123 INFO app.realtime.setup: Tempo real iniciado (backend redis, instância
3f2a9c1d)`. O log de cada requisição vem desligado (`UVICORN_ACCESS_LOG=1` liga); o token do
WebSocket (`/ws?token=`) é sempre mascarado como `***` pela aplicação. Em desenvolvimento, os logs
ficam nas janelas abertas pelo `dev.ps1` (ou em `.devtools\logs` com `-Background`).

**Saúde.** `GET /api/health` é o que o healthcheck do container e um monitor externo devem
consultar:

| Situação | HTTP | Corpo |
| --- | --- | --- |
| tudo certo | 200 | `{"status":"ok","database":true,"realtime":"redis","redis":"ok"}` (sem Redis: `"realtime":"memory","redis":"disabled"`) |
| Redis fora do ar | 200 | `{"status":"degraded","database":true,"realtime":"redis","redis":"down"}` — o REST funciona; o tempo real volta sozinho |
| banco fora do ar | 503 | `{"status":"degraded","database":false,...}` — o container fica *unhealthy* |

Alerte em 503 e também em `"status":"degraded"`. O indicador no topo da grade mostra a cada
recepcionista se o tempo real está conectado.

## 7. Proxy reverso e WebSocket

Em produção, publique a aplicação por HTTPS através de um proxy reverso. O WebSocket (`/ws`)
precisa dos cabeçalhos de *upgrade*; a SPA monta `wss://` sozinha quando a página está em HTTPS.

**nginx:**

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    http2 on;
    server_name agenda.empresa.com.br;
    ssl_certificate     /etc/ssl/agenda.crt;
    ssl_certificate_key /etc/ssl/agenda.key;

    client_max_body_size 12m;            # importação de planilha (a aplicação recusa > 10 MB com 413)

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1h;           # o navegador manda PING a cada 25 s
        proxy_send_timeout 1h;
        access_log off;                  # a URL leva o token de acesso (?token=)
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name agenda.empresa.com.br;
    return 301 https://$host$request_uri;
}
```

Com o proxy na mesma máquina, use `HTTP_BIND=127.0.0.1` no `.env` e, se quiser restringir,
`FORWARDED_ALLOW_IPS` com o IP de onde o proxy conecta. O proxy precisa repassar o
`X-Forwarded-For`: é dele que sai o IP usado no limite de tentativas de login por IP.

A aplicação já envia `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` e, na página
da SPA, a `Content-Security-Policy` (seção 9): não é preciso repeti-los no proxy, e um CSP próprio
do proxy precisa liberar `connect-src 'self' ws: wss:` para o WebSocket. HSTS
(`add_header Strict-Transport-Security "max-age=31536000" always;`) é do proxy, se desejado.

**IIS (Application Request Routing):** instale o recurso **Protocolo WebSocket** (Gerenciador do
Servidor → Servidor Web → Desenvolvimento de Aplicativos), o **URL Rewrite** e o **ARR 3.0**; em
*ARR → Server Proxy Settings* marque *Enable proxy* e *Preserve host header*. Crie uma regra de
reescrita `(.*)` → `http://127.0.0.1:8000/{R:1}` e, em *View Server Variables*, autorize
`HTTP_X_FORWARDED_PROTO` com o valor `https` na regra. O ARR repassa o *upgrade* do `/ws`
automaticamente quando o Protocolo WebSocket está instalado; o tempo limite padrão (120 s) é
suficiente por causa do PING de 25 s. O log do IIS grava a *query string*: desmarque o campo
**URI Query** (*Logging → Select Fields*) para não registrar o token do WebSocket.

## 8. Escala

- **Um processo, sem Redis**: travas e eventos ficam na memória — suficiente para uma recepção
  inteira, mas é o limite (`AGENDA_REDIS_URL` vazio ⇒ `WEB_CONCURRENCY=1`).
- **Mais processos ou mais servidores exigem Redis** (`AGENDA_REDIS_URL`): as travas são chaves com
  TTL no Redis, os eventos atravessam processos pelo canal `agenda:events` e a presença ("quem está
  online") usa heartbeat com TTL, sem "fantasmas" quando um processo cai. O compose já faz isso
  (`WEB_CONCURRENCY=2`). Não é preciso *sticky session*: qualquer processo atende qualquer conexão.
- **Várias réplicas** atrás de um balanceador (que repasse WebSocket): aponte todas para o mesmo
  PostgreSQL e Redis e deixe só uma aplicar as migrations (`AGENDA_SKIP_INIT_DB=1` nas demais).
- **Conexões com o banco**: cada processo usa até 30 conexões (pool 10 + 20). Mantenha
  `processos × 30` abaixo do `max_connections` do PostgreSQL (padrão 100).
- O tempo real (< 200 ms, RNF01) depende de Redis e PostgreSQL na mesma rede de baixa latência
  que a aplicação.

## 9. Segurança

- **Troque o `AGENDA_JWT_SECRET`** (32+ caracteres aleatórios). Em `production` a aplicação não sobe
  com o valor padrão nem com menos de 32 caracteres, e o compose não sobe sem ele.
- **Credenciais de demonstração são só para desenvolvimento**: `admin/admin123`,
  `recepcao1/recepcao123` e `recepcao2/recepcao123` vêm do `seed-demo` e do `backend/.env.example`.
  O `seed-demo` é bloqueado fora de `AGENDA_ENV=development`; não use o `backend/.env.example` em
  produção. Os modelos públicos (`.env.docker.example`) não trazem senha nenhuma, e em produção o
  `init-db` recusa para o administrador inicial senha curta (menos de 12), igual ao usuário, de
  exemplo/demonstração ou começando com `troque` (seção 4).
- **Força bruta no login**: 5 falhas seguidas do mesmo usuário ou do mesmo IP bloqueiam por 1 min,
  depois 5 e 15 min (HTTP 429, `AGENDA_LOGIN_*`, seção 5). O limite por IP depende do IP real do
  cliente: com `FORWARDED_ALLOW_IPS=*`, a porta 8000 só pode ser alcançada pelo proxy.
- **Sessões revogáveis**: o token leva a versão da senha da pessoa; trocar a senha (tela ou
  `reset-password`) ou desativar a conta invalida na hora todos os tokens anteriores (REST) e
  derruba o tempo real em até 1 minuto.
- **Cabeçalhos HTTP**: toda resposta leva `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY` e `Referrer-Policy: strict-origin-when-cross-origin`; a página da SPA
  leva uma `Content-Security-Policy` que só permite scripts, estilos e conexões da própria origem
  (detalhes em [docs/CONTRATO.md](docs/CONTRATO.md), seção 5.7).
- **Uploads e corpo das requisições**: o envio da planilha só é lido depois de conferir o token de
  administrador e o tamanho (até 10 MB; 401/403/413 antes de gravar qualquer byte em disco); as
  demais requisições aceitam até 1 MB.
- **HTTPS**: o token de acesso fica no navegador e vai no cabeçalho `Authorization` e na URL do
  WebSocket; fora de uma rede totalmente confiável, publique só por HTTPS (seção 7).
- **Logs**: a aplicação mascara `?token=` nos logs; configure o proxy para não registrar a URL do
  `/ws` (seção 7).
- **Rede**: o compose não publica PostgreSQL nem Redis no host. Exponha só o proxy reverso
  (`HTTP_BIND=127.0.0.1`) e limite `FORWARDED_ALLOW_IPS` quando a porta 8000 for alcançável por
  outras máquinas. Redis fora do compose: use senha/TLS na URL (`rediss://:senha@host:6380/0`).
- **Container**: roda como usuário sem privilégios (`agenda`, uid 10001) com `no-new-privileges`;
  segredos ficam no `.env`, que não entra na imagem (`.dockerignore`) nem no controle de versão.
- **Senhas** com PBKDF2-SHA256 (310 mil iterações) e sal aleatório; sessões expiram em 12 h;
  papéis `ADMIN` (cadastros, importação) e `RECEPTIONIST` (grade, manutenção de vídeo, auditoria).
- **Backups** contêm dados pessoais (nomes de solicitantes): guarde-os com acesso restrito.

## 10. Testes

| O quê | Comando |
| --- | --- |
| Tudo (backend em grupos paralelos + frontend) | `.\scripts\test.ps1` |
| + ponta a ponta | `.\scripts\test.ps1 -E2E` |
| Só alguns arquivos do backend | `.\scripts\test.ps1 -Tests test_rooms.py, 'test_excel_*' -SkipFrontend` |
| Backend direto | `cd backend; .\.venv\Scripts\python -m pytest -q` |
| Frontend (unitários e componentes) | `cd frontend; npm test -- --run` |
| Tipos e lint do frontend | `cd frontend; npm run typecheck; npm run lint` |
| Ponta a ponta (Playwright + Microsoft Edge) | `cd frontend; npm run e2e` |

**Backend** (pytest): cada execução recria o schema do banco `AGENDA_TEST_DATABASE_URL` (padrão
`agenda_test` em `127.0.0.1:5433`, criado se não existir) — execuções simultâneas precisam de
bancos diferentes. No Windows cada conexão nova com o PostgreSQL custa ~200 ms, então a suíte
completa em sequência leva de 15 a 20 minutos; o `test.ps1` divide os arquivos em grupos
(`-Groups 4`, bancos `agenda_test_g1`…`agenda_test_g4`), equilibrados pelo tempo da última
execução, e roda o Vitest ao mesmo tempo. Logs e relatórios JUnit ficam em
`.devtools\test-results`; o script termina com código 1 se algo falhar. Opções: `-Groups`,
`-DbPrefix`, `-Tests`, `-PytestArgs`, `-SkipBackend`, `-SkipFrontend`, `-E2E`, `-E2EPort`,
`-E2EDbName`, `-TimeoutMinutes`.

**Ponta a ponta**: `npm run e2e` compila o frontend e roda o Playwright no **Microsoft Edge
instalado** (`channel: msedge`, não baixa navegadores); o próprio Playwright sobe o backend na
porta `8100` com o banco `agenda_e2e`, recriado a cada execução (`E2E_PORT`, `E2E_DB_NAME` mudam
isso). `npm run e2e -- --headed` mostra a janela; o relatório fica em
`frontend/playwright-report` (`npx playwright show-report`).

## 11. Estrutura do projeto

```
.
├── backend/
│   ├── app/
│   │   ├── api/            rotas REST (auth, users, rooms, bookings, availability, audit, config, health, excel)
│   │   ├── services/       regras de negócio, conflitos, avisos (RF06), auditoria
│   │   ├── realtime/       WebSocket /ws, travas (memória/Redis), eventos, presença, pub/sub
│   │   ├── excel/          importação da planilha legada e exportação .xlsx
│   │   ├── config.py       configuração AGENDA_* · db.py · models.py · schemas.py · security.py
│   │   ├── middleware.py   cabeçalhos de segurança/CSP e guarda do upload e do tamanho do corpo
│   │   ├── cli.py          python -m app.cli (init-db, create-user, reset-password, seed-demo, import/export)
│   │   └── main.py         aplicação FastAPI (+ SPA em "/")
│   ├── alembic/            migrations (0001_initial: tabelas + constraint GiST; 0002: token_version, import_key)
│   ├── tests/              pytest (API, concorrência, tempo real, Excel, CLI)
│   ├── requirements.txt    dependências de produção (requirements-dev.txt: + testes)
│   ├── constraints.txt     versões exatas testadas (pip install -r ... -c constraints.txt)
│   └── .env.example        modelo de configuração de desenvolvimento
├── frontend/
│   ├── src/                SPA React (api/, realtime/, store/, features/grid|admin|auth, components/ui)
│   ├── e2e/                testes ponta a ponta (Playwright)
│   └── package.json        dev, build, test, lint, e2e
├── docs/                   planejamento, contrato, importação, operação, manual da recepção
├── scripts/
│   ├── dev.ps1             ambiente de desenvolvimento com um comando
│   ├── test.ps1            todos os testes em paralelo
│   ├── dev-postgres.ps1    PostgreSQL 16 portátil para desenvolvimento
│   └── docker-entrypoint.sh
├── Dockerfile              imagem única (build do frontend + backend)
├── docker-compose.yml      produção: db + redis + app
└── .env.docker.example     modelo do .env de produção
```
