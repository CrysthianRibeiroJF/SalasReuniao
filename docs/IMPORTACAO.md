# Importação e exportação da planilha (RF08)

Este documento explica como trazer as abas da planilha atual para o sistema na virada, como ler o
relatório da importação e como funciona a exportação para Excel. As regras técnicas estão no
contrato (`docs/CONTRATO.md`, seções 5.6 e 8). O código fica em `backend/app/excel/`.

A importação é **tolerante**: como cada pessoa preenche a planilha de um jeito, ela tenta entender
o que está escrito e **avisa** tudo o que precisou deduzir ou ignorar. Por isso o fluxo é sempre
o mesmo: **simular → conferir o relatório → corrigir a planilha ou o cadastro → simular de novo →
importar de verdade**.

---

## 1. Checklist da virada

Os passos seguem o plano de transição (`docs/PLANEJAMENTO.md`, seção 8).

| Quando | O que fazer | Como conferir |
| --- | --- | --- |
| **2 dias antes** | Padronizar os nomes das salas: cada coluna da planilha deve ter o mesmo nome da sala cadastrada em *Admin → Salas* (maiúsculas, acentos, espaços extras e o prefixo "Sala" não importam; veja a seção 3.3). Cadastrar as salas que faltarem. | Rodar a **simulação**. A lista **"Salas não encontradas no cadastro"** precisa ficar vazia. Avisos "associada por semelhança" devem ser conferidos um a um. |
| **2 dias antes** | Corrigir na planilha o que a simulação apontar: abas sem data reconhecível, horários fora de ordem, conflitos entre reservas. | Simular de novo até sobrarem só avisos esperados. |
| **Fora do horário comercial** (véspera ou madrugada do go-live) | Fazer a **importação real** das abas de hoje em diante. Ninguém deve estar agendando na planilha nem no sistema nesse momento. | Relatório "IMPORTAÇÃO CONCLUÍDA" com o total esperado de reservas e sem erros. Conferir alguns dias na grade. |
| **Manhã do go-live** | Mudar a permissão da planilha antiga para **somente leitura** e colocar no topo dela um link para o novo sistema, para que ninguém agende no arquivo antigo por hábito. | Tentar editar a planilha com uma conta de recepção: não deve ser possível. |
| **Primeira semana** | Manter a **exportação diária** (botão *Exportar dia* na grade ou o comando `export-xlsx`) como backup visual para quem quiser. | Um arquivo `agenda-AAAA-MM-DD.xlsx` por dia, salvo onde a equipe combinar. |

> Reimportar a mesma planilha **não duplica** nada e **não ressuscita** nada (seção 4.3): cada
> reserva importada guarda a marca da célula de onde veio, então uma reunião que foi movida,
> remarcada, editada ou cancelada no sistema depois da importação não volta. Se a planilha ainda
> recebeu reservas depois da primeira importação, basta importar de novo: só as células novas
> (ou alteradas na planilha) entram — confira a simulação antes.

---

## 2. Como rodar

### 2.1 Pela tela (administradores)

*Admin → Importar planilha*: escolha o arquivo `.xlsx` (até 10 MB), clique em **Simular**, confira
o relatório e só então clique em **Importar de verdade**. A opção *Incluir datas passadas* também
importa as abas de dias que já passaram (normalmente não é necessário).

### 2.2 Pela linha de comando (de dentro de `backend/`)

```bash
# simulação (padrão): não grava nada
.venv/Scripts/python -m app.cli import-xlsx "C:\caminho\Agenda 2026.xlsx"

# importação real
.venv/Scripts/python -m app.cli import-xlsx "C:\caminho\Agenda 2026.xlsx" --commit

# opções
#   --include-past   importa também abas com datas anteriores a hoje
#   --user ana       registra "ana" como autora das reservas (padrão: primeiro admin ativo)
```

O comando imprime o mesmo relatório da tela e termina com código 1 se houver **erros** (arquivo
ilegível, falha de banco), o que permite usá-lo em scripts.

> **Telas abertas e Redis.** Com Redis configurado (`AGENDA_REDIS_URL`, padrão do Docker
> Compose), a CLI usa o mesmo Redis das instâncias do servidor: no fim de um `--commit` ela
> repassa o evento `BOOKINGS_IMPORTED` (e imprime `Servidor avisado (N instância(s))`), e as
> grades abertas se atualizam sozinhas; durante a leitura ela também confere as **travas de
> seleção** das telas abertas (seção 4.2). **Sem Redis** cada servidor guarda as travas e as
> conexões na própria memória: a CLI não consegue avisar ninguém (quem estiver com a grade
> aberta precisa recarregar a página, F5) nem enxergar as travas de seleção — prefira a
> importação pela tela nesse caso, que faz as duas coisas.

