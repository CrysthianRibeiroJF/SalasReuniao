O planejamento técnico e funcional abaixo estrutura o desenvolvimento do **Sistema de Agendamento de Salas em Tempo Real**, desenhado para substituir a planilha atual sem perda de agilidade operacional e garantindo segurança contra conflitos de horário entre as recepcionistas.

---

## 1. Escopo e Objetivos do Projeto

* **Objetivo Principal:** Substituir a planilha compartilhada (modelo de uma aba por dia) por uma aplicação web sincronizada em tempo real, mantendo a familiaridade visual da grade de horários e integrando o fluxo ao sistema de chamados.
* **Usuários-Alvo:** Equipe de recepcionistas (operação simultânea de leitura, criação, edição e remanejamento) e administração/TI (cadastro de salas e manutenção).
* **Problemas Resolvidos:** Sobreposição de reservas (*double booking*), falta de atualização automática na tela, uso ineficiente de salas de videoconferência, trabalho manual para reservas de dia inteiro e desconexão com os números de chamado.

---

## 2. Levantamento de Requisitos

### Requisitos Funcionais (RF)

| Código | Requisito | Descrição Detalhada |
| --- | --- | --- |
| **RF01** | **Gestão de Tipos de Sala** | Cadastro e agrupamento visual das salas em duas categorias: **Videoconferência** e **Normal**, incluindo capacidade máxima e recursos fixos. |
| **RF02** | **Status Técnico da Sala** | Permitir marcar uma sala de Videoconferência como *"Vídeo em Manutenção"* (vinculando o chamado de TI), tratando-a temporariamente como sala Normal na grade. |
| **RF03** | **Grade Diária em Abas** | Exibição das salas vs. horários do dia (ex: 08h às 19h) com barra de abas inferiores/superiores para alternar entre os dias da semana com 1 clique, espelhando a planilha atual. |
| **RF04** | **Reserva Horária e Dia Inteiro** | Agendamento por faixa de horário específica ou com botão de 1 clique para **Dia Inteiro**, **Manhã Toda** ou **Tarde Toda** (mesclando visualmente o período na grade). |
| **RF05** | **Vínculo com Chamados** | Campo dedicado ao **Nº do Chamado** na reserva, gerando link clicável direto para o sistema de chamados e permitindo busca rápida pelo número do ticket. |
| **RF06** | **Otimização de Videoconferência** | Flag *"Utilizará Videoconferência?"* na reserva. Se marcada como "Não" em uma sala de vídeo, o sistema alerta caso haja salas normais livres e destaca a reunião na grade como candidata a remanejamento. |
| **RF07** | **Histórico e Auditoria** | Registro automático de quem criou, alterou, moveu ou cancelou cada reserva, com data e hora exatas. |
| **RF08** | **Importação e Exportação Excel** | Rotina para importar as abas da planilha atual na virada do sistema e botão para exportar a grade do dia/semana em formato `.xlsx`. |

### Requisitos Não-Funcionais (RNF)

* **RNF01 — Sincronização em Tempo Real:** Qualquer alteração na grade ou seleção de célula deve refletir nas telas de todas as recepcionistas conectadas em menos de 200 milissegundos via WebSockets.
* **RNF02 — Consistência Atômica:** O banco de dados deve impedir fisicamente duas reservas na mesma sala com interseção de horário, mesmo em requisições disparadas no mesmo milissegundo.
* **RNF03 — Alta Produtividade (Regra dos 3 Cliques):** Uma recepcionista deve conseguir criar uma reserva padrão (célula + chamado + responsável + salvar) em no máximo 3 cliques ou apenas pelo teclado.

---

## 3. Arquitetura e Stack Tecnológica

| Camada | Tecnologia | Papel no Sistema |
| --- | --- | --- |
| **Backend / API** | **Python + FastAPI** | API REST para operações CRUD e servidor nativo de **WebSockets** (`async/await`) para transmissão instantânea de eventos de tela. |
| **Banco de Dados** | **PostgreSQL (15+)** | Persistência relacional e validação nativa de conflito de intervalos de tempo através da extensão `btree_gist` e tipo `tstzrange`. |
| **Cache / Mensageria** | **Redis** | Gerenciamento de *Soft Locks* (bloqueio temporário de célula com expiração automática de 120 segundos) e *Pub/Sub* para sincronizar múltiplas instâncias do backend. |
| **Frontend** | **React (ou Vue.js) + Tailwind CSS** | Interface reativa de página única (SPA) com suporte a *drag-and-drop*, atalhos de teclado e atualização parcial de células sem recarregar a página. |
| **Manipulação de Planilhas** | **Python (`openpyxl` / `pandas`)** | Motor para leitura das abas diárias da planilha legada e geração de relatórios espelhados em Excel. |

