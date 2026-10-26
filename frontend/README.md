# Agenda de Salas — frontend

SPA da recepção (contrato `docs/CONTRATO.md` §7): React 19 + TypeScript estrito + Vite 8 +
Tailwind CSS 4, estado em zustand, ícones lucide-react, testes com Vitest + Testing Library.

## Comandos

| Comando                                                 | O que faz                                                                                                                |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `npm install`                                           | instala as dependências (use `--fetch-timeout=120000 --fetch-retries=5` atrás do proxy)                                  |
| `npm run dev`                                           | Vite em `http://127.0.0.1:5173`, com proxy de `/api` e `/ws` para `127.0.0.1:8000` (`AGENDA_BACKEND_URL` muda o destino) |
| `npm run build`                                         | `tsc -b` (zero erros) + build de produção em `dist/`                                                                     |
| `npm run preview`                                       | serve o `dist/` em `http://127.0.0.1:4173` com o mesmo proxy                                                             |
| `npm test -- --run`                                     | testes unitários e de componentes (jsdom)                                                                                |
| `npm run e2e`                                           | build + testes ponta a ponta (Playwright no Microsoft Edge instalado, backend real na porta 8100, banco `agenda_e2e`)    |
| `npm run typecheck` / `npm run lint` / `npm run format` | checagem de tipos, ESLint, Prettier                                                                                      |

Em produção o backend serve o `dist/` na mesma origem (`AGENDA_FRONTEND_DIST`): a SPA usa URLs
relativas (`/api/...`) e monta o WebSocket a partir de `window.location` (`ws(s)://host/ws?token=`).
Rotas da SPA (`/login`, `/admin/...`) dependem do fallback para `index.html` do backend.

## Organização

```
src/
  api/         types.ts (espelho de schemas.py + eventos), client.ts (fetch + Bearer + ApiError),
               errors.ts (parsing/tradução pt-BR), endpoints.ts (todas as rotas)
  realtime/    socket.ts (WebSocket: HELLO, backoff, PING, request_id), service.ts (ciclo de vida),
               applyEvent.ts (eventos → stores)
  store/       auth, config, rooms, bookings (+ bookingsModel puro), locks (+ locksModel), presence,
               realtime, ui (seleção, cursor, modal, filtros, destaque), toasts
  lib/         time (datas/horas locais), grid (geometria minutos ↔ px, seleção, snap), periods,
               rooms, labels, auditDiff, people
  features/
    grid/      GridPage (4 zonas), TopBar, SearchBox, RoomGrid/RoomColumn/BookingBlock, DayTabs,
               BookingPopover, composer/ (modal rápido, travas, conflito 5.2, RF06)
    admin/     Salas, Usuários, Importar planilha, Auditoria
    auth/ shell/ common/
  components/ui/  primitivas (Button, Dialog, FloatingPanel, Segmented, Switch, Tooltip, Toaster…)
```

## Regras que valem a pena saber

- **Eventos idempotentes e à prova de ordem**: uma reserva só é substituída por uma `version` maior;
  a visão por dia deriva de `booking.date` (mudar de data tira a reserva do dia antigo); canceladas
  ficam como lápide para barrar eventos atrasados. Buscas mescladas sem perder eventos recebidos
  durante a busca (`store/bookingsModel.ts`).
- **Travas**: seleção → `LOCK_SLOT` (negada = aviso, o modal não abre); o modal retrava ao mudar
  sala/horário, renova a cada ⅓ de `lock_ttl_seconds` (entre 5 e 45 s; 40 s com 120), retrava na
  hora se a própria trava for liberada com o modal aberto e libera ao fechar; salvar envia `lock_id`
  (o servidor libera, regras D6 do contrato, seção 5.4).
  Travas vencidas somem no cliente pelo `expires_at` (relógio do servidor, medido no `HELLO`).
- **Reconexão**: backoff exponencial até ~10 s + jitter; ao reconectar recarrega salas e a semana
  exibida a cada `HELLO`; fechamento `4401` (sessão vencida/revogada; antes consulta
  `/api/auth/me` para saber o motivo) ou `4403` (conta desativada), ou 401 na API, volta para o
  login com o aviso do motivo (contrato, seções 6 e 7). O evento `offline` do navegador
  fecha a conexão na hora (o indicador mostra "Reconectando…") e o `online` reconecta sem esperar o
  backoff.
- **Teclado (RNF03)**: setas/Shift+setas/Enter/Esc na grade, `Alt+1..4` no modal, `PageUp/PageDown`
  e `Ctrl+←/→` trocam o dia, `/` e `Ctrl+K` abrem a busca.

## Testes ponta a ponta (`e2e/`)

`npm run e2e` faz o build e roda o Playwright no **Microsoft Edge instalado** (`channel: 'msedge'`,
headless, 1366×768). O `webServer` do `playwright.config.ts` sobe o uvicorn na porta 8100 servindo o
`dist/`; o `e2e/global-setup.ts` recria o banco `agenda_e2e`, roda `python -m app.cli init-db` e
cadastra salas, usuários (senhas de teste em `e2e/support/data.ts`) e reservas pela API REST.

- Cada teste que grava usa um dia próprio em semanas futuras fixas (`DAYS` em `e2e/support/data.ts`)
  e limpa esse dia antes de começar, então não depende de "hoje" e pode ser repetido.
- `e2e/support/grid.ts` é o page object da grade (`[data-cell]`, `[data-booking-id]`, `[data-lock-id]`…);
  `e2e/support/timing.ts` mede os atrasos do tempo real (impressos como `[tempo real] …`).
- Por padrão um worker (backend e banco compartilhados). Variáveis: `E2E_WORKERS`, `E2E_PORT`,
  `E2E_DB_NAME`, `E2E_PG_URL`, `E2E_PYTHON`, `E2E_REUSE_SERVER=1`.
- Capturas das telas principais em `e2e/screenshots/`; falhas deixam trace e captura de tela em
  `test-results/` (`npx playwright show-trace …`) e o relatório em `playwright-report/`.