### 2.3 Pela API

`POST /api/import/xlsx?dry_run=true&include_past=false` (somente administradores), multipart com o
campo `file`. Devolve o `ImportReport` descrito na seção 5. Arquivo que não termina em `.xlsx`,
maior que 10 MB ou ilegível → HTTP 422 com `code` `INVALID_FILE_TYPE`, `FILE_TOO_LARGE` ou
`INVALID_FILE` e mensagem em português (um envio muito acima do limite é recusado antes de ser
lido, com HTTP 413 `FILE_TOO_LARGE`). Um `.xlsx` válido mas inflado demais (seção 8) também é
`INVALID_FILE`, com a mensagem explicando o limite.

---

## 3. Formato esperado da planilha legada

Exemplo de uma aba (o arquivo `docs/exemplos/planilha-legada-exemplo.xlsx` tem vários casos):

```
      A          B                     C                     D                  ...
1  AGENDA DE SALAS - SEGUNDA-FEIRA 26/10/2026                                        ← título (opcional)
2  Horário   Sala 01 - Executiva   Sala 02 - Diretoria   Sala 05 - Reunião      ← cabeçalho
3  08:00                           ┌───────────────────┐
4  08:30                           │ INC0048250        │                         ← célula mesclada
5  09:00     Chamado 48211         │ Fernanda Costa    │                           C3:C10 = 08:00–12:00
6  09:30     Carla Mendes          │ Planejamento [VC] │                           (Manhã Toda)
...
```

### 3.1 Uma aba por dia

Cada aba é um dia. Abas sem data reconhecível (ex.: "Resumo", "LEIA-ME") são ignoradas com a
situação **SKIPPED_UNRECOGNIZED**. Duas abas com a mesma data são lidas normalmente; o que for
repetido ou conflitante entre elas é pulado com aviso.

**Abas ocultas** (Exibir/Ocultar do Excel, inclusive "muito ocultas") são ignoradas, também como
**SKIPPED_UNRECOGNIZED**, com o aviso `Aba oculta na planilha: ignorada. Se as reservas dela
valem, reexiba a aba no Excel e importe de novo.` — é comum esconder uma cópia antiga em vez de
apagá-la, e ela não pode ganhar conflitos das reservas visíveis. Abas grandes demais para serem
uma agenda diária (seção 8) são ignoradas da mesma forma, com aviso.

### 3.2 Data da aba

A data vem do **nome da aba**; se o nome não tiver data, de uma **célula de data no topo** da aba
(acima da grade: um valor de data do Excel ou um texto como "Segunda-feira, 26/10/2026").

Formatos aceitos no nome (com ou sem dia da semana na frente, maiúsculas/acentos indiferentes):

| Exemplo | Data |
| --- | --- |
| `26-10`, `26.10`, `26_10`, `26/10` | 26/10 (ano deduzido) |
| `26-10-2026`, `26.10.26` | 26/10/2026 |
| `Seg 26-10`, `SEG - 26.10`, `segunda 26.10.26`, `Segunda-feira 26-10` | 26/10 |
| `2026-10-26` | 26/10/2026 |
| `26 out`, `26-out`, `26 de outubro de 2026` | 26/10 |

Regras:

- **Ano ausente**: usa o ano que deixa a data mais perto de hoje (em dezembro, `04-01` é janeiro do
  ano seguinte). Se o nome tiver dia da semana, ele desempata entre anos possíveis a até ~6 meses de
  hoje. Se uma célula do topo mostrar a mesma data **com** ano, vale o ano da célula.
- **Dia da semana que não bate** com a data (ex.: `Ter 26-10` quando 26/10 é segunda) gera aviso;
  a data do número continua valendo.
- **Título com outra data** (ex.: aba `Seg 26-10` com título "27/10/2026", aba copiada sem
  atualizar o título) gera aviso; vale a data do nome da aba.
- **Data que não existe** perto de hoje (`29-02` sem ano fora dos anos bissextos, ou `31-04`
  com ano) → aba ignorada (**SKIPPED_UNRECOGNIZED**) com o aviso `Data inválida: 29/02 sem ano
  não existe em 2025, 2026 nem 2027; escreva o ano no nome da aba (ex.: 29-02-AAAA).` — a
  importação não é marcada como falha.

### 3.3 Linha de cabeçalho (salas)

