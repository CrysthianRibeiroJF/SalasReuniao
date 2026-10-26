# Manual da Recepção — Agenda de Salas

Bem-vinda à nova agenda! Ela funciona como a planilha que você já conhece — salas nas colunas,
horários nas linhas, um dia por aba —, com algumas vantagens:

- o que uma colega faz aparece na sua tela **na hora**, sem recarregar;
- **ninguém consegue reservar a mesma sala no mesmo horário** que outra pessoa: o sistema não deixa;
- enquanto alguém está reservando um horário, ele aparece para as outras como **"Reservando: Fulana"**;
- o número do chamado vira **link** para o sistema de chamados.

Para entrar, abra o endereço do sistema no navegador e use o **seu** usuário e senha (cada uma tem
o seu: o histórico registra quem fez cada coisa). A sessão dura um turno (12 horas); depois disso,
é só entrar de novo. Várias abas do mesmo navegador usam a mesma sessão: **Sair** numa aba sai de
todas, e se outra pessoa entrar nesse navegador as outras abas passam a ser dela.

---

## 1. A tela

```
+----------------------------------------------------------------------------------+
| [Buscar chamado ou nome]  Todas | Vídeo | Normais | Livres o dia todo  Exportar  ● |  1. barra superior
+----------------------------------------------------------------------------------+
|        | VIDEOCONFERÊNCIA          | SALAS NORMAIS                                  |
|        | Sala 01 | Sala 02 | ...   | Sala 05 | Sala 06 | ...                      |
| 08:00  |         | ####### |       |         |         |                          |  2. grade
| 08:30  |         | ####### |       | //////  |         |   //// = "Reservando:   |
| ...    |         |         |       |         |         |           Fulana"        |
+----------------------------------------------------------------------------------+
| ‹ › Hoje |  Seg 26/10 (5) | Ter 27/10 (3) | Qua 28/10 | ... |  + Calendário      |  4. abas dos dias
+----------------------------------------------------------------------------------+
                  (3. o modal de reserva abre ao lado das células escolhidas)
```

1. **Barra superior** — busca, filtros de salas, botões de exportar para Excel, indicador de
   conexão (bolinha verde = **Conectado**), as iniciais das colegas online e o seu menu (trocar de
   tela, sair).
2. **Grade** — à esquerda o bloco **Videoconferência** (ícone de câmera), à direita as **Salas
   Normais**. Passe o mouse no nome da sala para ver capacidade e recursos. A linha colorida marca a
   hora atual; horários que já passaram ficam mais claros.
3. **Modal de reserva** — abre quando você escolhe um horário livre.
4. **Abas dos dias** — como as abas da planilha: `Seg 26/10` … `Sex 30/10`, com o número de
   reservas de cada dia. As setas `‹ ›` trocam de semana, **Hoje** volta para hoje e
   **+ Calendário** abre qualquer data (sábado e domingo aparecem como uma aba extra).

Filtros de 1 clique: **Todas as Salas**, **Videoconferência**, **Salas Normais** e
**Livres Dia Inteiro** (só as salas sem nenhuma reserva no dia; enquanto o dia carrega, aparece
*"Procurando salas livres o dia inteiro…"*).

Uma coluna **cinza com o selo "Sala desativada"** é de uma sala que saiu de uso e ainda tem
reservas antigas naquele dia. Ela não aceita reservas novas, mas as reservas dela podem ser
abertas, arrastadas para outra sala ou canceladas.

## 2. Reservar em 3 cliques

1. **Clique** num horário livre da sala (para mais de meia hora, **clique e arraste** pelas células
   da mesma sala).
2. O modal abre com o cursor no **Nº do Chamado**: digite o número, aperte `Tab` e digite o
   **Solicitante**. *Assunto/Área* é opcional.
3. Aperte `Enter` (ou clique em **Salvar**). Pronto: a reserva aparece para todas.

Enquanto o modal está aberto, as colegas veem o horário hachurado com **"Reservando: [seu nome]"**.
Se desistir, aperte `Esc` ou **Cancelar**: o horário é liberado na hora. Clicou na célula errada?
`Esc` logo em seguida cancela antes mesmo de o modal abrir.

Clicar no nome de uma sala, na coluna de horários ou na barra de rolagem da grade **não** fecha o
modal nem apaga o que você digitou (o cursor volta para o campo). Só um clique numa célula ou
reserva da grade, ou fora dela, fecha o modal.

Em **Mais detalhes** você informa o número de participantes e observações.

## 3. Dia Inteiro, Manhã e Tarde

No campo **Tipo de Período**:

