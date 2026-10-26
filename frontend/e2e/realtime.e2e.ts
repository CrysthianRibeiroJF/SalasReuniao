import { DAYS, USERS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, capture, longDate } from './support/grid'
import { installSocketProbe, median, report, socketLatencies, watch } from './support/timing'

/** Limite ponta a ponta (ação de uma → tela da outra) com folga para a máquina de testes. */
const MAX_DELAY_MS = 1_000

test.describe('tempo real entre duas recepcionistas', () => {
  test('criação, edição e cancelamento da Ana aparecem para a Bia sem recarregar', async ({
    ana,
    bia,
    anaApi,
    ids,
  }, testInfo) => {
    const date = DAYS.realtimeCrud
    await anaApi.clearDay(date)
    await installSocketProbe(bia)
    const a = new Agenda(ana)
    const b = new Agenda(bia)
    await a.open(date)
    await b.open(date)
    await expect(b.tab(date)).toHaveAccessibleName(`${longDate(date)}, 0 reservas`)

    // Criação
    await a.clickCell('s05', '09:00')
    await expect(a.composer()).toBeVisible()
    await ana.keyboard.type('960001')
    await ana.keyboard.press('Tab')
    await ana.keyboard.type('Cliente Tempo Real')
    const created = watch(
      bia,
      (room) =>
        !!document.querySelector(`[data-room-id="${room}"] [data-booking-id][aria-label*="Cliente Tempo Real"]`),
      ids.rooms.s05,
    )
    created.start()
    await ana.keyboard.press('Enter')
    const createDelay = await created.done()
    report(testInfo, 'criação (Ana salva → bloco na tela da Bia)', createDelay)
    expect(createDelay).toBeLessThan(MAX_DELAY_MS)
    await expect(b.blocks('s05', /Chamado 960001, Cliente Tempo Real, 09:00 às 09:30/)).toHaveCount(1)
    await expect(b.tab(date)).toHaveAccessibleName(`${longDate(date)}, 1 reserva`)
    const [booking] = await anaApi.bookings(date)

    // Edição (pelo popover → Editar)
    await a.block(booking!.id).click()
    await a.popover('Cliente Tempo Real').getByRole('button', { name: 'Editar' }).click()
    const editor = a.composer('Editar reserva')
    await expect(editor).toBeVisible()
    await editor.getByLabel('Solicitante').fill('Cliente Editado')
    const edited = watch(
      bia,
      (id) => !!document.querySelector(`[data-booking-id="${id}"][aria-label*="Cliente Editado"]`),
      booking!.id,
    )
    edited.start()
    await ana.keyboard.press('Enter')
    const editDelay = await edited.done()
    report(testInfo, 'edição (Ana salva → texto novo na tela da Bia)', editDelay)
    expect(editDelay).toBeLessThan(MAX_DELAY_MS)
    await expect(a.toast('Reserva atualizada')).toBeVisible()

    // Cancelamento (popover → Cancelar → confirmar). A Bia está com os detalhes da mesma reserva
    // abertos: o popover dela fecha sozinho quando a reserva some.
    await b.block(booking!.id).click()
    await expect(b.popover('Cliente Editado')).toBeVisible()
    await a.block(booking!.id).click()
    const popover = a.popover('Cliente Editado')
    await popover.getByRole('button', { name: 'Cancelar', exact: true }).click()
    const cancelled = watch(bia, (id) => !document.querySelector(`[data-booking-id="${id}"]`), booking!.id)
    cancelled.start()
    await popover.getByRole('button', { name: 'Cancelar reserva' }).click()
    const cancelDelay = await cancelled.done()
    report(testInfo, 'cancelamento (Ana confirma → bloco some da tela da Bia)', cancelDelay)
    expect(cancelDelay).toBeLessThan(MAX_DELAY_MS)
    await expect(b.tab(date)).toHaveAccessibleName(`${longDate(date)}, 0 reservas`)
    await expect(b.column('s05').locator('[data-booking-id]')).toHaveCount(0)
    await expect(b.popover('Cliente Editado')).toBeHidden()

    // RNF01: da publicação no servidor até chegar ao navegador da Bia (meta: < 200 ms).
    const latencies = await socketLatencies(bia)
    const all: number[] = []
    for (const type of ['SLOT_LOCKED', 'SLOT_UNLOCKED', 'BOOKING_CREATED', 'BOOKING_UPDATED', 'BOOKING_CANCELLED']) {
      const samples = latencies.get(type) ?? []
      expect(samples.length, `evento ${type} recebido pela Bia`).toBeGreaterThan(0)
      report(testInfo, `evento ${type} (publicação no servidor → navegador da Bia)`, Math.max(...samples))
      all.push(...samples)
    }
    report(testInfo, `mediana servidor → navegador (${all.length} eventos)`, median(all))
    expect(median(all)).toBeLessThan(200)
    expect(Math.max(...all)).toBeLessThan(MAX_DELAY_MS)
  })

  test('trava "Reservando: Ana": aparece para a Bia, some com Esc, bloqueia o clique e sai ao fechar a página', async ({
    ana,
    bia,
    anaApi,
    ids,
  }, testInfo) => {
    const date = DAYS.realtimeLocks
    await anaApi.clearDay(date)
    const a = new Agenda(ana)
    const b = new Agenda(bia)
    await a.open(date)
    await b.open(date)
    const hasLock = (room: number) => !!document.querySelector(`[data-room-id="${room}"] [data-lock-id]`)
    const noLock = (room: number) => !document.querySelector(`[data-room-id="${room}"] [data-lock-id]`)

    // Ana abre o modal numa célula → a Bia vê a célula travada com o nome dela.
    const locked = watch(bia, hasLock, ids.rooms.s06)
    locked.start()
    await a.clickCell('s06', '10:00')
    const lockDelay = await locked.done()
    report(testInfo, 'trava (clique da Ana → "Reservando" na tela da Bia)', lockDelay)
    expect(lockDelay).toBeLessThan(MAX_DELAY_MS)
    await expect(a.composer()).toBeVisible()
    const lock = b.locks('s06')
    await expect(lock).toHaveText(/Reservando:\s*Ana/)
    await expect(lock).toHaveAttribute('title', `${USERS.ana.fullName} está reservando 10:00–10:30`)
    // A própria Ana não vê a trava dela como bloqueio.
    await expect(a.locks('s06')).toHaveCount(0)

    // Esc fecha o modal e libera a trava para a Bia.
    const released = watch(bia, noLock, ids.rooms.s06)
    released.start()
    await ana.keyboard.press('Escape')
    const releaseDelay = await released.done()
    report(testInfo, 'liberação (Esc da Ana → trava some da tela da Bia)', releaseDelay)
    expect(releaseDelay).toBeLessThan(MAX_DELAY_MS)
    await expect(a.composer()).toBeHidden()

    // Faixa de 1 h: o bloco hachurado mostra o nome completo.
    await a.dragCells('s07', '10:00', '10:30')
    await expect(a.composer()).toBeVisible()
    const rangeLock = b.locks('s07')
    await expect(rangeLock).toContainText('Reservando:')
    await expect(rangeLock).toContainText(USERS.ana.fullName)
    await expect(rangeLock).toHaveAttribute('title', `${USERS.ana.fullName} está reservando 10:00–11:00`)
    await capture(bia, 'trava-reservando')

    // A Bia clica na célula travada (o clique cai no hachurado): aviso e nenhum modal.
    await rangeLock.click()
    await expect(b.toast(`${USERS.ana.fullName} está reservando este horário agora.`)).toBeVisible()
    await expect(b.composer()).toHaveCount(0)
    await expect(bia.locator('[data-selection]')).toHaveCount(0)

    // Ana fecha a página com o modal aberto: a conexão cai e a trava some para a Bia.
    const gone = watch(bia, noLock, ids.rooms.s07)
    gone.start()
    await ana.close()
    const closeDelay = await gone.done()
    report(testInfo, 'desconexão (Ana fecha a página → trava some da tela da Bia)', closeDelay)
    expect(closeDelay).toBeLessThan(MAX_DELAY_MS)

    // Agora a Bia consegue reservar ali.
    await b.clickCell('s07', '10:30')
    await expect(b.composer()).toBeVisible()
    await bia.keyboard.press('Escape')
    await expect(b.composer()).toBeHidden()
  })

  test('as duas clicam na mesma célula ao mesmo tempo: só uma abre o modal', async ({ ana, bia, anaApi }) => {
    const date = DAYS.realtimeLocks
    await anaApi.clearDay(date)
    const a = new Agenda(ana)
    const b = new Agenda(bia)
    await a.open(date)
    await b.open(date)

    await Promise.all([a.clickCell('s08', '15:00'), b.clickCell('s08', '15:00')])
    const opened = async () => (await a.composer().count()) + (await b.composer().count())
    await expect.poll(opened).toBe(1)
    const [winner, loser] = (await a.composer().count()) === 1 ? [a, b] : [b, a]
    await expect(loser.toast(/está reservando este horário agora\.$/)).toBeVisible()
    await expect(loser.locks('s08')).toHaveCount(1)
    await expect(winner.locks('s08')).toHaveCount(0)
    // Continua só um modal (a negada não abre depois).
    await expect.poll(opened, { timeout: 1_500 }).toBe(1)
    await winner.page.keyboard.press('Escape')
    await expect(loser.locks('s08')).toHaveCount(0)
  })

  test('presença: cada recepcionista vê a outra online', async ({ ana, bia }) => {
    const a = new Agenda(ana)
    const b = new Agenda(bia)
    await a.open(DAYS.realtimeLocks)
    await b.open(DAYS.realtimeLocks)
    const onlineForAna = ana.getByRole('group', { name: /colegas? online$/ })
    await expect(onlineForAna.getByLabel(/^Bia Recepção/)).toBeVisible()
    await expect(onlineForAna.getByLabel(/^Ana Recepção/)).toHaveCount(0)
    const onlineForBia = bia.getByRole('group', { name: /colegas? online$/ })
    await expect(onlineForBia.getByLabel(/^Ana Recepção/)).toBeVisible()
    await expect(onlineForBia.getByText('AR')).toBeVisible()
  })

  test('edição concorrente: a segunda a salvar é avisada e não desfaz a mudança da primeira', async ({
    ana,
    bia,
    anaApi,
    ids,
  }) => {
    const date = DAYS.staleEdit
    await anaApi.clearDay(date)
    const original = await anaApi.createBooking({
      room_id: ids.rooms.s08,
      date,
      period_type: 'CUSTOM',
      start: '11:00',
      end: '12:00',
      ticket_number: '970001',
      title: 'Pauta original',
      requester_name: 'Reunião Concorrente',
    })
    const a = new Agenda(ana)
    const b = new Agenda(bia)
    await a.open(date)
    await b.open(date)

    for (const agenda of [a, b]) {
      await agenda.block(original.id).click()
      await agenda.popover('Reunião Concorrente').getByRole('button', { name: 'Editar' }).click()
      await expect(agenda.composer('Editar reserva')).toBeVisible()
    }

    // Ana salva primeiro (muda o assunto).
    const anaEditor = a.composer('Editar reserva')
    await anaEditor.getByLabel('Assunto/Área').fill('Pauta da Ana')
    await anaEditor.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(anaEditor).toBeHidden()
    await expect(b.block(original.id)).toHaveAccessibleName(/Pauta da Ana/)

    // Bia, com a versão antiga aberta, muda o solicitante e salva: "alterada por outra pessoa".
    const biaEditor = b.composer('Editar reserva')
    await biaEditor.getByLabel('Solicitante').fill('Solicitante da Bia')
    await biaEditor.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(b.toast('Esta reserva foi alterada por outra pessoa')).toBeVisible()
    await expect(biaEditor.getByRole('alert')).toContainText('Esta reserva foi alterada por outra pessoa.')
    await expect(biaEditor).toBeVisible()
    // O formulário passa a mostrar a mudança da Ana e mantém a da Bia.
    await expect(biaEditor.getByLabel('Assunto/Área')).toHaveValue('Pauta da Ana')
    await expect(biaEditor.getByLabel('Solicitante')).toHaveValue('Solicitante da Bia')
    expect((await anaApi.booking(original.id)).requester_name).toBe('Reunião Concorrente')

    // Salvar de novo grava a mudança da Bia sem desfazer a da Ana.
    await biaEditor.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(biaEditor).toBeHidden()
    await expect(b.toast('Reserva atualizada')).toBeVisible()
    const final = await anaApi.booking(original.id)
    expect(final).toMatchObject({ title: 'Pauta da Ana', requester_name: 'Solicitante da Bia', version: 3 })
    await expect(a.block(original.id)).toHaveAccessibleName(/Solicitante da Bia, Pauta da Ana/)
  })

  test('reserva cancelada pela outra enquanto o modal de edição está aberto', async ({ ana, anaApi, biaApi, ids }) => {
    const date = DAYS.staleEdit
    await anaApi.clearDay(date)
    const booking = await anaApi.createBooking({
      room_id: ids.rooms.s09,
      date,
      period_type: 'CUSTOM',
      start: '15:00',
      end: '16:00',
      requester_name: 'Vai Ser Cancelada',
    })
    const a = new Agenda(ana)
    await a.open(date)
    await a.block(booking.id).click()
    await a.popover('Vai Ser Cancelada').getByRole('button', { name: 'Editar' }).click()
    const editor = a.composer('Editar reserva')
    await editor.getByLabel('Assunto/Área').fill('Mudança que não vai entrar')

    await biaApi.cancelBooking(booking.id, 'Cancelada pela Bia')
    await expect(a.block(booking.id)).toHaveCount(0)
    await editor.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(editor.getByRole('alert')).toContainText('Reservas canceladas não podem ser alteradas.')
    await ana.keyboard.press('Escape')
    await expect(editor).toBeHidden()
    expect(await anaApi.booking(booking.id)).toMatchObject({ status: 'CANCELLED', title: null })
  })
})
