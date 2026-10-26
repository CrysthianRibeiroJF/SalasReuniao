# Operação — Agenda de Salas

Guia da administração e da TI para colocar o sistema no ar no lugar da planilha (go-live), cuidar
dele no dia a dia e saber o que fazer quando algo dá errado. Instalação, configuração e comandos
estão no [README](../README.md); o formato da planilha legada e o relatório da importação, em
[IMPORTACAO.md](IMPORTACAO.md); o guia das recepcionistas, em [MANUAL_RECEPCAO.md](MANUAL_RECEPCAO.md).

Nos comandos abaixo, **Docker** significa a instalação com `docker compose` (rodar na pasta do
projeto, no servidor) e **Windows** significa a instalação sem Docker (rodar de dentro da pasta
`backend\` do servidor).

---

## 1. Antes da virada

Confira tudo isto até a semana anterior:

- [ ] Sistema no ar no endereço definitivo, **com HTTPS** (README, seções 4 e 7), e
      `GET /api/health` respondendo `{"status":"ok","database":true,"realtime":"redis","redis":"ok"}`.
- [ ] Administrador inicial criado com senha forte (README, seção 4) e a senha **apagada do `.env`**
      depois do primeiro acesso.
- [ ] A porta 8000 só é alcançável pelo proxy reverso (`HTTP_BIND=127.0.0.1` ou firewall): é o que
      torna confiável o IP usado no limite de tentativas de login.
- [ ] `AGENDA_TICKET_URL_TEMPLATE` configurado: o número do chamado vira link nas reservas.
- [ ] **Salas cadastradas** em *Administração → Salas*, com tipo (Videoconferência ou Normal),
      capacidade, recursos e a mesma ordem das colunas da planilha.
- [ ] **Uma conta para cada recepcionista** (nada de conta compartilhada: o histórico registra quem
      fez cada coisa).
- [ ] Backup diário do banco agendado e um **teste de restauração** feito num banco separado
      (README, seção 6, "Teste de restauração (não afeta produção)").
- [ ] Recepcionistas treinadas com o [manual](MANUAL_RECEPCAO.md), de preferência num ambiente de
      testes com dados de exemplo.
- [ ] Teste de tempo real: duas pessoas em computadores diferentes; o que uma faz aparece na tela
      da outra sem recarregar, e a seleção de uma aparece para a outra como *"Reservando: Fulana"*.

## 2. Virada da planilha (go-live)

Segue o plano de transição do [planejamento](PLANEJAMENTO.md) (seção 8):

| Quando | Etapa |
| --- | --- |
| 2 dias antes | 2.1 Padronizar os nomes das salas e simular a importação |
| Fora do horário comercial (véspera à noite ou madrugada) | 2.2 Importação real das abas de hoje em diante |
| Manhã do go-live | 2.3 Planilha antiga em somente leitura, com link para o sistema |
| Primeira semana | 2.4 Operação assistida com exportação diária |

### 2.1 Dois dias antes: padronização e simulação

1. **Compare os nomes.** Cada coluna de sala da planilha precisa corresponder a uma sala
   cadastrada. Maiúsculas, acentos, espaços extras e o prefixo "Sala" não importam
   (`SALA 01 executiva` casa com `Sala 01 - Executiva`; detalhes em IMPORTACAO.md, seção 3.3).
   Cadastre as salas que faltarem ou corrija o nome na planilha — o que for o nome oficial.
   **Não renomeie salas no cadastro entre uma importação e outra** (a marca de reimportação usa o
   nome cadastrado: IMPORTACAO.md, seção 4.3).
2. **Simule a importação** com uma cópia `.xlsx` atual da planilha (arquivos `.xls` antigos precisam
   ser salvos como `.xlsx`). A simulação **não grava nada**.
   - **Pela tela:** *Administração → Importar planilha* → *Escolher arquivo* → *Simular importação*.
   - **Docker:**
     ```bash
     docker compose cp "Agenda 2026.xlsx" app:/tmp/agenda.xlsx
     docker compose exec app python -m app.cli import-xlsx /tmp/agenda.xlsx
     ```
   - **Windows:**
     ```powershell
     .\.venv\Scripts\python -m app.cli import-xlsx "C:\Transicao\Agenda 2026.xlsx"
     ```
3. **Leia o relatório** (IMPORTACAO.md, seção 5) e corrija até sobrarem só avisos esperados:
   - a linha **"Salas não encontradas no cadastro"** precisa sumir;
   - avisos **"associada por semelhança"** devem ser conferidos um a um;
   - abas com **data não identificada** devem ser renomeadas (ex.: `Seg 26-10`, `26.10.2026`);
   - **conflitos** (duas reservas na mesma sala e horário) precisam ser resolvidos na planilha —
     senão a segunda é pulada.
4. **Simule de novo** depois de cada correção.
5. **Avise a equipe**: dia e hora da virada, endereço do sistema, login de cada uma e que, a partir
   da manhã do go-live, a planilha fica só para consulta.

### 2.2 Fora do horário comercial: importação real

Ninguém deve agendar na planilha nem no sistema durante esta janela (uma seleção aberta na grade
faz a importação **pular** aquele horário: IMPORTACAO.md, seção 4.2).

1. **Backup do banco** antes de importar (README, seção 6). Se algo sair errado, basta restaurá-lo
   ("Recuperação de desastre").
2. Baixe a **versão final** da planilha (`.xlsx`).
3. Rode a **simulação final** (2.1, passo 2) e anote o total de reservas "importáveis".
4. **Importe de verdade:**
   - **Pela tela:** depois da simulação, *Importar de verdade* (todas as telas abertas atualizam na
     hora; a importação usa exatamente as opções da simulação).
   - **Docker:**
     ```bash
     docker compose cp "Agenda 2026.xlsx" app:/tmp/agenda.xlsx
     docker compose exec app python -m app.cli import-xlsx /tmp/agenda.xlsx --commit --user admin
     ```
   - **Windows:**
     ```powershell
     .\.venv\Scripts\python -m app.cli import-xlsx "C:\Transicao\Agenda 2026.xlsx" --commit --user admin
     ```
   Só entram abas de **hoje em diante** (`--include-past` / *Incluir datas passadas* traz também as
   anteriores, o que normalmente não é necessário). `--user` define quem aparece como autor das
   reservas importadas no histórico (padrão: o primeiro administrador ativo). No Docker (com Redis)
   a linha de comando avisa as telas abertas (`Servidor avisado (N instância(s))`) e confere as
   seleções em andamento; **sem Redis** ela não vê as seleções nem avisa ninguém (F5 nas telas
   abertas) — nesse caso prefira a importação pela tela.
5. **Confira:**
   - relatório **"IMPORTAÇÃO CONCLUÍDA"**, sem erros e com o total anotado no passo 3;
   - abra dois ou três dias na grade e compare com a planilha (reservas de Dia Inteiro, Manhã/Tarde,
     reuniões longas mescladas, números de chamado);
   - *Administração → Auditoria*, filtro *Importou*: as reservas aparecem com o seu usuário.
6. Se a planilha recebeu reservas depois da importação, **importe de novo**: só as células novas
   entram. Cada reserva importada guarda a marca da célula de origem, então o que já foi importado
   **nunca é recriado** — nem se a recepção já a cancelou, mudou de sala/horário ou editou no
   sistema (aviso "já importada anteriormente"). Uma célula **alterada na planilha** conta como nova
   (e pode aparecer como conflito com a antiga). Detalhes em IMPORTACAO.md, seção 4.3.

### 2.3 Manhã do go-live: planilha somente leitura

1. Mude a permissão da planilha antiga para **somente visualização** para todas as recepcionistas
   (SharePoint/OneDrive: *Compartilhar → Pode exibir*; pasta de rede: permissão NTFS só de
   *Leitura*; Google Planilhas: *Leitor*).
2. Coloque no topo da planilha (uma aba `LEIA-ME` na frente e/ou uma linha no alto de cada aba) o
   aviso com o link:
   > **Esta planilha não é mais atualizada.** Agende em: https://agenda.empresa.com.br
3. Com uma conta da recepção, confirme que **não** é possível editar a planilha.
4. Na primeira hora, passe pela recepção: todas conseguem entrar? O indicador no topo mostra
   **Conectado** e os avatares das colegas online aparecem?

### 2.4 Primeira semana: operação assistida e exportação diária

- **Exportação diária** como backup visual (pedido do planejamento): no fim de cada dia, alguém
  clica em **Exportar → Dia** (e **Semana** na sexta) e salva o arquivo na pasta combinada, por
  exemplo `\\servidor\Recepcao\Agenda\`. O arquivo sai como `agenda-AAAA-MM-DD.xlsx`, no mesmo
  formato da planilha (uma aba por dia).
- Para automatizar (agendador do servidor, todo dia às 19h30):
  - **Docker** (o container só grava em `/tmp`):
    ```bash
    docker compose exec -T app python -m app.cli export-xlsx -o /tmp/agenda-dia.xlsx
    docker compose cp app:/tmp/agenda-dia.xlsx "/mnt/recepcao/agenda-$(date +%F).xlsx"
    ```
  - **Windows** (Agendador de Tarefas, "Iniciar em": a pasta `backend\`):
    ```powershell
    .\.venv\Scripts\python -m app.cli export-xlsx -o "\\servidor\Recepcao\Agenda\agenda-$(Get-Date -Format yyyy-MM-dd).xlsx"
    ```
  Sem `--date`, exporta o dia de hoje; `--week` exporta a semana (segunda a sexta) do dia. Se a
  pasta de destino não aceitar gravação, o comando termina com
  `Erro: não foi possível gravar a planilha em ...` (código 1) — confira a permissão da pasta.
- **Conversa rápida diária** com a equipe (5 minutos): dúvidas, sugestões, problemas. Anote tudo.
- **Acompanhe** *Administração → Auditoria*, os logs (`docker compose logs --since 24h app`) e o
  `/api/health`.
- **Fim da operação assistida**: depois de uma semana sem incidentes e com a equipe segura, a
  exportação diária vira opcional e a planilha antiga pode ser arquivada.

## 3. Dia a dia da administração

| Tarefa | Onde / como |
| --- | --- |
| Nova sala, capacidade, recursos, ordem das colunas | *Administração → Salas*. Mudar uma sala de Videoconferência para Normal encerra a manutenção de vídeo. |
| Desativar uma sala | *Administração → Salas → Editar*, desligue *Sala ativa* e salve. Salas não são apagadas (o histórico fica). Se ainda houver reservas por vir nela, nada é salvo e aparece a lista (data, horário, solicitante, chamado; até 50, com "Mostrando X de N") com o link **Abrir na grade** em cada uma (abre o dia certo, com a reserva destacada, em outra aba): mova cada reserva para outra sala ou cancele e salve de novo. Depois de desativada, a sala só aparece na grade — cinza, com o selo "Sala desativada" — nos dias passados em que tinha reservas; ali não se cria reserva nova. |
| Vídeo quebrado / consertado | Qualquer pessoa, pelo ícone no cabeçalho da sala na grade, informando o chamado de TI. A sala vai para o bloco das normais até a manutenção ser encerrada. |
| Nova recepcionista | *Administração → Usuários → Novo usuário* (papel Recepção). Pela linha de comando: `create-user --username ana.souza --name "Ana Souza" --role RECEPTIONIST` (Docker: `docker compose exec app python -m app.cli create-user ...`). |
| Senha esquecida | *Administração → Usuários* → editar → *Nova senha*. Pela linha de comando (inclusive para o único administrador): `reset-password --username ana.souza` (Docker: `docker compose exec app python -m app.cli reset-password --username ana.souza`; a senha é pedida duas vezes). Trocar a senha **encerra as sessões abertas** da pessoa; se for a sua, entre de novo. |
| Forçar a saída de alguém (computador esquecido logado, senha vazada) | Redefina a senha da pessoa (tela ou `reset-password`): os tokens antigos deixam de valer na hora e o tempo real dela cai em até 1 minuto, com a mensagem "Sua sessão foi encerrada porque a senha foi alterada". |
| Alguém saiu da equipe | *Administração → Usuários* → desativar. O acesso pela API é recusado na hora ("Seu acesso foi desativado. Fale com o administrador.") e o tempo real dela cai em até 1 minuto. Reativada depois, a pessoa precisa entrar de novo. O histórico continua com o nome dela. |
| Alguém bloqueado por tentativas de login | Espere o bloqueio acabar (1, 5 ou 15 min, mostrado na tela). Com Redis, dá para liberar antes apagando a chave: `docker compose exec redis redis-cli DEL agenda:login:user:<login>` (e, se for o IP, `agenda:login:ip:<ip>`; `redis-cli --scan --pattern 'agenda:login:*'` lista). Sem Redis, reiniciar o `app` zera os contadores. |
| Quem mudou uma reserva? | Na reserva, aba *Histórico* (lista todos os campos que mudaram); ou *Administração → Auditoria* (filtros por dia, pessoa e ação). |
| Backup | Diário e automático (README, seção 6). **Teste uma restauração por mês** num banco separado, sem parar o sistema (README, seção 6, "Teste de restauração (não afeta produção)"). |
| Atualizar o sistema | Backup, `docker tag agenda-salas:latest agenda-salas:anterior` e depois `docker compose build --pull && docker compose up -d` (as migrations rodam sozinhas). README, seção 6. |
| Monitorar | `GET /api/health` (200 `"ok"` = tudo certo; 200 `"degraded"` com `"redis":"down"` = Redis fora, REST funcionando; 503 = banco fora do ar), `docker compose ps` (app *healthy*), `docker compose logs -f app`. |

## 4. Solução de problemas

| Sintoma | Causa provável | O que fazer |
| --- | --- | --- |
| Indicador no topo em **Reconectando…** ou **Offline** (WebSocket desconectado) | Rede instável, servidor reiniciando, ou proxy sem suporte a WebSocket | O navegador tenta de novo sozinho (até ~10 s entre tentativas) e recarrega salas e semana ao voltar. Se não volta: `/api/health` responde? O proxy repassa `Upgrade`/`Connection` no `/ws` (README, seção 7)? No IIS, o recurso *Protocolo WebSocket* está instalado? Enquanto isso, a tela funciona, mas as colegas não veem a seleção ("Sem tempo real" no modal). |
| Login com **"Muitas tentativas de entrada sem sucesso. Tente novamente em N minuto(s)."** (HTTP 429, botão *Entrar* desabilitado com contagem regressiva) | 5 senhas erradas seguidas para o mesmo usuário **ou** a partir do mesmo IP (1 min; depois 5 e 15 min) | Esperar o tempo mostrado. Para liberar antes, ver *Alguém bloqueado por tentativas de login* (seção 3). Muitas pessoas bloqueadas ao mesmo tempo atrás do mesmo IP: alguém está errando muito ou tentando adivinhar senhas — veja `docker compose logs app`. Se **todo mundo** aparece com o mesmo IP (o do proxy), o `X-Forwarded-For` não está chegando: confira o proxy e o `FORWARDED_ALLOW_IPS` (README, seção 7). |
| Volta para o login com **"Sua sessão expirou. Entre novamente."** | Sessão de 12 h vencida (`AGENDA_JWT_EXPIRE_MINUTES`) ou `AGENDA_JWT_SECRET` trocado | Entrar de novo. É o comportamento esperado. |
| Volta para o login com **"Sua sessão foi encerrada porque a senha foi alterada. Entre novamente."** (401 `SESSION_REVOKED`) | A senha da pessoa foi trocada (por ela em outro computador, pelo administrador ou por `reset-password`), ou a conta foi desativada e reativada | Entrar com a senha nova. Se ninguém trocou a senha, investigue (auditoria de usuários, logs) e redefina-a. |
| Volta para o login com **"Seu acesso foi desativado. Fale com o administrador."** (401 `ACCOUNT_DISABLED` / WebSocket 4403) | A conta foi desativada em *Administração → Usuários* | Se foi engano, reative a conta; a pessoa entra de novo. Na tela de login, conta desativada aparece como "Usuário ou senha inválidos". |
| **Conectado**, mas as alterações das colegas não aparecem | Vários processos sem Redis (cada um com sua memória) | `/api/health` deve mostrar `"realtime":"redis"` quando há mais de um processo. No Docker o entrypoint já força 1 processo sem Redis; fora dele, use `AGENDA_REDIS_URL` ou um processo só. |
| `/api/health` com **`"status":"degraded"` e `"redis":"down"`** (HTTP 200) / **Redis fora do ar** | Container `redis` parado, falta de memória | O REST continua funcionando (o banco impede conflitos); novas seleções e gravações podem falhar com erro, e o limite de login passa para a memória de cada processo. `docker compose ps redis`, `docker compose logs redis`, `docker compose restart redis`. A aplicação reconecta sozinha e, quando o Redis volta, fecha os WebSockets (código 1012) para que os navegadores reconectem e recarreguem a grade (eventos do intervalo podem ter se perdido). Nada se perde de fato: as reservas estão no PostgreSQL, o Redis só guarda travas temporárias. Na **subida**, o `app` não inicia sem o Redis configurado (log: `Redis inacessível em redis://...`; o container reinicia até ele voltar). |
| **"Reservando: Fulana"** não sai | Fulana está com o modal aberto (a trava é renovada a cada ~40 s) ou o navegador dela travou/perdeu a rede sem fechar a conexão | Peça para ela fechar o modal (`Esc`). Fechar a aba libera na hora; uma queda silenciosa da rede (cabo, Wi-Fi, notebook que dormiu) é percebida em até ~40 s (*ping* do WebSocket); no pior caso a trava **vence sozinha em até 120 s** (`AGENDA_LOCK_TTL_SECONDS`) mais ~2 s da varredura. Reiniciar o `redis` (ou o `app`, sem Redis) apaga todas as travas. |
| "Horário ocupado" / conflito ao salvar | Outra pessoa salvou antes no mesmo horário (o banco nunca aceita duas) | A grade já mostra a reserva da colega. Use as salas livres sugeridas no próprio aviso. |
| "Esta reserva foi alterada por outra pessoa" | Duas pessoas editaram a mesma reserva | Os dados foram recarregados; confira e refaça a alteração. |
| Desativar sala: **"A sala tem N reservas ativas que ainda não aconteceram…"** (409 `ROOM_HAS_FUTURE_BOOKINGS`) | A sala ainda tem reservas confirmadas ou com check-in que não terminaram | Nada foi alterado. Use os links **Abrir na grade** da lista para mover cada reserva para outra sala (ou cancelar) e salve de novo (seção 3, *Desativar uma sala*). Não há como forçar. |
| `app` **reinicia sem parar** (`docker compose ps` mostra *Restarting (1)*; `/api/health` não responde) | O `init-db` ou a configuração falhou antes do uvicorn subir: banco inacessível ou **senha do banco errada** (vale a `POSTGRES_PASSWORD` da **primeira** subida do volume; trocá-la no `.env` depois não muda o banco), senha do administrador inicial recusada, `AGENDA_JWT_SECRET` com menos de 32 caracteres, Redis configurado e inacessível | `docker compose logs --tail 50 app` mostra o motivo (nas falhas do `init-db`, logo antes da linha `init-db falhou (...)`): `não foi possível acessar o banco de dados (...)` → `docker compose ps db`, volte a `POSTGRES_PASSWORD` original no `.env` (ou mude a senha no banco: `docker compose exec db psql -U agenda -d agenda -c "ALTER USER agenda PASSWORD '...'"`); `administrador inicial não criado: ...` → corrija `AGENDA_BOOTSTRAP_ADMIN_PASSWORD` ou deixe-o vazio (README, seção 4); `AGENDA_JWT_SECRET muito curto` → gere um com 32+ caracteres; `Redis inacessível` → linha do Redis acima. |
| `app` **unhealthy** / `/api/health` responde 503 | Banco caiu com a aplicação já rodando: PostgreSQL parado, sem disco | `docker compose ps db`, `docker compose logs db`, espaço em disco (`df -h`). A aplicação volta sozinha quando o banco voltar. |
| Importação recusa o arquivo | Não é `.xlsx`, maior que 10 MB (HTTP 413/422 `FILE_TOO_LARGE`), ou o proxy limita o upload | Salve como `.xlsx`; remova abas antigas; no nginx, `client_max_body_size 12m`. |
| **Avisos na importação** | Planilha fora do padrão | Veja IMPORTACAO.md, seção 5. Resumo: *sala não encontrada* → cadastre ou padronize o nome; *associada por semelhança* → confira se é a sala certa; *conflito* → resolva na planilha (senão a reserva é pulada); *data não identificada* → renomeie a aba (`Seg 26-10`); *horário fora de ordem* / *dia da semana não confere* / *célula mostra outra data* → corrija a aba; *"Fulana está reservando este horário agora na grade — pulada"* → espere a seleção terminar (até 2 min) e importe de novo; *"já importada anteriormente (pode ter sido movida ou cancelada no sistema)"* → esperado numa reimportação, nada a fazer. Os demais avisos são informativos. |
| Import pela linha de comando não aparece nas telas abertas | Instalação sem Redis | Sem Redis a CLI não tem como avisar o servidor (nem enxergar as seleções em andamento): as recepcionistas precisam recarregar a página (F5). Pela tela, a atualização é imediata. |
| `export-xlsx` termina com **"não foi possível gravar a planilha em ..."** | A pasta de destino não aceita gravação (no Docker, só `/tmp` aceita) | Use `-o` com uma pasta gravável: no Docker `-o /tmp/semana.xlsx` e depois `docker compose cp app:/tmp/semana.xlsx .`. |
| Número do chamado sem link | `AGENDA_TICKET_URL_TEMPLATE` vazio | Configure (`https://chamados.empresa.com.br/ticket/{ticket}`) e reinicie o `app`. |
| Grade com horário ou dias errados | Configuração de calendário | `AGENDA_TIMEZONE`, `AGENDA_DAY_START`/`AGENDA_DAY_END`, `AGENDA_WORK_DAYS` (README, seção 5). |

## 5. Plano de retorno (rollback)

### 5.1 Voltar para a planilha

Use só se o sistema ficar **indisponível em horário comercial sem previsão de volta** (ou se houver
perda de dados) — problemas pontuais se resolvem pela seção 4.

1. **Avise a equipe**: a partir de agora, ninguém agenda no sistema.
2. **Exporte a agenda futura**, se o sistema ainda responder: *Exportar → Semana* para a semana
   atual e as próximas, ou pela linha de comando, para cada semana:
   - **Docker:**
     ```bash
     docker compose exec -T app python -m app.cli export-xlsx --week --date 2026-10-26 -o /tmp/semana-2026-10-26.xlsx
     docker compose cp app:/tmp/semana-2026-10-26.xlsx .
     ```
   - **Windows:**
     ```powershell
     .\.venv\Scripts\python -m app.cli export-xlsx --week --date 2026-10-26 -o "C:\Retorno\semana-2026-10-26.xlsx"
     ```
   Se o sistema não responder, use a última exportação diária (2.4) e, se preciso, um backup do
   banco restaurado num banco separado (README, seção 6, "Teste de restauração") ou em outra
   máquina, de onde se exporta do mesmo jeito.
3. **Reabra a planilha para edição** (desfaça o passo 2.3) e acrescente o que foi agendado no
   sistema desde a virada, a partir das exportações — elas têm o mesmo formato (uma aba por dia,
   salas em colunas). Outra opção é usar o próprio arquivo exportado como planilha temporária.
4. Registre o motivo, o horário e o que foi feito.

**Para voltar ao sistema** depois de corrigido: importe a planilha de novo (seção 2.2). Só as
células **novas** entram: o que já tinha sido importado ou exportado pelo sistema não é recriado
(nem o que foi cancelado ou remanejado no sistema), mas **cancelamentos e mudanças feitos na
planilha durante o retorno não são aplicados automaticamente** — uma célula alterada entra como
reserva nova (ou conflita com a antiga). Compare os dias afetados e ajuste no sistema antes de
bloquear a planilha outra vez.

### 5.2 Desfazer uma atualização de versão

As migrations só andam para frente: voltar a versão anterior é **restaurar o backup feito antes da
atualização num banco recriado vazio** e subir a **imagem anterior** guardada antes do build
(README, seção 6, "Atualização de versão"). Tudo o que foi agendado entre a atualização e o retorno
se perde com a restauração: exporte as semanas afetadas antes (passo 2 da seção 5.1) para
recadastrar.

```bash
# 1. guarde o estado atual (dá para voltar atrás) e pare a aplicação
docker compose exec -T db pg_dump -U agenda -d agenda -Fc -f /tmp/agenda-atual.dump
docker compose cp db:/tmp/agenda-atual.dump ./agenda-versao-nova.dump
docker compose stop app

# 2. recrie o banco vazio e restaure o backup de antes da atualização (tudo ou nada)
docker compose exec -T db dropdb -U agenda --force agenda
docker compose exec -T db createdb -U agenda agenda
docker compose cp ./agenda-antes-da-atualizacao.dump db:/tmp/agenda.dump
docker compose exec -T db pg_restore -U agenda -d agenda --no-owner --single-transaction /tmp/agenda.dump
docker compose exec -T db psql -U agenda -d agenda -c "select version_num from alembic_version"

# 3. volte para a imagem anterior, sem build, e confira
docker tag agenda-salas:anterior agenda-salas:latest
docker compose up -d --no-build --force-recreate app
docker compose exec app python -m alembic current      # mesma versão do passo 2, com "(head)"
```

- O `version_num` do passo 2 precisa ser o da versão anterior (ex.: `0001_initial` ao voltar de
  uma versão que trouxe a `0002_token_version_import_key`). Se o `pg_restore` falhar, nada foi
  misturado (o banco fica vazio): corrija e repita o passo 2.
- No passo 3, o `alembic current` deve terminar com `(head)`: a imagem antiga conhece exatamente as
  migrations do banco restaurado. Se a imagem fosse a nova, o entrypoint aplicaria as migrations
  novas de novo.
- Volte também o **código da pasta** para a versão anterior (`git checkout <versão>` ou a cópia
  guardada), para que um `docker compose build` futuro não traga a versão nova de volta.
- Sem a imagem `agenda-salas:anterior` (esqueceram o `docker tag`): volte o código da pasta e
  rode `docker compose up -d --build`. As bibliotecas Python saem nas versões de
  `backend/constraints.txt` daquela versão; as imagens base (`python:3.13-slim`, `node:24-alpine`)
  podem vir com correções mais novas.
- **Windows sem Docker:** pare o serviço, recrie o banco e restaure com os mesmos `dropdb`,
  `createdb`, `pg_restore` e `psql` da instalação do PostgreSQL (`-h servidor -U usuário`), volte a
  pasta do código, rode `.\.venv\Scripts\python -m pip install -r requirements.txt -c
  constraints.txt` e o `npm ci; npm run build` daquela versão e inicie o serviço.