É a linha cujas células têm os nomes das salas cadastradas. A comparação ignora maiúsculas,
acentos, pontuação, espaços extras, o prefixo "Sala" e zeros à esquerda:
`SALA 01 - EXECUTIVA`, `sala 1 – executiva` e `01 - Executiva` são a mesma sala
"Sala 01 - Executiva". Também são ignorados no cabeçalho uma segunda linha na célula e textos
entre parênteses ou de capacidade (`Sala 02 - Diretoria (10 pessoas)`, `Sala 01\n12 lugares`).

Associação **por semelhança** (sempre com aviso "confira"), somente quando há um único candidato:

- cabeçalho só com o número (`Sala 01`) → a única sala de número 01;
- cabeçalho só com o nome (`Diretoria`) → a única sala com esse nome;
- cadastro só com o número (`Sala 10`) e cabeçalho com nome (`Sala 10 - Entrevistas`).

Nunca associa pelo número quando os nomes são diferentes (`Sala 1 - Copa` **não** vira
"Sala 01 - Executiva"). Cabeçalhos que não casam com nenhuma sala vão para
**"Salas não encontradas no cadastro"** e as reservas dessas colunas são contadas como puladas
(cabeçalhos longos aparecem cortados em 60 caracteres, mas aparecem).
Uma linha de grupos acima do cabeçalho ("VIDEOCONFERÊNCIA", "SALAS NORMAIS") é ignorada, assim
como colunas auxiliares ("Observações", "Obs", "Notas", "Legenda", "Total") e o que estiver nelas.

Salas com nomes equivalentes no cadastro:

- o **nome exato** (sem diferenciar maiúsculas, acentos e espaços extras) vale primeiro;
- entre salas com o mesmo nome normalizado (ex.: `Sala 5 - Reunião` desativada e
  `Sala 05 - Reunião` ativa), a **ativa** vence — a coluna não é pulada como "sala desativada";
- se duas salas **ativas** têm nomes equivalentes e o cabeçalho não é exatamente um deles, a
  primeira é usada com o aviso `… mais de uma sala ativa tem nome equivalente (…); usada "…" —
  confira.`

Formatos de cabeçalho aceitos:

- **Cabeçalho em duas linhas** (sem horários entre elas): a linha mais perto da grade manda; da
  linha de cima entram as salas reconhecidas e as células mescladas na vertical até a linha de
  baixo (ex.: `Sala 11` em K1:K2 ao lado de uma linha de grupos).
- **Cabeçalho mesclado na horizontal** vale para todas as colunas que cobre. Se a sala ocupa
  **duas subcolunas** (ex.: "Responsável | Assunto" sob o mesmo nome de sala), os textos lado a
  lado nas mesmas linhas formam **uma reserva só** (`Carla Mendes` + `Planejamento 48211` →
  solicitante, assunto e chamado), com o aviso `Coluna B:C (…): o cabeçalho cobre 2 colunas;
  textos lado a lado foram unidos numa reserva só: B4 + C4.`
- **Mais de um bloco** na mesma aba (ex.: manhã em cima e tarde embaixo, cada bloco com seu
  cabeçalho) são aceitos: cada linha de horário usa o cabeçalho mais próximo acima dela. Um
  bloco menor (só algumas salas, só a tarde) é reconhecido quando a linha do cabeçalho tem o
  rótulo "Horário" na coluna de horários ou vem depois de uma linha que não é da grade (linha em
  branco); as reservas dele nunca são lidas com as colunas do bloco de cima.
- **Texto na grade numa coluna sem nome de sala** no cabeçalho não some: vira o aviso
  `N reserva(s) em colunas sem nome de sala no cabeçalho foram ignoradas: D7. Escreva o nome da
  sala no cabeçalho dessas colunas.` e conta como pulada. (Uma segunda coluna de horários, à
  direita, não conta.)

### 3.4 Coluna de horários

É a coluna com mais valores de horário. Formatos aceitos:

| Exemplo | Observação |
| --- | --- |
| `08:00`, `8:00`, `08:00:00` | texto |
| `8h`, `8h30`, `08H30`, `8hs`, `8h30min`, `8.30` | texto |
| `08:00 - 08:30`, `08:00 às 08:30`, `das 8h às 9h` | faixa: o início define a linha, o fim vale para a última linha |
| valor de hora do Excel (`hh:mm`) | inclusive com resíduos como 08:29:59,999 (arredonda para o minuto) |
| número fracionário do dia (`0,375` = 09:00) | células sem formatação |

Regras:

