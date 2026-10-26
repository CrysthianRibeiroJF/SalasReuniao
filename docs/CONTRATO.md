# Contrato Técnico — Agenda de Salas em Tempo Real

Este documento é a referência obrigatória para quem implementa qualquer parte do sistema.
O planejamento funcional original está em `docs/PLANEJAMENTO.md` (requisitos RF01–RF08, RNF01–RNF03,
fluxos 5.1–5.3, UX seção 6, DoD seção 7, transição seção 8). Este contrato **detalha e fecha** as
decisões em aberto e descreve o sistema **como está implementado** (inclusive as decisões D1–D10 da
revisão de segurança e operação, resumidas na seção 11). Em caso de conflito, vale este contrato.

Idioma: **código, identificadores e nomes de API em inglês; toda mensagem visível ao usuário em
português do Brasil** (inclusive `message` dos erros da API e o *reason* dos fechamentos do
WebSocket).

---

## 1. Visão geral da arquitetura

```
Navegador (React SPA) ──REST /api/*──▶ FastAPI ──▶ PostgreSQL 16 (constraint GiST = hard lock)
          ▲  │                            │
          │  └──WebSocket /ws?token=──────┤──▶ Redis (soft locks, pub/sub entre instâncias,
          └──────── eventos em tempo real ┘      limite de login; sem Redis: memória, 1 instância)
```

- **Hard lock** (RNF02): constraint `bookings_no_overlap` (EXCLUDE USING gist) — criada em
  `backend/alembic/versions/0001_initial.py`. O serviço faz pré-checagem amigável, mas a verdade é
  o banco: `asyncpg.exceptions.ExclusionViolationError` (SQLSTATE `23P01`, embrulhada em
  `sqlalchemy.exc.IntegrityError`) vira HTTP 409 `BOOKING_CONFLICT`.
- **Soft lock** (5.1): seleção de células → `LOCK_SLOT` via WebSocket → chaves Redis
  `lock:room_{id}:{YYYY-MM-DD}:{HH:MM}` (uma por slot de `slot_minutes`) com TTL de
  `lock_ttl_seconds` (120 s). Visível a todos como "Reservando: Fulana".
- **Eventos**: todo commit de escrita publica um evento (`app/realtime/events.py::publish`) que chega
  a todas as conexões de todas as instâncias em < 200 ms (RNF01).
- **Camadas HTTP** (de fora para dentro, `app/main.py` e `app/middleware.py`):
  `SecurityHeadersMiddleware` (cabeçalhos de segurança em toda resposta, até nos 500; CSP no HTML
  da SPA) → `ServerErrorMiddleware` → CORS → `RequestGuardMiddleware` (limite de corpo e guarda do
  upload da planilha antes de ler o corpo) → rotas. Detalhes na seção 5.7.

## 2. Ambiente de desenvolvimento (Windows, sem Docker nesta máquina)

| Item | Valor |
| --- | --- |
| Python | `backend/.venv/Scripts/python.exe` (3.13). Dependências: `backend/requirements-dev.txt`, com as versões travadas em `backend/constraints.txt` (`pip install -r requirements-dev.txt -c constraints.txt`). |
| PostgreSQL 16 | `127.0.0.1:5433`, usuário `postgres`, sem senha (trust). Binários em `C:\Users\crysthianr-jfm\AppData\Local\agenda-salas-dev\pgsql\bin`, dados em `...\agenda-salas-dev\pgdata` (caminho sem acentos é obrigatório — o Postgres falha com "ã" no caminho). Bancos: `agenda` (dev). Não há `psql.exe` nem `pg_dump.exe`; use Python/asyncpg para consultas. Parado: `powershell -File scripts/dev-postgres.ps1`. |
| Redis | não instalado. Dev usa backend em memória (`AGENDA_REDIS_URL` vazio). Testes de Redis usam `fakeredis` (inclui `fakeredis.TcpFakeServer` para teste multi-processo; `tests/realtime_support.py::FakeRedisTcp`). |
| Node | v24 (mínimo dos pacotes travados: `^22.22.2 \|\| ^24.15.0 \|\| >=26.0.0`, igual ao `engines` do `frontend/package.json`) / npm 11. Downloads podem ser lentos (use timeouts longos). |
| Portas | backend `8000`, frontend (Vite) `5173` com proxy de `/api` e `/ws` para `127.0.0.1:8000`. |

**Testes do backend**: `powershell -File scripts/test.ps1` (grupos paralelos; `-Tests`, `-DbPrefix`,
`-SkipFrontend`) ou, direto, `cd backend && .venv/Scripts/python -m pytest -q`.
Cada execução recria o schema do banco apontado por `AGENDA_TEST_DATABASE_URL`
(padrão `agenda_test`). **Execuções simultâneas precisam de bancos diferentes**, ex.:
`AGENDA_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/agenda_test_core`.
O conftest cria o banco se não existir. Os testes usam `NullPool` e cada conexão nova custa
~200 ms nesta máquina: rode subconjuntos enquanto desenvolve. Fixtures em
`backend/tests/conftest.py`: `session`, `make_user`, `admin`, `receptionist`, `receptionist2`,
`auth` (→ headers Bearer), `make_room`, `video_room`, `normal_room`, `app` (create_app + lifespan),
`client` (httpx ASGI). Apoio: `core_support.py`/`core_fixtures.py`, `realtime_support.py`
(`FakeRedisTcp`, `WsClient`, `ServerProcess`), `hardening_support.py`, `excel_builders.py`.

**Testes do frontend**: `cd frontend && npm test -- --run` (Vitest), `npm run lint`,
`npm run build`; ponta a ponta `npm run e2e` (Playwright no Microsoft Edge instalado,
`channel: msedge`; `E2E_PORT` e `E2E_DB_NAME` mudam a porta e o banco do backend dos testes).

## 3. Estrutura de pastas e responsabilidades

```
docs/PLANEJAMENTO.md  docs/CONTRATO.md  docs/IMPORTACAO.md  docs/OPERACAO.md  docs/MANUAL_RECEPCAO.md
backend/
  alembic.ini  alembic/env.py
  alembic/versions/0001_initial.py  0002_token_version_import_key.py   [publicadas — nunca alterar]
  requirements.txt  requirements-dev.txt  constraints.txt (versões exatas testadas)
  app/config.py  app/db.py  app/models.py  app/schemas.py  app/serializers.py  app/timeutil.py
  app/security.py      senhas, JWT (claim "tv"), check_token / AuthFailure
  app/middleware.py    SecurityHeadersMiddleware, RequestGuardMiddleware
  app/logging_config.py  logs "app.*" no nível do uvicorn
  app/main.py  app/cli.py  app/seed.py  app/migrate.py
  app/api/{auth,users,rooms,bookings,availability,audit,config,health,excel}.py
  app/services/{bookings,rooms,users,availability,audit,errors,login_throttle}.py
  app/realtime/{registry,events,setup,runtime,ws,hub,bus,locks,memory,redis_store,presence,
               rooms,accounts,external,codec,log_redaction}.py
  app/excel/{xlsx,reader,parsing,common,importer,exporter,locks}.py
  tests/                                       [cada módulo tem seus test_*.py]
frontend/  (src/api, src/realtime, src/store, src/features/{grid,admin,auth,shell}, e2e/)
scripts/{dev.ps1,test.ps1,dev-postgres.ps1,docker-entrypoint.sh}  Dockerfile  docker-compose.yml
```

