import type { ServerEventData } from '../src/api/types'
import { DAYS, USERS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, capture, roomName } from './support/grid'
import { installSocketProbe, socketEvents } from './support/timing'

/** Plano 5.2: uma reunião às 15h bloqueia o Dia Inteiro e o painel oferece as duas saídas. */
test('Dia Inteiro em sala com reunião às 15h: reservar em outra sala ou remanejar a reunião', async ({
  ana,
  anaApi,
  biaApi,
  ids,
}) => {
  const date = DAYS.fullDayConflict
  await anaApi.clearDay(date)
  const meeting = await biaApi.createBooking({
    room_id: ids.rooms.s05,
    date,
    period_type: 'CUSTOM',
    start: '15:00',
    end: '16:00',
    ticket_number: '920001',
    requester_name: 'Carlos Quinze',
  })
  const agenda = new Agenda(ana)
  await agenda.open(date)
  await expect(agenda.block(meeting.id)).toBeVisible()

  async function tryFullDay(requester: string) {
    await agenda.clickCell('s05', '08:00')
    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    await ana.keyboard.press('Tab')
    await ana.keyboard.type(requester)
    await ana.keyboard.press('Alt+2')
    await ana.keyboard.press('Enter')
    const panel = composer.getByRole('alert', { name: 'Conflito de horário' })
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('A sala já está reservada para Carlos Quinze (15:00–16:00).')
    return { composer, panel }
  }

  // (1) Reservar em outra sala livre o dia todo: clicar já troca a sala e salva.
  {
    const { composer, panel } = await tryFullDay('Treinamento Anual')
    await expect(panel).toContainText('Escolher outra sala livre o dia todo')
    await panel.getByRole('button', { name: `Reservar na ${roomName('s06')}` }).click()
    await expect(composer).toBeHidden()
    await expect(agenda.toast('Reserva criada')).toBeVisible()
    await expect(agenda.blocks('s06', /Treinamento Anual, 08:00 às 19:00, Dia inteiro/)).toHaveCount(1)
    await expect(agenda.block(meeting.id)).toBeVisible()
  }

  // (2) Remanejar a reunião das 15h para outra sala; em seguida o Dia Inteiro é salvo.
  {
    const { composer, panel } = await tryFullDay('Workshop Diretoria')
    const row = panel.getByRole('listitem').filter({ hasText: 'Carlos Quinze' })
    await expect(row).toContainText('#920001')
    await expect(row).toContainText('15:00–16:00')
    await capture(ana, 'conflito-dia-inteiro')
    // Sala 06 agora está ocupada o dia todo: não é oferecida.
    await expect(row.getByRole('button', { name: `Mover para ${roomName('s06')}` })).toHaveCount(0)
    await row.getByRole('button', { name: `Mover para ${roomName('s07')}` }).click()
    await expect(composer).toBeHidden()
    await expect(agenda.toast(`Reunião de Carlos Quinze movida para ${roomName('s07')}`)).toBeVisible()
    await expect(agenda.column('s07').locator(`[data-booking-id="${meeting.id}"]`)).toBeVisible()
    await expect(agenda.blocks('s05', /Workshop Diretoria, 08:00 às 19:00, Dia inteiro/)).toHaveCount(1)
  }

  const moved = await anaApi.booking(meeting.id)
  expect(moved).toMatchObject({ room_id: ids.rooms.s07, start: '15:00', end: '16:00', version: 2 })
  expect((await anaApi.logs(meeting.id)).map((l) => l.action)).toEqual(['CREATED', 'MOVED_ROOM'])
})

/**
 * Plano 5.2, opção 2, com outra recepcionista olhando (D6): remanejar a reunião conflitante só libera
 * as travas da Ana sobre a posição NOVA da reunião. A trava do Dia Inteiro que ela está montando
 * (posição antiga da reunião) continua a mesma, visível para a Bia, até o Dia Inteiro ser gravado.
 */