- **Cada linha é um slot.** Uma reserva vai do início da sua primeira linha até o início da linha
  seguinte à última. Na **última linha** da grade, o fim é o início dela + o passo da grade
  (ex.: 18:30 + 30 min = 19:00) ou o fim escrito na faixa (`18:30 - 19:00`).
- **Linhas sem rótulo** entre dois horários seguem o passo da grade: com rótulo de hora mesclado
  sobre duas linhas (`8h` em A3:A4), cada linha vale meia hora. Uma linha "ALMOÇO" entre 12:00 e
  13:00 vira 12:30.
- **Horário digitado errado** (ex.: `19:00` no lugar de `09:00`): a sequência de horários é a
  mais longa possível em ordem crescente, e o rótulo que fica fora dela recebe o horário que a
  posição indica, com o aviso `Linha 6: horário 19:00 parece digitado errado (esperado 09:00);
  usado 09:00.` — um erro de digitação não descarta mais o resto do dia.
- **Horário fora de ordem** sem um horário esperado possível (ex.: repetido depois do último)
  → aviso `… fora de ordem na coluna de horários; linha ignorada.`
- Mais de 100 linhas sem rótulo entre dois horários não são consideradas parte da grade.
- Textos nas colunas de sala **abaixo da grade** (observações, legendas) são ignorados com um
  aviso que lista as células; o mesmo vale para textos em linhas de horário acima do primeiro
  cabeçalho.

### 3.5 Reservas

- Cada **célula com texto** ou **intervalo mesclado com texto** numa coluna de sala é uma reserva.
- **Só cor, sem texto, não é reserva** (preenchimentos usados como marcação visual).
- **Marcação de horário vago** também não é reserva: células só com `LIVRE`, `vago`,
  `disponível` ou só com traços/pontos (`-`, `---`, `...`). Elas aparecem num aviso agregado.
- **Células seguidas com o mesmo texto** na mesma sala formam **uma reserva só** (muita gente
  copia o texto em vez de mesclar). Vale também para um intervalo mesclado seguido de cópias.
  Dois intervalos **mesclados** com o mesmo texto continuam sendo duas reservas, e uma linha vazia
  no meio separa as reservas.
- Bloco que cobre todo o horário de funcionamento (08:00–19:00) → **Dia Inteiro**; exatamente
  08:00–12:00 → **Manhã Toda**; exatamente 13:00–19:00 → **Tarde Toda**; o resto → Horário
  Específico. Um bloco maior que o horário de funcionamento (ex.: grade de 07:00 às 20:00 toda
  mesclada) vira Dia Inteiro, com aviso.
- **Mesclagem sobre várias salas** (ex.: evento usando as salas 05 e 06) → uma reserva em cada
  sala, com aviso.
- **Faixa mesclada que atravessa a coluna de horários** (ex.: "ALMOÇO" ou "FERIADO" na largura
  toda) → tratada como aviso, não vira reserva.
- Reserva fora do horário de funcionamento (ex.: 07:00–08:00) é importada mesmo assim, com aviso.
- Horários fora do padrão de 5 minutos (ex.: 11:07) → pulada com aviso.
- **Linhas e colunas ocultas** são ignoradas (como as abas ocultas), com o aviso
  `N célula(s) com texto em linhas ou colunas ocultas foram ignoradas: C5, B9. Se valem,
  reexiba-as no Excel e importe de novo.` Os rótulos da coluna de horários continuam valendo
  mesmo em linhas ocultas, para as linhas visíveis manterem seus horários.
- **Fórmulas** valem pelo último valor calculado salvo no arquivo. Fórmula **sem** valor salvo
  (arquivo gerado por um programa que não calcula) → aviso `N célula(s) com fórmula sem valor
  calculado foram ignoradas: …. Abra a planilha no Excel e salve de novo para gravar os
  valores.`
- Texto "escondido" sob uma mesclagem (fora da célula do canto, o LibreOffice pode guardá-lo)
  não conta, como no Excel.

### 3.6 Texto da célula