---

## 4. Modelagem do Banco de Dados

### 4.1. Tabela de Salas (`rooms`)

* `id` (UUID / Serial, PK)
* `name` (VARCHAR) — Ex: *"Sala 01 - Executiva"*
* `room_type` (ENUM: `VIDEO`, `NORMAL`) — Tipo original da sala
* `capacity` (INT) — Quantidade de lugares
* `video_status` (ENUM: `OPERATIONAL`, `MAINTENANCE`) — Estado do equipamento de vídeo
* `maintenance_ticket_id` (VARCHAR, Nullable) — Nº do chamado de TI caso o vídeo esteja quebrado
* `display_order` (INT) — Ordem fixa da coluna/linha na grade (igual à planilha)
* `is_active` (BOOLEAN)

### 4.2. Tabela de Agendamentos (`bookings`)

* `id` (UUID / Serial, PK)
* `room_id` (FK -> `rooms.id`)
* `ticket_number` (VARCHAR, Indexado) — Nº do chamado da solicitação
* `title` (VARCHAR) — Assunto ou área solicitante
* `requester_name` (VARCHAR) — Nome do responsável pela reunião
* `start_time` (TIMESTAMPTZ) — Data e hora de início
* `end_time` (TIMESTAMPTZ) — Data e hora de término
* `is_full_day` (BOOLEAN) — Indica se foi marcada como Dia Inteiro
* `requires_video` (BOOLEAN) — Indica se a reunião realmente usará o equipamento de vídeo
* `status` (ENUM: `CONFIRMED`, `CHECKED_IN`, `CANCELLED`, `NO_SHOW`)
* `created_by` (FK -> `users.id`) — Recepcionista que agendou
* `updated_by` (FK -> `users.id`) — Última recepcionista que editou
* **Regra de Integridade no PostgreSQL:** Constraint `EXCLUDE USING gist (room_id WITH =, tstzrange(start_time, end_time, '[)') WITH &&) WHERE (status != 'CANCELLED')`.

### 4.3. Tabela de Auditoria (`booking_logs`)

* `id` (UUID / Serial, PK)
* `booking_id` (FK -> `bookings.id`)
* `user_id` (FK -> `users.id`)
* `action` (ENUM: `CREATED`, `MOVED_ROOM`, `RESCHEDULED`, `CANCELLED`)
* `previous_state` (JSONB) — Dados antes da mudança
* `new_state` (JSONB) — Dados após a mudança
* `created_at` (TIMESTAMPTZ)

---

## 5. Regras de Negócio e Fluxos Críticos

### 5.1. Fluxo de Trabalho Simultâneo (Trava em Duas Etapas)

1. **Bloqueio Visual Temporário (Soft Lock):** Quando a Recepcionista A clica em um horário vazio (ou arrasta uma faixa de horários) na Sala 02, o frontend envia um evento WebSocket `LOCK_SLOT`. O Redis cria uma chave `lock:room_2:2026-10-23:14:00` com validade (TTL) de 2 minutos. Todas as outras recepcionistas veem imediatamente aquele espaço sombreado com o texto *"Reservando: [Nome da Recepcionista A]"*.
2. **Gravação Definitiva (Hard Lock):** Ao clicar em "Salvar", o backend valida os dados, grava no PostgreSQL (protegido pela constraint GiST), remove a chave de bloqueio do Redis e emite o evento `BOOKING_CREATED` para atualizar a grade de todas as recepcionistas.
3. **Abandono de Tela:** Se a Recepcionista A fechar o modal, clicar em "Cancelar" ou perder a conexão, o bloqueio é liberado imediatamente (ou expira sozinho após os 2 minutos).

### 5.2. Regra de Reserva de Dia Inteiro

* Ao marcar a opção **`Dia Inteiro`** no formulário, o sistema preenche automaticamente `start_time` com o horário de abertura da recepção (ex: `08:00`) e `end_time` com o fechamento (ex: `19:00`), definindo `is_full_day = True`.
* Caso já exista uma reunião de 1 hora marcada às 15h naquela sala, o sistema bloqueia a ação de Dia Inteiro imediatamente e oferece duas opções à recepcionista:
1. Escolher outra sala livre o dia todo;
2. Remanejar primeiro a reunião das 15h para outra sala.



### 5.3. Gestão Inteligente de Videoconferência vs. Sala Normal