test('remanejar a reunião conflitante mantém a trava do Dia Inteiro para a colega até salvar', async ({
  ana,
  bia,
  anaApi,
  biaApi,
  ids,
}) => {
  const date = DAYS.fullDayRelocateLock
  await anaApi.clearDay(date)
  const meeting = await biaApi.createBooking({
    room_id: ids.rooms.s05,
    date,
    period_type: 'CUSTOM',
    start: '15:00',
    end: '16:00',
    ticket_number: '920101',
    requester_name: 'Diego Remanejo',
  })
  await installSocketProbe(bia)
  const a = new Agenda(ana)
  const b = new Agenda(bia)
  await a.open(date)
  await b.open(date)
  await expect(b.block(meeting.id)).toBeVisible()

  // Segura a 2ª gravação do Dia Inteiro (a que vem depois do remanejamento) até conferir a tela da Bia.
  let posts = 0
  let saveRequested!: () => void
  const requested = new Promise<void>((resolve) => (saveRequested = resolve))
  let releaseSave!: () => void
  const held = new Promise<void>((resolve) => (releaseSave = resolve))
  await ana.route('**/api/bookings', async (route) => {
    if (route.request().method() === 'POST' && ++posts === 2) {
      saveRequested()
      await held
    }
    await route.continue()
  })

  await a.clickCell('s05', '08:00')
  const composer = a.composer()
  await expect(composer).toBeVisible()
  await ana.keyboard.press('Tab')
  await ana.keyboard.type('Planejamento Anual')
  await ana.keyboard.press('Alt+2')
  const lock = b.locks('s05')
  await expect(lock).toHaveAttribute('title', `${USERS.ana.fullName} está reservando 08:00–19:00`)
  const lockId = (await lock.getAttribute('data-lock-id'))!
  expect(lockId).toBeTruthy()

  await ana.keyboard.press('Enter')
  const panel = composer.getByRole('alert', { name: 'Conflito de horário' })
  await expect(panel).toContainText('A sala já está reservada para Diego Remanejo (15:00–16:00).')
  const row = panel.getByRole('listitem').filter({ hasText: 'Diego Remanejo' })
  await row.getByRole('button', { name: `Mover para ${roomName('s07')}` }).click()
  await requested

  // A reunião já está na Sala 07 para a Bia (o PATCH foi gravado e publicado) e a trava da Ana sobre
  // a Sala 05 é a mesma de antes: o servidor não a soltou (nem a Ana precisou travar de novo).
  await expect(b.column('s07').locator(`[data-booking-id="${meeting.id}"]`)).toBeVisible()
  await expect(b.column('s05').locator(`[data-booking-id="${meeting.id}"]`)).toHaveCount(0)
  await expect(lock).toHaveCount(1)
  await expect(lock).toHaveAttribute('data-lock-id', lockId)
  await expect(lock).toHaveText(/Reservando:\s*Ana/)
  type Unlocked = ServerEventData['SLOT_UNLOCKED']
  expect((await socketEvents<Unlocked>(bia, 'SLOT_UNLOCKED')).filter((e) => e.lock.lock_id === lockId)).toEqual([])
  // Enquanto isso, a Sala 05 continua protegida: a Bia não consegue começar uma reserva nela.
  await lock.click()
  await expect(b.toast(`${USERS.ana.fullName} está reservando este horário agora.`)).toBeVisible()
  await expect(b.composer()).toHaveCount(0)

  // Gravou o Dia Inteiro: aí sim a trava sai (uma vez, por "saved") e a reserva aparece para a Bia.
  releaseSave()
  await expect(composer).toBeHidden()
  await expect(b.blocks('s05', /Planejamento Anual, 08:00 às 19:00, Dia inteiro/)).toHaveCount(1)
  await expect(lock).toHaveCount(0)
  await expect
    .poll(async () =>
      (await socketEvents<Unlocked>(bia, 'SLOT_UNLOCKED'))
        .filter((e) => e.lock.lock_id === lockId)
        .map((e) => e.reason),
    )
    .toEqual(['saved'])
  await ana.unroute('**/api/bookings')

  expect(await anaApi.booking(meeting.id)).toMatchObject({ room_id: ids.rooms.s07, start: '15:00', end: '16:00' })
})