| O que | Como é reconhecido |
| --- | --- |
| **Nº do chamado** | Na ordem: linha `Chamado 48211` / `Ticket: 48211` / `CH-48211`; no meio do texto `Chamado 48211`, `Chamado nº 48211`, `CH-48211`; IDs `INC0012345`, `RITM…`, `REQ…`, `SCTASK…`, `CHG…`; `#48211`; por último uma sequência de 4 ou mais dígitos (`48211`). Um `#` na frente é removido. |
| Não é chamado | números que parecem ano (1990–2099), partes de datas (`26/10/2026`) e de telefones (`99999-1234`), números depois de "ramal", "tel", "sala", "andar"… |
| **Solicitante e assunto** | Texto em várias linhas: 1ª linha = solicitante, 2ª = assunto, demais = observações. Numa linha só: separado por `\|`, ` - ` (hífen **com espaços**), `–`/`—` ou `/` → 1ª parte = solicitante, o resto = assunto. Hífen sem espaços (`Ana-Lúcia`) não separa. |
| Sem solicitante | (ex.: célula só com `48211`) → registrado como **"Não informado"**, com aviso. |
| **Participantes** | `(8 pessoas)`, `12 participantes`, `20 pax` → campo participantes (aviso se passar da capacidade). |
| **Precisa de vídeo** | `vídeo`, `video`, `videoconferência`, `Teams`, `Zoom`, `Meet`, `Webex` (sem diferenciar maiúsculas) ou `VC` **em maiúsculas** (`vc` minúsculo costuma ser "você"). Aviso se a sala não tiver vídeo operacional. |
| **Horário escrito** | `14h15 às 15h45` ou `14:15–15:45` dentro da célula: se coincidir com as linhas ocupadas, vale o horário escrito (permite horários que não caem nos slots). Se for além das linhas, vale o escrito, com aviso; se não tiver nada a ver com as linhas, valem as linhas e o texto vai para as observações, com aviso. `de`/`da`/`das` antes da faixa (`das 8h às 9h`) só contam como palavra própria na mesma linha: o fim de "Tarde", "Qualidade" ou "Unidade" não é engolido. |
| **Selos** `[...]` | `[Dia inteiro]`, `[Manhã toda]`, `[Tarde toda]`, `[Check-in]`, `[Não compareceu]` são removidos do texto. `[Videoconferência]`/`[⚠ Precisa de vídeo]`/`[VC]` ligam o vídeo e `[Presencial — remanejável]`/`[Sem vídeo]` desligam, valendo mais que as palavras do texto. Outros colchetes (`[RH]`) ficam no texto. |

Textos maiores que os campos do sistema são cortados (solicitante 150, assunto 200 caracteres;
o assunto completo vai para as observações), com aviso.

---

## 4. Regras da importação

### 4.1 Datas passadas

Por padrão só entram abas com data **de hoje em diante** (situação **SKIPPED_PAST** para as
outras). Com `--include-past` / *Incluir datas passadas* elas também são lidas.

### 4.2 Conflitos

Conflitos **não abortam** a importação: a reserva conflitante é **pulada** e o aviso diz a aba, a
célula e com quem conflitou:

- com reserva já cadastrada no sistema:
  `Aba "Seg 26-10", B6:B7 (Sala 01 - Executiva, 09:30–10:30): conflito com a reserva já cadastrada de Carla Existente (09:00–10:00, chamado 1111) — pulada.`
- com outra reserva da própria planilha (a que aparece primeiro, na ordem das abas e dos
  horários, fica):
  `Aba "Seg 26-10 (2)", E5:E6 (Sala 07 - Atendimento, 09:00–10:00): conflito com E6:E7 (aba "Seg 26-10"), da própria planilha — pulada.`

- com uma **trava de seleção** (alguém com aquela faixa selecionada na grade agora, inclusive
  quem está importando):
  `Aba "Seg 26-10", B5:B6 (Sala 01 - Executiva, 09:00–10:00): Ana Recepção está reservando este horário agora na grade — pulada.`
  Importe de novo quando a seleção terminar (as travas expiram em 2 minutos).

A simulação faz exatamente as mesmas checagens (inclusive entre linhas da própria planilha e as
travas de seleção daquele momento), então o relatório da simulação é o que a importação real vai
fazer. Se alguém criar uma reserva no sistema entre a checagem e a gravação, o banco recusa e ela
é pulada da mesma forma.

As travas vêm do servidor (importação pela tela/API) ou, na CLI, do Redis (`AGENDA_REDIS_URL`).
**Sem Redis a CLI não vê as travas** das telas abertas (seção 2.2). Se as travas não puderem ser
consultadas (Redis fora do ar), a importação segue com o aviso `Não foi possível consultar as
travas de seleção das telas abertas (tempo real indisponível); as reservas foram conferidas só
contra o banco de dados.`

### 4.3 Importar de novo não duplica nem ressuscita

Duas regras, nesta ordem:

1. **Já existe igual**: reserva ativa com **a mesma sala, início, fim e nº do chamado** (sem
   chamado dos dois lados: mesmo solicitante). Pulada e contada no aviso agregado
   `3 reserva(s) já cadastrada(s) no sistema (mesma sala, horário e chamado) — pulada(s): B5:B6, D4:D25, E7.`