Regras de convivência entre módulos:
- Migrations publicadas não mudam: mudança de schema = nova revisão Alembic (`0003_...`).
- Os serviços REST só falam com o tempo real por `app.realtime.registry.get_lock_store()` e
  `app.realtime.events.publish()`. Nunca importe implementações concretas de tempo real nos serviços.
- `tests/conftest.py` é compartilhado: pode **acrescentar** fixtures, nunca mudar as existentes.

## 4. Modelo de dados

Ver `backend/app/models.py` e as migrations (fonte da verdade). Extensões em relação ao planejamento:

- `users`: `username` (único, case-insensitive), `full_name`, `password_hash` (PBKDF2), `role`
  (`ADMIN` | `RECEPTIONIST`), `is_active`, `last_login_at`, **`token_version`** (INTEGER, padrão 0;
  migration 0002) — versão dos tokens de login, ver 5.2 (D3).
- `rooms`: + `resources` (JSONB lista de textos — "recursos fixos" do RF01), `maintenance_since`.
  Propriedade calculada `effective_type`: `VIDEO` somente se `room_type=VIDEO` **e**
  `video_status=OPERATIONAL`; senão `NORMAL` (RF02 — sala em manutenção é tratada como normal).
  CHECK: só sala `VIDEO` pode estar em `MAINTENANCE`.
- `bookings`: + `period_type` (`CUSTOM`|`FULL_DAY`|`MORNING`|`AFTERNOON`), `notes`, `attendees`,
  `version` (concorrência otimista), `cancelled_at/by`, `cancel_reason`, `created_at/updated_at`,
  **`import_key`** (VARCHAR(100), nulo fora da importação; índice parcial
  `ix_bookings_import_key ... WHERE import_key IS NOT NULL`; migration 0002) — marca da célula da
  planilha de origem (D10, seção 8). `ticket_number` é opcional (algumas reuniões internas não têm
  chamado), mas a UI o coloca como primeiro campo. `requester_name` é obrigatório.
- `booking_logs.action`: `CREATED`, `IMPORTED`, `UPDATED`, `MOVED_ROOM`, `RESCHEDULED`,
  `STATUS_CHANGED`, `CANCELLED`. `previous_state`/`new_state` = `serializers.booking_snapshot()`.

Regras de horário (`app/timeutil.py`, configuráveis em `app/config.py`):
- Fuso `America/Sao_Paulo`; funcionamento `08:00–19:00`; slots de 30 min.
- Manhã Toda = `08:00–12:00`; Tarde Toda = `13:00–19:00`; Dia Inteiro = `08:00–19:00` (`is_full_day=true`).
- Reserva precisa estar **dentro do horário de funcionamento** do dia, `end > start`, minutos múltiplos de 5.
  (A importação pode relaxar o horário de funcionamento com aviso.)
- Dias úteis exibidos: segunda a sexta (`work_days`). Sábado/domingo podem ser abertos pelo calendário.

## 5. API REST (prefixo `/api`, JSON, autenticação `Authorization: Bearer <jwt>`)

Schemas exatos em `backend/app/schemas.py`. Erros de negócio: `{"detail": {"code", "message", ...}}`.
Erros de validação do FastAPI (422 padrão) continuam no formato padrão (`detail` = lista).

### 5.0 Códigos de erro comuns

| HTTP | `detail.code` | Quando |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | token ausente, inválido ou vencido — "Sessão expirada ou inválida. Entre novamente." |
| 401 | `SESSION_REVOKED` | token de uma versão anterior (`tv` ≠ `users.token_version`: a senha foi trocada, ou a conta foi desativada e reativada, depois de emitido) — "Sua sessão foi encerrada porque a senha foi alterada. Entre novamente." |
| 401 | `ACCOUNT_DISABLED` | usuário desativado ou removido — "Seu acesso foi desativado. Fale com o administrador." |
| 401 | `INVALID_CREDENTIALS` | só no login: usuário inexistente, senha errada **ou usuário inativo** (não revela qual) |
| 403 | `FORBIDDEN` | rota de administrador — "Apenas administradores podem fazer isso." |
| 404 | `BOOKING_NOT_FOUND`, `ROOM_NOT_FOUND`, `USER_NOT_FOUND` | |
| 409 | `BOOKING_CONFLICT`, `SLOT_LOCKED`, `STALE_VERSION`, `BOOKING_NOT_EDITABLE`, `ROOM_NAME_TAKEN`, `ROOM_HAS_FUTURE_BOOKINGS`, `USERNAME_TAKEN`, `CANNOT_DEMOTE_SELF` | seções 5.2–5.4 |
| 413 | `FILE_TOO_LARGE` / `REQUEST_TOO_LARGE` | corpo grande demais, recusado antes de ser lido (5.7) |
| 422 | `ROOM_INACTIVE`, `OUTSIDE_OPERATING_HOURS`, `INVALID_TIME`, `INVALID_INTERVAL`, `INVALID_DATE_RANGE`, `DATE_RANGE_TOO_LARGE`, `NOT_A_VIDEO_ROOM`, `INVALID_ROOM_NAME`, `INVALID_NAME`, `INVALID_REQUESTER`, `INVALID_FILE_TYPE`, `FILE_TOO_LARGE`, `INVALID_FILE`, `INVALID_INPUT` | regras de negócio; `INVALID_INPUT` = valor recusado pelo PostgreSQL (SQLSTATE classe 22) que passou da validação |
| 429 | `TOO_MANY_ATTEMPTS` | login bloqueado (D4), com `retry_after_seconds` e cabeçalho `Retry-After` |
| 503 | — | só `GET /api/health`, com o banco fora do ar (D5) |

Os 401 de token (`UNAUTHORIZED`, `SESSION_REVOKED`, `ACCOUNT_DISABLED`) levam
`WWW-Authenticate: Bearer`. O frontend mapeia o `code` para a mensagem da tela de login (seção 7).

### 5.1 Autenticação, configuração e saúde
| Método e rota | Acesso | Corpo → Resposta |
| --- | --- | --- |
| `POST /api/auth/login` | público | `LoginIn{username (1–50), password (1–200)}` → `TokenOut{access_token, token_type, expires_at, user}`. Falha: 401 `INVALID_CREDENTIALS`; bloqueado: 429 `TOO_MANY_ATTEMPTS`. Atualiza `last_login_at`. |
| `GET /api/auth/me` | logado | → `UserOut` (o frontend o usa para descobrir o motivo de um fechamento 4401) |
| `GET /api/config` | logado | → `ConfigOut` (horários, slots, presets, `lock_ttl_seconds`, template de link de chamado, `today`, `realtime_backend`) |
| `GET /api/health` | público | → `{"status": "ok"\|"degraded", "database": bool, "realtime": "memory"\|"redis", "redis": "ok"\|"down"\|"disabled"}` |