* **Priorização:** Salas do tipo `VIDEO` possuem destaque visual no cabeçalho.
* **Marcação de Empréstimo:** Se uma reunião sem necessidade de vídeo (`requires_video = False`) for alocada em uma sala `VIDEO`, o bloco da reserva recebe um selo discreto (`Reunião Presencial — Remanejável`). Quando uma recepcionista precisar urgente de uma sala de vídeo e todas estiverem cheias, ela bate o olho na grade, identifica esse selo e arrasta a reunião presencial para uma sala `NORMAL` vaga.

---

## 6. Estrutura de Interface (UX/UI)

A tela principal será dividida em **4 zonas fixas** (sem rolagem desnecessária):

1. **Barra Superior (Filtros e Busca Rápida):**
* Campo de busca instantânea por **Nº do Chamado** ou **Nome do Solicitante** (ao digitar, leva direto para a aba do dia correspondente e destaca a sala).
* Filtros de 1 clique: `Todas as Salas` | `Videoconferência` | `Salas Normais` | `Livres Dia Inteiro`.
* Indicador de recepcionistas online no momento (avatares no canto superior direito).


2. **Grade Central (Espelho da Planilha):**
* Colunas agrupadas: Bloco esquerdo com **Salas de Videoconferência**, bloco direito com **Salas Normais**.
* Linhas de horário de 30 em 30 minutos (ou 1 em 1 hora), com suporte a seleção de múltiplas células e mesclagem automática para reservas longas ou de Dia Inteiro.


3. **Modal Rápido / Popover de Agendamento:**
* Campos essenciais no topo: `Nº do Chamado`, `Solicitante/Assunto`, `Tipo de Período` (`Horário Específico` | `Dia Inteiro` | `Manhã` | `Tarde`) e switch `Precisa de Videoconferência?`.


4. **Barra Inferior de Abas Diárias (Estilo Excel):**
* Abas clicáveis por dia (`Seg 26/10`, `Ter 27/10`, `Qua 28/10`, `Qui 29/10`, `Sex 30/10`, `+ Calendário`) para navegação idêntica à planilha atual.



---

## 7. Cronograma de Execução (Roadmap em 4 Fases)

| Fase | Foco Principal | Entregáveis Técnicos | Critério de Pronto (DoD) |
| --- | --- | --- | --- |
| **Fase 1: Fundação e Banco** | Modelagem e API Base | Configuração do PostgreSQL com `btree_gist`, tabelas `rooms`, `bookings` e `users`, endpoints REST em FastAPI para criar/listar/editar reservas e validação de conflitos. | Duas requisições simultâneas para a mesma sala/horário resultam em apenas 1 gravação e 1 erro tratado. |
| **Fase 2: Grade Web e Dia Inteiro** | Interface Estilo Planilha | Construção da tela com abas diárias, divisão visual entre salas de Videoconferência e Normais, agendamento rápido, botão de **Dia Inteiro** e vínculo com o **Nº do Chamado**. | Recepcionista consegue navegar entre os dias pelas abas e reservar horários parciais ou dia inteiro. |
| **Fase 3: Tempo Real (WebSockets)** | Trabalho Simultâneo | Integração com Redis e WebSockets no FastAPI + Frontend para atualização instantânea da grade e exibição do *Soft Lock* (*"Sendo reservado por..."*). | Ações feitas na Aba A do navegador aparecem em tempo real na Aba B sem recarregar a página. |
| **Fase 4: Migração e Refinamento** | Planilha, Auditoria e Manutenção | Script Python de importação da planilha atual, exportação `.xlsx`, status de "Vídeo em Manutenção", alerta de remanejamento e log de auditoria. | Planilha atual importada com sucesso e recepcionistas operando 100% pelo sistema. |

---

## 8. Plano de Transição (Da Planilha para o Sistema)

1. **Padronização da Planilha Atual:** 2 dias antes da virada.
Garantir que os nomes das salas nas colunas da planilha atual correspondam exatamente aos nomes cadastrados na tabela `rooms` do novo banco de dados.


2. **Importação Automatizada das Abas Futuras:** Fora do horário comercial.
Executar o script de migração em Python para ler todas as abas de datas futuras da planilha (identificando células mescladas como reservas de múltiplas horas ou Dia Inteiro) e popular o PostgreSQL.


3. **Bloqueio da Planilha para Somente Leitura:** Manhã do Go-Live.
Mudar a permissão da planilha antiga para "Somente Visualização" para evitar que alguém agende no arquivo antigo por hábito, colocando um link no topo da planilha apontando para o novo sistema.


4. **Operação Assistida com Exportação Diária:** Primeira semana.
Manter ativo o botão de exportar o dia para Excel caso alguma recepcionista ou gestor queira salvar um backup visual enquanto a equipe ganha confiança na nova ferramenta.