2. **Já foi importada antes**: cada reserva importada guarda uma marca da célula de origem
   (`bookings.import_key` = `v1:` + SHA-256 de *data da aba | sala | início | fim | texto da
   célula normalizado*, 40 caracteres hexadecimais). Se **qualquer** reserva — de qualquer
   status, inclusive cancelada, movida para outra sala/horário ou editada — já tem a marca, a
   célula é pulada com o aviso
   `4 reserva(s) já importada(s) anteriormente (pode ter sido movida ou cancelada no sistema) — pulada(s): …`.
   Assim, o que a recepção cancelou ou remanejou no sistema depois da virada não volta numa
   reimportação.

Consequências: uma célula **alterada na planilha** (texto, horário ou sala) tem outra marca e é
importada como nova (pode conflitar com a antiga, que aparece como conflito); a mesma reunião
copiada para **outro horário ou outra sala** também é nova. A sala entra na marca pelo **nome
cadastrado** (normalizado), não pelo texto do cabeçalho: corrigir a grafia do cabeçalho na
planilha não muda nada, mas **renomear a sala no cadastro** entre duas importações faz as
células dela parecerem novas — evite renomear salas no meio da transição. Reservas importadas
**antes desta versão** não têm marca: para elas só vale a regra 1.

Reservas repetidas dentro da própria planilha (aba duplicada) geram o aviso
`… repetida(s) dentro da própria planilha — pulada(s): …`.

### 4.4 Salas

- Sala **desativada** no cadastro: reservas da coluna são puladas com aviso (reative a sala, se
  for o caso, e importe de novo).
- Sala de vídeo **em manutenção**: as reservas entram normalmente; as que pedem vídeo recebem
  aviso (e aparecem na grade com o selo "Precisa de vídeo").

### 4.5 O que é gravado

Cada reserva é criada pelo mesmo serviço da tela, com histórico **IMPORTED** (auditoria) em nome
do usuário que importou, status *Confirmada*, a marca `import_key` da célula de origem (seção 4.3)
e aceitando horários fora do funcionamento (com aviso); as travas de seleção já foram conferidas
pela importação (seção 4.2). Cada reserva é gravada isoladamente: uma falha não desfaz as
outras. Ao final de uma importação real, as telas abertas recebem o evento
`BOOKINGS_IMPORTED {dates, count}` e recarregam os dias afetados (pela CLI, só com Redis — seção
2.2).

---

## 5. Como ler o relatório

```
SIMULAÇÃO DA IMPORTAÇÃO (nada foi gravado)
==========================================
Aba "LEIA-ME": ignorada (data não reconhecida)
    aviso: Data não identificada: use o nome da aba (ex.: "Seg 26-10", "26.10.2026") ou uma célula com a data no topo.
Aba "Qui 22-10" (22/10/2026): ignorada (data passada)
Aba "Seg 26-10" (26/10/2026): simulada
    reservas: 5 encontradas, 4 importáveis, 1 puladas
    aviso: Coluna L ("Sala 11 - Copa"): sala não cadastrada — 1 reserva(s) ignorada(s): L8:L9.
...
Total: 20 encontradas, 16 importáveis, 4 puladas
Salas não encontradas no cadastro: Sala 11 - Copa
Para gravar de verdade, execute novamente com --commit.
```

Campos do `ImportReport` (mesmos da tela e da API):

| Campo | Significado |
| --- | --- |
| `dry_run` | `true` = simulação (nada gravado). |
| `sheets[]` | Uma linha por aba, na ordem do arquivo. |
| `sheets[].status` | `DRY_RUN` (simulada), `IMPORTED` (importada), `SKIPPED_PAST` (data passada), `SKIPPED_UNRECOGNIZED` (sem data ou data inválida, sem coluna de horários, sem cabeçalho de salas, aba oculta ou grande demais — o aviso diz qual), `ERROR` (conteúdo corrompido ou erro inesperado ao ler a aba). |
| `sheets[].bookings_found` | Reservas encontradas na aba (inclusive em colunas de salas não cadastradas). |
| `sheets[].bookings_imported` | Gravadas — ou, na simulação, **as que seriam gravadas**. |
| `sheets[].bookings_skipped` | Puladas (conflito, trava de seleção, já existente, já importada antes, repetida, sala desconhecida/desativada, coluna sem sala, horário inválido). Sempre `encontradas = importadas + puladas`. |
| `sheets[].warnings` | Avisos em português com a célula (`B6:B7`) e a sala/horário entendidos. |
| `total_*` | Soma das abas. |
| `unknown_rooms` | Cabeçalhos que não casaram com nenhuma sala cadastrada (padronize os nomes ou cadastre as salas). |
| `errors` | Problemas que impedem a importação de algo: arquivo ilegível, falha de banco ao gravar. A CLI termina com código 1 quando há erros. |