**Token (JWT HS256)**: claims `sub` (id do usuário), `name`, `role`, `tv` (= `users.token_version`
no momento do login) e `exp` (`AGENDA_JWT_EXPIRE_MINUTES`, padrão 720). Toda requisição confere
assinatura, validade, usuário ativo e `tv` igual à versão atual (`security.check_token`). Token sem
`tv` vale como `tv=0`; `tv` que não é inteiro ou `sub` fora do INTEGER → `UNAUTHORIZED`.
Em produção `AGENDA_JWT_SECRET` precisa ter **32 caracteres ou mais** (senão a aplicação não sobe).

**Limite de tentativas de login (D4)** — `app/services/login_throttle.py`:
- Contadores separados por **usuário** (login normalizado: sem espaços nas pontas, minúsculo) e por
  **IP do cliente** (`request.client.host`; atrás de proxy é o IP já corrigido pelo
  `uvicorn --proxy-headers`, que só confia em `FORWARDED_ALLOW_IPS`).
- `AGENDA_LOGIN_MAX_FAILURES` (5) falhas seguidas, sem intervalo maior que
  `AGENDA_LOGIN_FAILURE_WINDOW_SECONDS` (900 = 15 min) entre uma e a seguinte, bloqueiam por
  60 s; cada falha depois disso bloqueia por 300 s e depois 900 s (`AGENDA_LOGIN_BLOCK_SECONDS`
  = `[60,300,900]`, o último valor se repete).
- Bloqueado (pelo usuário **ou** pelo IP): HTTP 429
  `{"detail": {"code": "TOO_MANY_ATTEMPTS", "message": "Muitas tentativas de entrada sem sucesso. Tente novamente em 1 minuto.", "retry_after_seconds": 60}}`
  e cabeçalho `Retry-After: 60`. A senha nem é conferida e a tentativa não conta como nova falha.
- Login bem-sucedido zera o contador do usuário e encerra a sequência de falhas daquele IP (uma
  recepção atrás de NAT não fica bloqueada por erros de digitação misturados com acertos).
- Com `AGENDA_REDIS_URL`: hashes `agenda:login:user:<login>` e `agenda:login:ip:<ip>` com TTL
  (janela + bloqueio), compartilhados entre instâncias. Sem Redis: memória do processo. Redis fora
  do ar: a memória assume e o Redis é tentado de novo a cada 30 s.

**Saúde (D5)**: `status` é `"ok"` com o banco acessível e o Redis `ok` ou `disabled`; senão
`"degraded"`. HTTP **503 somente com o banco fora do ar** (o `SELECT 1` tem prazo de
`AGENDA_HEALTH_DB_TIMEOUT_SECONDS`, padrão 3 s); Redis fora do ar → **200** com
`"status": "degraded", "redis": "down"` (PING com 2 s de prazo): o REST continua funcionando (o
hard lock é do banco) e o tempo real se recupera sozinho. Exemplos:
`{"status":"ok","database":true,"realtime":"redis","redis":"ok"}` ·
`{"status":"ok","database":true,"realtime":"memory","redis":"disabled"}` ·
`{"status":"degraded","database":true,"realtime":"redis","redis":"down"}` (200) ·
`{"status":"degraded","database":false,...}` (503).

### 5.2 Usuários (admin)
`GET /api/users` → `list[UserOut]` · `POST /api/users` (`UserCreate`) → 201 `UserOut`
(409 `USERNAME_TAKEN`) · `PATCH /api/users/{id}` (`UserUpdate{full_name?, password? (6–200),
role?, is_active?}`) → `UserOut`. Um admin não pode se desativar nem remover o próprio papel de
admin (409 `CANNOT_DEMOTE_SELF`). Usuários não são apagados.

**Revogação de sessões (D3)**: `token_version` é incrementado quando a **senha muda** (`PATCH` com
`password`, ou `python -m app.cli reset-password`) e quando a conta é **desativada**
(`is_active` true→false). Todos os tokens anteriores passam a receber 401 `SESSION_REVOKED` (REST)
e 4401 (WebSocket, em até 60 s — seção 6). Consequências: um admin que troca a própria senha
precisa entrar de novo; uma conta desativada e depois reativada precisa de login novo (o token
antigo recebe `SESSION_REVOKED`, cuja mensagem fixa fala em troca de senha). Para forçar a saída de
alguém, o administrador redefine a senha dessa pessoa.

### 5.3 Salas
| Método e rota | Acesso | Detalhes |
| --- | --- | --- |
| `GET /api/rooms?include_inactive=false` | logado | `list[RoomOut]` ordenado por (`effective_type` VIDEO primeiro, `display_order`, `name`). `include_inactive=true` é permitido a **qualquer usuário logado** (somente leitura): a grade precisa das salas desativadas que ainda têm reservas. |
| `GET /api/rooms/{id}` | logado | `RoomOut` (404 `ROOM_NOT_FOUND`) |
| `POST /api/rooms` | admin | `RoomCreate` → 201 `RoomOut`; 409 `ROOM_NAME_TAKEN`. Publica `ROOM_CREATED`. |
| `PATCH /api/rooms/{id}` | admin | `RoomUpdate` → `RoomOut`. Mudar `room_type` para NORMAL zera manutenção. Publica `ROOM_UPDATED`. Desativar: ver abaixo. |
| `PUT /api/rooms/{id}/maintenance` | logado | `MaintenanceIn{ticket_id}` → `RoomOut`. Só sala `VIDEO` (senão 422 `NOT_A_VIDEO_ROOM`). Marca `MAINTENANCE`, `maintenance_since=now`. Publica `ROOM_UPDATED`. |
| `DELETE /api/rooms/{id}/maintenance` | logado | volta para `OPERATIONAL`, limpa chamado. Publica `ROOM_UPDATED`. |

**Desativação (D1)**: salas não são apagadas (histórico); desativar = `PATCH {"is_active": false}`.
Enquanto a sala tiver reservas **ativas** (`CONFIRMED` ou `CHECKED_IN`) com `end_time > agora`, a
desativação é recusada e **nada muda** (nem `ROOM_UPDATED` é publicado):

```json
{"detail": {"code": "ROOM_HAS_FUTURE_BOOKINGS",
            "message": "A sala tem 3 reservas ativas que ainda não aconteceram. Remaneje ou cancele essas reservas antes de desativar a sala.",
            "count": 3,
            "bookings": [/* BookingOut, no máximo 50, por start_time */]}}
```