| Opção | Horário | Atalho |
| --- | --- | --- |
| Horário Específico | o que você selecionou (pode ajustar Início e Fim) | `Alt+1` |
| Dia Inteiro | 08:00 às 19:00 | `Alt+2` |
| Manhã | 08:00 às 12:00 | `Alt+3` |
| Tarde | 13:00 às 19:00 | `Alt+4` |

Não precisa selecionar o dia todo na grade: clique em qualquer horário da sala, escolha
**Dia Inteiro** e salve. O bloco ocupa o período inteiro, como as células mescladas da planilha.

## 4. Quando o horário já está ocupado

Se já existir uma reunião no caminho (por exemplo, você pediu **Dia Inteiro** e há uma reunião às
15h), o sistema não deixa salvar e mostra duas saídas, cada uma com 1 clique:

1. **Escolher outra sala livre** — botões *Reservar na Sala X* com as salas livres no período
   inteiro (as do mesmo tipo aparecem primeiro). Clicou, salvou.
2. **Remanejar a reunião que está no caminho** — para cada reunião conflitante aparecem as salas
   livres naquele horário (*Mover para Sala Y*). Ao mover, o sistema tenta salvar a sua reserva de
   novo automaticamente. Se aparecer um aviso **"Reunião de Fulano: …"** (por exemplo, sala menor
   que o número de participantes), ele é sobre a reunião que você remanejou.

Cada botão mostra a capacidade da sala (**"· 8 lugares"**, em vermelho quando a sala é pequena
para os participantes informados), e as salas em que a reunião cabe aparecem primeiro. Se uma
reunião do caminho já tiver sido mudada por outra pessoa, o painel avisa *"Já saiu do caminho…
Tente salvar de novo"*.

Se a mensagem for **"Esta reserva foi alterada por outra pessoa"**, uma colega mexeu na mesma
reserva ao mesmo tempo: a tela já foi atualizada, confira e faça de novo.

## 5. Videoconferência e o selo "Remanejável"

- O switch **Precisa de Videoconferência?** começa em **Não**. Marque **Sim** só quando a reunião
  vai usar o equipamento de vídeo.
- Se você reservar uma **sala de vídeo** para uma reunião **sem vídeo**, aparece um aviso com as
  **salas normais livres** no mesmo horário — clique numa delas para trocar. Você pode manter a
  sala de vídeo, se preferir: a reunião ganha o selo **"Reunião Presencial — Remanejável"**.
- Precisa de uma sala de vídeo e estão todas ocupadas? Procure na grade os blocos com o selo
  **Remanejável** e **arraste** essa reunião para uma sala normal livre (seção 6). A sala de vídeo
  fica livre para quem realmente precisa.
- O selo **"⚠ Precisa de vídeo"** indica uma reunião que precisa de vídeo numa sala em que o vídeo
  não está funcionando: mude-a para uma sala de vídeo livre.

## 6. Mover uma reunião (arrastar e soltar)

Clique no bloco da reserva, **segure e arraste** para outra sala e/ou outro horário. Enquanto
arrasta, o destino mostra *"Mover para Sala X"* ou *"Horário ocupado"*. Soltou num lugar livre, a
reserva muda na hora para todas. Se o destino estiver ocupado ou alguém estiver reservando ali, o
bloco volta para o lugar e um aviso explica o motivo. `Esc` durante o arrasto cancela.

Mudar o horário de uma reserva de Dia Inteiro, Manhã ou Tarde a transforma em Horário Específico.
Para trocar de **dia**, use **Editar** (seção 8) e mude a *Data*.

## 7. "Reservando: Fulana"

O hachurado com **"Reservando: Fulana"** (ou **"Editando: Fulana"**) quer dizer que a colega está
com aquele horário aberto no modal. Ninguém mais consegue reservá-lo enquanto isso; se você
clicar ali, o sistema avisa quem está reservando.

O horário é liberado assim que ela salva, cancela ou fecha o navegador. Se a rede dela cair ou o
computador travar, a marcação some sozinha em menos de 1 minuto — **no máximo em 2 minutos**. Se
continuar por mais tempo, peça para ela apertar `Esc` ou recarregar a página.

## 8. Buscar, editar, check-in, não compareceu e cancelar

**Buscar:** aperte `/` (ou `Ctrl+K`) e digite o número do chamado ou o nome do solicitante (acentos
não importam). Use `↑`/`↓` para escolher e `Enter` para ir: o sistema abre o dia certo, rola a
grade até a reserva e a destaca.

**Clique numa reserva** para ver os detalhes (com o link do chamado) e as ações:

| Botão | Quando usar |
| --- | --- |
| **Editar** | mudar qualquer dado, inclusive a sala e a data |
| **Check-in** | as pessoas chegaram para a reunião |
| **Não compareceu** | a reunião não aconteceu (a reserva continua na grade, marcada, para o histórico) |
| **Voltar para confirmada** | desfaz um check-in ou "não compareceu" marcado por engano |
| **Cancelar** | pede confirmação e um motivo (opcional); o horário fica livre para outras reservas |

A aba **Histórico** mostra quem criou, alterou, moveu ou cancelou a reserva, quando e o que mudou
(todos os campos, também quando a reserva trocou de sala ou de horário).

## 9. Vídeo em manutenção

Quando o equipamento de vídeo de uma sala quebrar: passe o mouse sobre o nome da sala na grade,
clique no ícone de **chave inglesa** que aparece ao lado do nome, informe o **número do chamado de TI** e clique em
**Marcar manutenção**. A sala vai para o bloco das **Salas Normais** com o selo
**"Vídeo em manutenção · #chamado"** (clique para abrir o chamado; em colunas estreitas o texto
encolhe, mas o número fica visível). As reuniões que precisam de
vídeo nessa sala ganham o selo **"⚠ Precisa de vídeo"**: remaneje-as.

Quando o vídeo voltar a funcionar, clique de novo na chave inglesa (ela fica visível enquanto a
sala está em manutenção) e em **Encerrar manutenção**.

## 10. Exportar para Excel

No topo da tela, **Exportar → Dia** baixa a grade do dia exibido e **Semana** baixa a semana
inteira (uma aba por dia), no mesmo formato da planilha antiga. Use para imprimir, enviar a um
gestor ou guardar uma cópia.

## 11. Atalhos de teclado

| Onde | Tecla | O que faz |
| --- | --- | --- |
| Grade | `↑` `↓` `←` `→` | move o cursor de célula |
| Grade | `Shift+↑` / `Shift+↓` | aumenta ou diminui a seleção na mesma sala |
| Grade | `Home` / `End` | primeiro / último horário do dia |
| Grade | `Enter` | abre o modal de reserva (ou os detalhes, se o cursor estiver sobre uma reserva) |
| Grade | `Esc` | desfaz a seleção |
| Grade (fora de campos de texto) | `PageUp` / `PageDown` ou `Ctrl+←` / `Ctrl+→` | dia anterior / próximo dia |
| Grade (fora de campos de texto) | `/` | vai para a busca |
| Qualquer lugar | `Ctrl+K` | vai para a busca |
| Busca | `↑` `↓`, `Enter`, `Esc` | escolhe o resultado, abre, limpa |
| Abas dos dias | `←` `→` | troca de aba (com a aba selecionada) |
| Modal | `Enter` | salva |
| Modal | `Ctrl+Enter` | salva quando o cursor está em *Observações* |
| Modal | `Alt+1` … `Alt+4` | Horário Específico, Dia Inteiro, Manhã, Tarde |
| Modal | `Esc` | fecha sem salvar e libera o horário |
| Arrastando uma reserva | `Esc` | cancela o arrasto |

Reserva só com o teclado: setas até a célula → `Enter` → chamado → `Tab` → solicitante → `Enter`.

## 12. Se algo parecer estranho

- **Bolinha amarela "Reconectando…" ou cinza "Offline"** no topo: a conexão caiu. O sistema tenta
  voltar sozinho e atualiza a grade quando consegue. Você pode continuar trabalhando, mas as colegas
  não verão sua seleção até a conexão voltar (o modal mostra "Sem tempo real").
- **Voltou para a tela de login** — o aviso no topo diz o motivo:

  | Aviso | O que aconteceu | O que fazer |
  | --- | --- | --- |
  | *"Sua sessão expirou. Entre novamente."* | a sessão de 12 horas terminou | entre de novo; você volta para a tela em que estava |
  | *"Sua sessão foi encerrada porque a senha foi alterada. Entre novamente."* | a sua senha foi trocada (por você em outro computador ou pela administração) | entre com a senha nova; se você não trocou a senha, avise a administração |
  | *"Seu acesso foi desativado. Fale com o administrador."* | a sua conta foi desativada | fale com a administração |

- **"Muitas tentativas de entrada sem sucesso. Tente novamente em …"**: depois de 5 senhas erradas
  seguidas, a entrada fica bloqueada por 1 minuto (e por mais tempo se continuar errando). A tela
  mostra a contagem (*"Tente novamente em 0:59"*) e o botão **Entrar** volta sozinho no fim. O
  bloqueio também vale para o endereço de rede de onde vieram as tentativas: colegas no mesmo local
  podem precisar esperar esse minuto também. Esqueceu a senha? Peça à administração para
  redefini-la.
- **A grade parece desatualizada**: aperte `F5`.
- Qualquer outra coisa: avise a administração, com o horário e o que você estava fazendo.