Avisos que pedem ação antes da importação real: **salas não encontradas**, **associada por
semelhança**, **nome equivalente**, **colunas sem nome de sala**, **conflito**, **data não
identificada/inválida**, **horário digitado errado/fora de ordem**, **dia da semana não
confere**, **célula mostra outra data**, **aba/linhas/colunas ocultas**, **fórmula sem valor
calculado**, **textos unidos (cabeçalho sobre 2 colunas)**. Avisos informativos: fora do horário
de funcionamento, Dia Inteiro ajustado, solicitante "Não informado", vídeo indisponível,
participantes acima da capacidade, textos ignorados fora da grade, células de horário vago,
**já importada anteriormente**, **reservando agora na grade** (importe de novo mais tarde),
travas de seleção indisponíveis.

---

## 6. Exportação para Excel

- Tela: botões *Exportar dia* / *Exportar semana* na grade (qualquer usuário logado).
- API: `GET /api/export/day.xlsx?date=AAAA-MM-DD` (padrão: hoje) e
  `GET /api/export/week.xlsx?start_date=AAAA-MM-DD` (qualquer dia vira a segunda-feira da semana;
  padrão: semana atual). Arquivos `agenda-2026-10-26.xlsx` / `agenda-semana-2026-10-26.xlsx`.
- CLI: `python -m app.cli export-xlsx --date 2026-10-26 [--week] [-o arquivo.xlsx]`.

Formato (espelho da planilha):

- uma aba por dia (`Seg 26-10`); a exportação da semana tem uma aba por dia útil configurado e
  inclui sábado/domingo só quando houver reservas;
- linha 1: título com dia da semana e data; linha 2: grupos **Videoconferência** e **Salas
  Normais** (cores diferentes); linha 3: salas com capacidade. Sala de vídeo em manutenção vai
  para o bloco normal com "(vídeo em manutenção — chamado X)"; sala desativada só aparece se ainda
  tiver reservas no dia;
- coluna A com os horários (`08:00`, `08:30`, …), estendida quando há reservas fora do horário de
  funcionamento;
- cada reserva ativa é um intervalo mesclado com linhas fixas, nesta ordem:
  `Chamado <n>` (só quando há chamado), solicitante, assunto, `HH:MM–HH:MM` (quando o horário
  não cai nos slots, ex.: 14:15–15:45) e selos (`[Dia inteiro]`, `[Manhã toda]`, `[Tarde toda]`,
  `[Videoconferência]`, `[⚠ Precisa de vídeo]`, `[Presencial — remanejável]`,
  `[N participantes]`, `[Check-in]`, `[Não compareceu]`); cores próprias para vídeo, normal,
  remanejável, check-in, não compareceu e vídeo indisponível;
- para a leitura de volta nunca ficar ambígua, a exportação acrescenta `Chamado —` (sem chamado)
  quando o solicitante começa com "Chamado", e a linha de horário quando o assunto (ou o
  solicitante) parece uma linha de horário ou de selos (ex.: assunto `14:00–15:00` ou
  `[Check-in]`);
- textos digitados (reservas, nomes de salas) são gravados **sempre como texto**: um solicitante
  `=1+1` ou `=HYPERLINK(...)` não vira fórmula (antes virava, e com assunto deixava o arquivo
  inteiro ilegível); textos que começam com `=`, `+`, `-` ou `@` também ficam marcados como texto
  para o Excel não convertê-los se alguém editar a célula. Caracteres que o formato não aceita
  (controles, U+FFFE/U+FFFF colados de outros sistemas) são removidos, e quebras de linha dentro
  de um campo viram espaço;
- bordas finas, quebra de texto, painéis congelados abaixo dos cabeçalhos e à direita dos
  horários; impressão em paisagem, A4, ajustada à largura, repetindo os cabeçalhos.