Não há opção de forçar: o admin remaneja ou cancela as reservas e salva de novo. O `PATCH` trava a
linha da sala (`FOR UPDATE`) e toda criação/remanejamento para a sala trava a mesma linha
(`FOR SHARE`) até o commit, então uma desativação e uma reserva simultâneas não se cruzam.
Reservas antigas (já terminadas, ou de dados legados) continuam numa sala desativada: a grade
mostra a coluna da sala, com o selo **"Sala desativada"**, **só nos dias em que ela ainda tem
reservas ativas** — assim a grade, a contagem das abas e a busca concordam. Nessa coluna não se
cria reserva nem se solta reserva arrastada; as reservas dela podem ser abertas, editadas
(dados), movidas para uma sala ativa ou canceladas. No servidor: criar, mudar o horário ou mover
para uma sala inativa → 422 `ROOM_INACTIVE`; editar só os dados de uma reserva que está nela é
permitido.

### 5.4 Reservas
| Método e rota | Detalhes |
| --- | --- |
| `GET /api/bookings?date=YYYY-MM-DD` ou `?start_date=&end_date=` (inclusivo, máx. 31 dias) `&include_cancelled=false` | `list[BookingOut]` que começam no(s) dia(s) local(is), ordenado por `start_time`, `room_id`. 422 `INVALID_DATE_RANGE` / `DATE_RANGE_TOO_LARGE`. |
| `GET /api/bookings/search?q=&limit=20` | `q` (mín. 2 caracteres, máx. 100; caracteres NUL são removidos) casa com `ticket_number` (prefixo/igual, ignorando `#` e espaços) **ou** `requester_name`/`title` (contém, sem diferenciar maiúsculas **nem acentos**: a extensão `unaccent` não é garantida, então o SQL normaliza com `translate(lower(col), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc')` e o termo em Python da mesma forma). Não canceladas. Ordem: hoje e futuras (mais próximas primeiro), depois passadas (mais recentes primeiro). |
| `GET /api/bookings/{id}` | `BookingOut` (404 `BOOKING_NOT_FOUND`) |
| `POST /api/bookings` | `BookingCreate` (+ `lock_id?`) → **201** `BookingWriteResult` |
| `PATCH /api/bookings/{id}` | `BookingUpdate` (`version` + campos + `lock_id?`) → `BookingWriteResult`. Usa `model_fields_set` para saber o que veio (campo enviado com `null` = limpar). |
| `POST /api/bookings/{id}/cancel` | `BookingCancel{version?, reason?}` → `BookingOut` (status `CANCELLED`) |
| `POST /api/bookings/{id}/status` | `BookingStatusIn{status, version?}` → `BookingOut` (`CHECKED_IN`, `NO_SHOW`, `CONFIRMED`) |
| `GET /api/bookings/{id}/logs` | `list[BookingLogOut]` (mais antigo primeiro) |
| `GET /api/audit?date=&booking_date=&user_id=&action=&limit=100&offset=0` | `list[AuditEntryOut]` mais recentes primeiro (todos os logados podem ver; `limit` até 500). `date` = dia (local) da ação; `booking_date` = dia em que a reserva estava **naquela entrada** (casa com o `previous_state` **ou** o `new_state` do log). Os campos-resumo `booking_room_name`, `booking_date`, `booking_requester_name` e `booking_ticket_number` descrevem a reserva **como ela estava naquela entrada** (do próprio snapshot; na falta dele, o estado atual). |

Regras de escrita (serviço `app/services/bookings.py`):
1. Resolve o período (`timeutil.resolve_period`): presets ignoram `start`/`end` enviados. No
   **PATCH**, os horários de uma reserva de período fixo (Dia Inteiro/Manhã/Tarde) **só são
   recalculados** quando `period_type`, `date`, `start` ou `end` vêm na edição: mudar só dados ou
   só a sala mantém o horário gravado (mesmo que a configuração de horários tenha mudado depois),
   e o log é `UPDATED`/`MOVED_ROOM`, sem falso `BOOKING_CONFLICT`. Com `period_type` ou `date`, o
   preset é resolvido de novo com a configuração atual (um Dia Inteiro levado para outro dia ocupa
   o dia inteiro daquele dia). `start`/`end` sem `period_type` numa reserva de período fixo a
   transformam em Horário Específico.
2. Valida: sala existe e ativa (422 `ROOM_INACTIVE`/404 `ROOM_NOT_FOUND`; ver 5.3), dentro do
   horário (422 `OUTSIDE_OPERATING_HOURS`), alinhamento de 5 min (422 `INVALID_TIME`), `end > start`
   (422 `INVALID_INTERVAL`). Reserva cancelada não pode ser editada (409 `BOOKING_NOT_EDITABLE`).
   Limites de entrada na seção 5.7.
3. PATCH/cancel/status com `version` diferente da atual → 409 `STALE_VERSION` com
   `current: BookingOut` (outra recepcionista alterou antes). `version` é incrementada a cada escrita.
4. **Soft locks de outros usuários**: se `get_lock_store().find_overlapping(room, start, end)` devolver
   trava de OUTRO usuário (ignorando travas do próprio usuário e travas cujo `booking_id` seja a
   própria reserva em edição) → 409 `SLOT_LOCKED` com `locks: [LockOut]` e mensagem
   "Fulana está reservando este horário agora."
5. **Conflito**: pré-checagem `SELECT` de reservas ativas sobrepostas (excluindo a própria no PATCH);
   se houver → 409 `BOOKING_CONFLICT`. Se a pré-checagem passar mas o INSERT/UPDATE violar a
   constraint (corrida) → mesmo 409. Corpo:
   `{"code": "BOOKING_CONFLICT", "message": "...", "conflicts": [BookingOut], "alternatives": [RoomBrief]}`
   onde `alternatives` = salas ativas livres no intervalo inteiro, priorizando o mesmo
   `effective_type` exigido (`VIDEO` se `requires_video`, senão `NORMAL` primeiro, depois as demais).
   Para Dia Inteiro isso implementa a opção 1 do fluxo 5.2 ("escolher outra sala livre o dia todo");
   a opção 2 (remanejar a reunião conflitante) é feita pelo frontend com `PATCH` da reserva conflitante.
6. Grava + `booking_logs` na **mesma transação**. Ação do log: criação `CREATED` (ou `IMPORTED`);
   mudou sala → `MOVED_ROOM`; mudou só data/horário → `RESCHEDULED`; só dados → `UPDATED`;
   status → `STATUS_CHANGED`; cancelamento → `CANCELLED`. Nada mudou → não grava log nem versão.
7. **Após o commit, libera travas do próprio usuário (D6)**, publicando `SLOT_UNLOCKED`
   (reason `saved`) para cada uma; depois publica `BOOKING_CREATED` / `BOOKING_UPDATED` (com
   `previous: {room_id, date, start, end}`) / `BOOKING_CANCELLED`:

   | Escrita | Travas do usuário liberadas |
   | --- | --- |
   | `POST` (criar) | (a) a `lock_id` enviada, se for dele; (c) as dele **sem** `booking_id` que se sobrepõem à nova reserva |
   | `PATCH` (editar/mover) | (a) a `lock_id` enviada, se for dele; (b) as dele cujo `booking_id` é esta reserva, em qualquer posição; (c) as dele **sem** `booking_id` sobre a posição **nova** (sala nova, horário novo). Travas que só cobrem a posição **antiga** nunca são liberadas (podem ser outra seleção dele, ex.: o Dia Inteiro que ele monta enquanto remaneja esta reunião, fluxo 5.2). |
   | cancelar | só (b): `BookingCancel` não tem `lock_id`, e cancelar não ocupa horário |
   | status | nenhuma |

   Travas de outros usuários nunca são liberadas por uma escrita.