**Ida e volta**: um arquivo exportado pode ser importado de volta e recria exatamente as mesmas
reservas (sala, data, horários, tipo de período, chamado, solicitante, assunto, vídeo e
participantes). O arquivo leva uma marca nas propriedades (palavra-chave
`agenda-salas:export:v1`) e, com ela, a importação lê cada reserva **por posição**: a 1ª linha
`Chamado …` é o chamado exatamente como escrito (`a definir`, `INC 0012345`), a seguinte o
solicitante (inclusive `-`), depois o assunto; o horário só vem da linha `HH:MM–HH:MM` e vídeo,
participantes e tipo de período só dos selos — nada de procurar chamado, horário ou participantes
no meio dos textos, como na planilha legada. Um Horário Específico de 08:00 às 12:00 continua
específico (sem o selo `[Manhã toda]`). Status (check-in, não compareceu) e observações não
voltam: as reservas reimportadas ficam *Confirmadas*. Reimportar o mesmo arquivo segue a seção
4.3 (já cadastradas / já importadas antes são puladas).

---

## 7. Planilha de exemplo

`docs/exemplos/planilha-legada-exemplo.xlsx` é uma planilha no formato legado para treinar a
simulação. Ela usa as salas do `seed-demo` com grafias variadas e uma sala inexistente, e cada aba
usa um formato de horário diferente. Para gerar de novo (por exemplo, para a próxima semana):

```bash
cd backend
.venv/Scripts/python scripts/make_sample_legacy.py                     # próxima segunda-feira
.venv/Scripts/python scripts/make_sample_legacy.py --monday 2026-10-26 -o ../docs/exemplos/planilha-legada-exemplo.xlsx
```

Simulação esperada (salas do `seed-demo` cadastradas, rodando na sexta-feira anterior à semana do
arquivo):

| Aba | Situação | Encontradas | Importáveis | Puladas | Por quê |
| --- | --- | --- | --- | --- | --- |
| LEIA-ME | ignorada | – | – | – | sem data |
| Qui 22-10 | ignorada | – | – | – | data passada |
| Seg 26-10 | simulada | 5 | 4 | 1 | título com data, linha de grupos, mesclagens, dia inteiro, cópias de texto, só cor, coluna "Sala 11 - Copa" desconhecida |
| Ter 27.10 | simulada | 3 | 3 | 0 | horários `8h`/`8h30`, manhã toda, `[VC]` |
| quarta 28-10-26 | simulada | 3 | 3 | 0 | horários como hora do Excel, texto em várias linhas, participantes |
| Qui 29.10.2026 | simulada | 3 | 3 | 0 | horários como fração do dia, tarde toda |
| Sex 30-10 | simulada | 3 | 3 | 0 | horários como faixa `08:00 - 08:30` |
| Sex 30-10 (cópia) | simulada | 3 | 0 | 3 | aba duplicada: 2 repetidas + 1 conflito com a aba original |
| **Total** | | **20** | **16** | **4** | Salas não encontradas: Sala 11 - Copa |

---

## 8. Limitações conhecidas

- Só `.xlsx` (Excel 2007+). Arquivos `.xls` precisam ser salvos como `.xlsx` antes; `.ods` e
  `.csv` não são lidos. Limite de 10 MB por arquivo.
- O arquivo é lido em streaming, sem montar a planilha inteira na memória, e com limites contra
  arquivos inflados ("zip bombs", formatação aplicada a faixas enormes): no máximo **64 MB** de
  dados descompactados no total; partes com compactação fora do normal (mais de 200:1 em partes
  acima de 4 MB) são recusadas; no máximo **1.000 abas** e **50.000 reservas** por arquivo
  (acima disso: `INVALID_FILE`/erro, divida o arquivo). Por aba, mais de **20.000 células
  preenchidas** ou **10.000 mesclagens** → a aba é ignorada com aviso (não parece uma agenda
  diária). Células vazias só com formatação não contam e não ocupam memória. Uma importação é
  lida por vez em cada servidor.
- Grades com as salas nas **linhas** e os horários nas **colunas** (transpostas) não são
  reconhecidas, nem abas com **vários dias** lado a lado (semana inteira numa aba só): nesse
  caso, separe uma aba por dia antes de importar.
- Não há como saber, pela planilha, quem criou cada reserva: todas ficam em nome de quem importou
  (o histórico mostra a ação **IMPORTED**).
- Check-in, não compareceu e cancelamentos não são importados (só reservas ativas, como
  *Confirmadas*). Cores da planilha legada não têm significado para a importação.
- Fórmulas são lidas pelo último valor calculado salvo no arquivo; as que não têm valor salvo
  são listadas num aviso (seção 3.5). Células com erro (`#N/D`, `#REF!`) são ignoradas.
- O status das abas ignoradas por outros motivos (oculta, grande demais, data inválida) também é
  `SKIPPED_UNRECOGNIZED`; o aviso da aba diz o motivo.
- A CLI sem Redis não vê as travas de seleção nem avisa as telas abertas (seção 2.2).