8. **Avisos não bloqueantes** (`warnings` em `BookingWriteResult`):
   - `VIDEO_ROOM_NOT_NEEDED` (RF06): `requires_video=false` em sala com `effective_type=VIDEO` e
     existe ao menos uma sala `NORMAL` (efetiva) livre no mesmo intervalo →
     `data: {"free_normal_rooms": [RoomBrief]}`.
   - `VIDEO_UNAVAILABLE`: `requires_video=true` em sala sem vídeo operacional →
     `data: {"free_video_rooms": [RoomBrief]}`.
   - `OVER_CAPACITY`: `attendees > capacity` (quando capacidade > 0) → `data: {"capacity", "attendees"}`.

### 5.5 Disponibilidade
| Rota | Detalhes |
| --- | --- |
| `GET /api/availability/free-rooms?date=&start=HH:MM&end=HH:MM&room_type=VIDEO\|NORMAL&exclude_booking_id=` | `list[RoomOut]` ativas sem reserva ativa sobreposta (filtro por `effective_type`). Também aceita `period_type` no lugar de start/end. |
| `GET /api/availability/full-day?date=` | `list[RoomOut]` sem nenhuma reserva ativa no horário de funcionamento do dia (filtro "Livres Dia Inteiro"). |

### 5.6 Excel (módulo Excel)
| Rota | Detalhes |
| --- | --- |
| `GET /api/export/day.xlsx?date=` | planilha do dia no formato espelho (salas em colunas: bloco Videoconferência depois Normais; linhas de `slot_minutes`; reservas mescladas). Textos de usuários são sempre células de texto (nunca fórmula). |
| `GET /api/export/week.xlsx?start_date=` | uma aba por dia útil da semana (nome da aba `Seg 26-10`, sem `/`). |
| `POST /api/import/xlsx?dry_run=true&include_past=false` | admin, multipart campo `file` → `ImportReport`. `dry_run=true` não grava nada. Publica `BOOKINGS_IMPORTED` ao final de uma importação real. |

Downloads exigem o header Bearer (o frontend usa `fetch` → `Blob` → link temporário). Nome do
arquivo via `Content-Disposition` (ex.: `agenda-2026-10-26.xlsx`).

**Upload (D8)**: antes de ler qualquer byte do corpo, o `RequestGuardMiddleware` confere o token de
`POST /api/import/xlsx` — não é ADMIN ativo → 401 (`UNAUTHORIZED` / `SESSION_REVOKED` /
`ACCOUNT_DISABLED`) ou 403 `FORBIDDEN`, no formato da seção 5.0 — e recusa com 413
`{"detail": {"code": "FILE_TOO_LARGE", "message": "O arquivo passa do limite de 10 MB. ..."}}`
um `Content-Length` acima de **10 MB + 64 KB** (folga do envelope multipart) ou um corpo
*chunked* que passe disso enquanto chega. Essas recusas levam `Connection: close`. Depois, a
própria rota (`app/api/excel.py`) recusa com 422 o arquivo que não é `.xlsx`
(`INVALID_FILE_TYPE`), que tem mais de 10 MB (`FILE_TOO_LARGE`) ou é ilegível/inflado demais
(`INVALID_FILE`). Regras da importação: seção 8 e `docs/IMPORTACAO.md`.

### 5.7 Limites, validação e cabeçalhos HTTP

**Cabeçalhos de segurança (D7)** em **toda** resposta HTTP (inclusive 401/404/413/422/500):
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`. No **HTML da SPA** (respostas `text/html` fora de
`/docs` e `/redoc`) também:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

JSON, `/assets/*` e o Swagger (`/docs`, `/redoc`, que usa CDN e script inline) não recebem CSP. A
SPA precisa continuar funcionando sob essa política: **nenhum script inline, nenhum `eval`/`new
Function`/`setTimeout` com texto** (guardado por `frontend/src/test/csp.test.ts` e
`frontend/e2e/security.e2e.ts`; nos testes E2E, não use `page.waitForFunction`).

**Tamanho do corpo**: `POST /api/import/xlsx` até 10 MB + 64 KB (5.6); qualquer outra requisição
até **1 MB** → acima disso 413 `{"detail": {"code": "REQUEST_TOO_LARGE", "message": "A requisição é grande demais."}}`
(conferido pelo `Content-Length` e pelos bytes que chegam).

**Validação de entrada** (`InputModel`, `LocalDate`, `LocalTime`/`HHMM`, `DbId` em `schemas.py`) —
fora disso, 422 padrão do FastAPI:
- nenhum texto (nem item de lista) do corpo pode conter o caractere NUL;
- ids (path, query e corpo) entre 1 e 2³¹−1; `offset` até 2³¹−1;
- datas (reservas, `GET /api/bookings`, disponibilidade, auditoria `date`/`booking_date`) entre
  **2000-01-01 e 2100-12-31**;
- horas locais **sem fuso** (`09:00`; `09:00Z` ou `09:00+05:00` são recusadas) em reservas e
  disponibilidade;
- `display_order` 0–100000; `capacity` 0–10000; `resources` até 50 itens de até 100 caracteres;
  `lock_id` até 100 caracteres; `notes` até 2000; `attendees` 1–10000.

Um valor que ainda assim o PostgreSQL recuse (SQLSTATE classe 22, "data exception") vira 422
`{"detail": {"code": "INVALID_INPUT", "message": "Algum valor enviado é inválido."}}`; os demais
erros de banco continuam 500.

## 6. WebSocket `/ws?token=<jwt>`

O token é validado no *handshake* com as mesmas regras do REST (`check_token`). Para que o
navegador veja o código (fechar antes do `accept` vira HTTP 403 e o navegador só vê 1006), o
servidor aceita e fecha em seguida. Envelope em ambas as direções:
`{"type": str, "data": object, "ts": iso, "request_id"?: str}` — tipos em `app/realtime/events.py`.

**Códigos de fechamento** (*reason* em pt-BR):

| Código | Quando | *Reason* | O que o frontend faz |
| --- | --- | --- | --- |
| **4401** | token ausente, inválido ou vencido (no *handshake*); o JWT venceu com a conexão aberta | `Sessão expirada ou inválida. Entre novamente.` | se outra aba já trocou o token, reconecta com o novo; senão chama `GET /api/auth/me` uma vez para saber o motivo exato e volta ao login (padrão: "Sua sessão expirou. Entre novamente.") |
| **4401** | token revogado (D3): no *handshake* ou na revalidação periódica, que vê `token_version` maior que o `tv` da conexão | `Sua sessão foi encerrada porque a senha foi alterada. Entre novamente.` | idem (o `/api/auth/me` devolve 401 `SESSION_REVOKED`) |
| **4403** | usuário desativado ou removido: no *handshake* ou na revalidação periódica | `Seu acesso foi desativado. Fale com o administrador.` | não reconecta; sai com "Seu acesso foi desativado. Fale com o administrador." |
| **1012** | a instância está encerrando (deploy, reinício) | `Servidor reiniciando` | reconecta (backoff até ~10 s) e ressincroniza no `HELLO` |
| **1012** | a assinatura do Redis voltou depois de uma queda (eventos do intervalo podem ter se perdido) | `Reconectando para sincronizar os dados.` | idem |
| **1013** | a instância ainda não aceita (ou já não aceita) conexões, no *handshake* | `Tempo real indisponível. Tente novamente.` | reconecta |
| **1013** | a fila de envio do cliente estourou (512 mensagens; conexão lenta demais) | `Conexão lenta demais` | reconecta e ressincroniza |
| 1011 | erro interno ao autenticar/abrir a conexão | `Erro interno. Tente novamente.` | reconecta |

**Revalidação**: a cada 60 s cada instância consulta, numa query só, os usuários conectados
(`accounts.active_user_ids` → `{id: token_version}`): fecha com 4403 os desativados/removidos e com
4401 (*reason* de sessão revogada) as conexões cujo `tv` é **menor** que a versão atual — abas
abertas com o token novo continuam. O REST recusa na hora; o tempo real cai em até 1 minuto.

Cliente → servidor:
| type | data | resposta |
| --- | --- | --- |
| `LOCK_SLOT` | `{room_id, date, start, end, booking_id?}` (+ `request_id`) | `LOCK_ACQUIRED {lock}` ao remetente + `SLOT_LOCKED {lock}` broadcast; ou `LOCK_DENIED {reason: "LOCKED_BY_OTHER", message, conflicts: [LockOut]}` |
| `UNLOCK_SLOT` | `{lock_id}` | `SLOT_UNLOCKED {lock, reason: "released"}` broadcast (só o dono pode liberar) |
| `REFRESH_LOCK` | `{lock_id}` | `LOCK_REFRESHED {lock}` ao remetente + `SLOT_LOCKED {lock}` broadcast (novo `expires_at`) |
| `PING` | `{}` | `PONG` |

Mensagens inválidas não derrubam o socket: viram `ERROR {code, message}` (`MESSAGE_TOO_LARGE` acima
de 16 KiB, `INVALID_JSON`, `INVALID_MESSAGE`, `UNKNOWN_TYPE`, `INTERNAL_ERROR`, além dos erros de
validação do `LOCK_SLOT`).

Servidor → cliente: `HELLO` ao conectar (`{user, instance_id, server_time, locks, online}`),
`SLOT_LOCKED`, `SLOT_UNLOCKED` (reasons `released`|`saved`|`expired`|`disconnected`),
`BOOKING_CREATED`, `BOOKING_UPDATED`, `BOOKING_CANCELLED`, `BOOKINGS_IMPORTED`,
`ROOM_CREATED`, `ROOM_UPDATED`, `PRESENCE {online}`, `ERROR {code, message}`.
O frontend recarrega as salas e a semana exibida a **cada** `HELLO` (inclusive o primeiro), então
qualquer reconexão ressincroniza a tela.

Semântica das travas:
- Uma trava cobre `[start, end)` de uma sala em um dia; é guardada como uma chave por slot
  (`registry.slot_key`) + metadados da trava; aquisição é tudo-ou-nada (SET NX por slot com rollback
  das já obtidas em caso de falha — sem depender de Lua, para funcionar com fakeredis).
- Uma nova seleção do mesmo usuário substitui suas travas sobrepostas (e o frontend libera a
  anterior ao mudar de seleção). Um usuário pode ter travas em várias abas/conexões.
- Liberação: `UNLOCK_SLOT`; gravação de uma reserva pelo dono (regras D6, seção 5.4 regra 7);
  desconexão do WebSocket (`release_connection`); ou expiração do TTL. Fechar a aba libera na hora;
  uma queda silenciosa da rede (cabo, Wi-Fi, notebook que dormiu) só é percebida pelo *ping* do
  uvicorn (20 s + 20 s de prazo: até ~40 s) ou, no pior caso, pelo TTL (120 s) + a varredura. Uma
  tarefa de fundo por instância chama `pop_expired()` a cada ~2 s e publica
  `SLOT_UNLOCKED reason=expired` (cada trava exatamente uma vez, mesmo com várias instâncias — a
  remoção atômica do `ZREM` é a "posse" da expiração).
- O frontend renova (`REFRESH_LOCK`) a cada **⅓ de `lock_ttl_seconds`, entre 5 e 45 s** (40 s com o
  padrão de 120 s) enquanto o modal estiver aberto, e trava de novo na hora se a própria trava for
  liberada (`saved`/`expired`) com o modal ainda aberto. Durante o próprio salvamento não pede
  trava nova; uma `LOCK_ACQUIRED` atrasada é liberada logo.
- O navegador manda `PING` a cada 25 s (espera `PONG` por 10 s) e reconecta com backoff exponencial
  até ~10 s.
- Presença: conexões por usuário em todas as instâncias; `PRESENCE` é emitido quando alguém entra/sai.
  Com Redis, cada instância mantém suas conexões em chaves com TTL (heartbeat de 10 s, TTL 20 s)
  para não deixar "fantasmas" quando uma instância morre.
- Multi-instância: canal Redis pub/sub `agenda:events`; cada instância repassa a suas conexões locais.
  Sem Redis: bus em memória (entrega local direta).

## 7. Frontend (React + TypeScript + Vite + Tailwind CSS)

Stack: React 19, TypeScript estrito, Vite, Tailwind CSS v4 (`@tailwindcss/vite`), react-router,
zustand (estado), lucide-react (ícones). Sem bibliotecas de calendário pesadas. Tipos da API em
`frontend/src/api/types.ts` espelhando `backend/app/schemas.py`. Textos em pt-BR. O build não pode
ter script inline nem `eval` (CSP, seção 5.7).

Telas: **Login**, **Grade** (principal), **Admin** (Salas, Usuários, Importar planilha, Auditoria —
Salas/Usuários/Importar só para ADMIN; Auditoria para todos).

Login e sessão (D2/D4):
- Aviso da tela de login conforme o motivo do fim da sessão: 401 `UNAUTHORIZED` / WS 4401 →
  "Sua sessão expirou. Entre novamente."; `SESSION_REVOKED` → "Sua sessão foi encerrada porque a
  senha foi alterada. Entre novamente."; `ACCOUNT_DISABLED` / WS 4403 → "Seu acesso foi desativado.
  Fale com o administrador."
- 429 `TOO_MANY_ATTEMPTS`: mostra a mensagem do servidor e "Tente novamente em m:ss", contando a
  cada segundo, com **Entrar** desabilitado até o fim (`retry_after_seconds`, ou o `Retry-After`).
- A sessão é compartilhada entre as abas do navegador (evento `storage`): **Sair** numa aba sai de
  todas; um login numa aba entra nas abas que estão na tela de login; outra pessoa entrando faz as
  demais abas recarregarem como ela. Depois de **Sair** (ou de conta desativada) o próximo login
  vai para a grade; depois de sessão vencida/revogada, a mesma pessoa volta à página em que estava.

Grade — 4 zonas fixas sem rolagem de página (só a grade rola internamente, com cabeçalho e coluna
de horários *sticky*):
1. **Barra superior**: título, busca instantânea (atalhos `/` ou `Ctrl+K`; debounce ~200 ms;
   resultados com chamado, solicitante, data, sala; Enter abre o primeiro → muda a aba para o dia,
   rola até a reserva e a destaca com animação), filtros de 1 clique `Todas as Salas` |
   `Videoconferência` | `Salas Normais` | `Livres Dia Inteiro` (este último: salas sem nenhuma
   reserva ativa no dia; enquanto as reservas do dia não chegaram, mostra "Procurando salas livres
   o dia inteiro…" em vez de listar todas), botões Exportar dia/semana, indicador de conexão em
   tempo real, avatares (iniciais coloridas) das recepcionistas online, menu do usuário.
2. **Grade central**: colunas = salas (bloco **Videoconferência** à esquerda com destaque visual e
   ícone de câmera; bloco **Salas Normais** à direita; sala VIDEO em manutenção aparece no bloco
   normal com selo "Vídeo em manutenção · #chamado" com link — o número fica visível mesmo quando o
   texto é cortado). Sala desativada com reservas no dia: coluna cinza hachurada "Sala desativada"
   (5.3). Cabeçalho mostra nome, capacidade, recursos (tooltip). Linhas = slots de `slot_minutes`
   de `day_start` a `day_end`. Reservas são blocos que mesclam visualmente as linhas que ocupam
   (posicionamento por minutos), exibindo chamado (link), solicitante, assunto, horário e selos:
   "Dia inteiro", "Reunião Presencial — Remanejável" (`is_relocatable`), "⚠ Precisa de vídeo"
   (`video_unavailable`), "Check-in", "Não compareceu". Travas de outros usuários: hachurado com
   "Reservando: Fulana". Linha da hora atual no dia de hoje; horários passados levemente
   esmaecidos. Estrutura ARIA de grade válida (`role=row`, `aria-colspan` nos grupos) e uma região
   *live* educada anuncia a célula do cursor do teclado.
3. **Modal/popover rápido de agendamento** ancorado na seleção: campos no topo
   `Nº do Chamado` (foco automático), `Solicitante`, `Assunto/Área`, `Tipo de Período`
   (segmentado: Horário Específico | Dia Inteiro | Manhã | Tarde), início/fim (quando específico),
   switch `Precisa de Videoconferência?` (padrão **Não**), "Mais detalhes" recolhido
   (participantes, observações). Avisos inline: RF06 (sala de vídeo sem necessidade + lista de salas
   normais livres clicáveis para trocar de sala), capacidade, vídeo indisponível. Conflito de Dia
   Inteiro (5.2): painel com (1) salas livres o dia todo (clicar troca a sala e salva) e
   (2) para cada reunião conflitante, salas livres naquele horário para remanejá-la com 1 clique
   (PATCH da conflitante; depois tenta salvar de novo). Os botões de sala mostram "· N lugares"
   (em vermelho se não cabe) e listam primeiro as salas que comportam os participantes; avisos do
   remanejamento aparecem como "Reunião de X: …". Cliques no cabeçalho, na coluna de horários ou na
   barra de rolagem da grade **não** fecham o modal (só um clique numa célula/reserva da grade ou
   fora dela).
4. **Barra inferior de abas diárias** estilo Excel: `Seg 26/10` … `Sex 30/10` da semana exibida,
   setas ‹ › de semana, botão "Hoje", `+ Calendário` (seletor de data; datas fora de seg–sex viram
   aba extra temporária). Cada aba mostra a quantidade de reservas do dia (quando conhecida).

URL: `/?date=YYYY-MM-DD` define o dia; `&highlight=<bookingId>` (usado pelos links "Abrir na grade"
da desativação de sala) destaca a reserva, volta o filtro para "Todas" e sai da URL.

Interações e RNF03 (≤ 3 cliques ou só teclado):
- Clique numa célula vazia (1) → trava (`LOCK_SLOT`) e abre o modal com foco no chamado → digitar
  chamado, Tab, solicitante → Enter ou clique em Salvar (2). Arrastar sobre várias células da mesma
  sala seleciona a faixa. `Esc` antes da resposta do `LOCK_SLOT` cancela (a trava que chegar
  atrasada é liberada e o modal não abre).
- Teclado: setas movem o cursor de célula; Shift+setas estende a seleção; Enter abre o modal;
  Esc fecha (liberando a trava); dentro do modal Enter salva (Ctrl+Enter no campo de observações);
  Alt+1..4 escolhe o tipo de período; PageUp/PageDown ou Ctrl+←/→ trocam de dia.
- Clique numa reserva → popover de detalhes/edição (editar, mover, check-in, não compareceu,
  cancelar com confirmação, histórico via `/logs` com todos os campos alterados, link do chamado).
- Arrastar uma reserva para outra sala/horário (drag-and-drop) → `PATCH` com `version`; destino
  destacado; conflito/trava → toast de erro e o bloco volta ao lugar.
- Edição concorrente: 409 `STALE_VERSION` → aviso "Esta reserva foi alterada por outra pessoa" e
  recarrega os dados.
- Tempo real: cliente WebSocket com reconexão automática (backoff até ~10 s); a cada `HELLO`
  recarrega salas e semana; aplica eventos de forma incremental (sem recarregar a página);
  fechamentos 4401/4403 conforme a seção 6.
- Administração › Salas: desativar uma sala com reservas futuras mostra a lista do 409
  `ROOM_HAS_FUTURE_BOOKINGS` (data, horário, solicitante, chamado; "Mostrando X de N") com um link
  **Abrir na grade** (nova aba) por reserva; nada é salvo. Toasts nas páginas `/admin/*` ficam no
  canto inferior esquerdo (na grade, inferior direito).
- Administração › Importar: a importação real usa exatamente as opções da simulação; mudar
  "Incluir datas passadas" depois de simular descarta a simulação.

## 8. Importação da planilha legada (módulo Excel)

Formato, heurísticas e relatório detalhados em `docs/IMPORTACAO.md`. Resumo do contrato:
- Uma aba por dia; data pelo nome da aba (`26-10`, `26.10`, `26/10`, `26-10-2026`, `Seg 26-10`,
  `segunda 26.10.26` …) ou por uma célula de data no topo; ano ausente → ano que torna a data mais
  próxima de hoje; data impossível (ex.: 29/02 sem ano bissexto próximo) ou aba sem data →
  `SKIPPED_UNRECOGNIZED` com aviso. Abas ocultas e células em linhas/colunas ocultas são ignoradas
  com aviso.
- Cabeçalho = linha (ou faixa de linhas) cujas células casam com nomes de salas cadastradas
  (sem acento, sem caixa, ignorando espaços extras e o prefixo "Sala"; nome exato e salas ativas
  têm preferência). Nomes não encontrados → `unknown_rooms`.
- Coluna de horários = coluna com valores tipo `08:00`, `8h`, `8:00 - 8:30`, `08h30`, `time`/`datetime`.
- Cada célula não vazia (ou **intervalo mesclado**) numa coluna de sala vira uma reserva cobrindo as
  linhas correspondentes. Bloco que cobre todo o horário de funcionamento → `FULL_DAY`;
  manhã/tarde inteiras → `MORNING`/`AFTERNOON`.
- Texto da célula: extrai `ticket_number`, `requester_name`/`title` e indício de vídeo; planilhas
  geradas pela própria exportação são lidas por posição (ida e volta exata).
- Somente datas ≥ hoje por padrão (`include_past=false`). Conflitos e travas não abortam a
  importação: a reserva é pulada com aviso. Ação de log `IMPORTED`. Limites contra arquivos
  maliciosos (64 MB descompactados, *zip bomb*, células/mesclagens por aba): ver IMPORTACAO §8.
- **Travas de seleção (D9)**: uma reserva da planilha que se sobrepõe a uma trava de **qualquer**
  pessoa (inclusive de quem importa) é pulada com o aviso
  `Ana Recepção está reservando este horário agora na grade — pulada.` — também na simulação. Pela
  tela/API vale a trava do servidor; na CLI, com `AGENDA_REDIS_URL` a checagem usa um
  `RedisLockStore` (o mesmo Redis do servidor); **sem Redis a CLI não vê nenhuma trava**. Se as
  travas não puderem ser consultadas, a importação segue com um aviso.
- **Reimportação idempotente (D10)**: cada reserva importada guarda
  `import_key = "v1:" + sha256(data da aba ISO | chave normalizada da sala | início HH:MM | fim HH:MM | texto normalizado da célula)[:40]`
  (passada a `create_booking(import_key=...)`). Antes de criar, se **qualquer** reserva — de qualquer
  status, inclusive cancelada, movida ou editada — já tem aquela marca, a célula é pulada com
  "já importada anteriormente (pode ter sido movida ou cancelada no sistema)". A checagem antiga
  continua: reserva ativa idêntica (mesma sala, início, fim e chamado) é pulada como "já cadastrada".
  Assim o que a recepção cancelou ou remanejou depois da virada não volta numa reimportação.
- CLI: `python -m app.cli import-xlsx caminho.xlsx [--commit] [--include-past] [--user admin]`
  (padrão é simulação) e `python -m app.cli export-xlsx --date 2026-10-26 [--week] -o arquivo.xlsx`.

## 9. CLI (`python -m app.cli`, executado de dentro de `backend/`)

| Comando | Detalhes |
| --- | --- |
| `init-db` | migrations + administrador inicial de `AGENDA_BOOTSTRAP_ADMIN_*` **se não houver usuários**. Em `AGENDA_ENV=production` recusa (código 1, mensagem em pt-BR) uma senha inicial com menos de 12 caracteres, igual ao usuário, conhecida (`admin`, `admin123`, `senha123`, `123456`…, `password`, `changeme`, os valores dos modelos…) ou que comece com `troque`. Senha vazia: não cria ninguém e orienta a usar `create-user`. |
| `create-user --username --name --role [--password]` | pede a senha no terminal (duas vezes) se omitida; mínimo de 6 caracteres |
| `reset-password --username [--password]` | define uma nova senha (pede duas vezes se omitida), incrementa `token_version` (encerra as sessões abertas, D3) e avisa se a conta está desativada |
| `seed-demo` | salas de exemplo (4 de vídeo, 6 normais), usuários de demonstração (senhas em `backend/.env.example`/`seed.py`) e reservas na semana atual — no sábado/domingo, na **próxima** semana. **Somente** `AGENDA_ENV=development`. |
| `import-xlsx`, `export-xlsx` | seção 8. Sem `-o`, o `export-xlsx` grava `agenda-AAAA-MM-DD.xlsx` (ou `agenda-semana-…`) na pasta atual ou, se ela não aceita gravação (ex.: `/app/backend` na imagem Docker), na pasta temporária do sistema; o caminho final é impresso. |

Erros esperados viram `Erro: <mensagem>` no stderr e código 1, sem traceback. Falha de arquivo
local (permissão, pasta inexistente) é relatada como tal ("não foi possível gravar a planilha
em …", "não foi possível ler o arquivo …"); só erros de conexão/banco dizem "não foi possível
acessar o banco de dados (URL sem senha)".

## 10. Critérios de pronto (do planejamento, verificáveis)

- Fase 1: teste automatizado — duas requisições `POST /api/bookings` simultâneas para a mesma
  sala/horário → exatamente um 201 e um 409 `BOOKING_CONFLICT`; o banco tem 1 registro.
- Fase 2: na grade, navegar pelas abas e reservar horário parcial e Dia Inteiro; link do chamado.
- Fase 3: ação na aba A do navegador aparece na aba B sem recarregar; trava visível como
  "Reservando: Fulana"; liberação ao cancelar/fechar/desconectar e por expiração.
- Fase 4: importação da planilha (com relatório), exportação `.xlsx`, vídeo em manutenção, alerta
  de remanejamento, auditoria consultável.

## 11. Decisões da revisão (D1–D10)

| | Decisão | Onde |
| --- | --- | --- |
| D1 | Desativar sala com reservas ativas por vir → 409 `ROOM_HAS_FUTURE_BOOKINGS` (sem forçar); `include_inactive=true` para todos os logados; coluna "Sala desativada" só nos dias com reservas | 5.3, 7 |
| D2 | Códigos de 401 (`UNAUTHORIZED`, `SESSION_REVOKED`, `ACCOUNT_DISABLED`), login de inativo continua `INVALID_CREDENTIALS`; WS 4401/4403; mensagens do frontend | 5.0, 6, 7 |
| D3 | Claim `tv` = `users.token_version`; troca de senha (e desativação) revoga os tokens anteriores; sem `tv` = 0 | 5.1, 5.2, 9 |
| D4 | Limite de login por usuário e por IP: 5 falhas → 1, 5, 15 min; 429 `TOO_MANY_ATTEMPTS` + `Retry-After`; Redis ou memória | 5.1, 7 |
| D5 | `GET /api/health` com `status` ok/degraded e `redis` ok/down/disabled; 503 só sem banco | 5.1 |
| D6 | Travas liberadas depois de uma escrita: `lock_id`, travas da própria reserva e travas sem reserva sobre a posição nova; nunca as da posição antiga | 5.4 (regra 7), 6 |
| D7 | Cabeçalhos de segurança em toda resposta e CSP no HTML da SPA | 5.7 |
| D8 | Guarda ASGI do upload: token de ADMIN e tamanho antes de ler o corpo → 401/403/413 | 5.6, 5.7 |
| D9 | Importação pula reservas sob trava de seleção de qualquer pessoa (CLI: só com Redis) | 8 |
| D10 | `bookings.import_key`: reimportar nunca recria reserva movida, cancelada ou editada | 4, 8 |
