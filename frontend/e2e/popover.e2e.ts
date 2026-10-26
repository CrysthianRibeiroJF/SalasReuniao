import { DAYS } from './support/data'
import { ticketUrl } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda, roomName } from './support/grid'

test('popover da reserva: check-in, não compareceu, cancelar com motivo e histórico', async ({
  ana,
  anaApi,
  biaApi,
  ids,
}) => {
  const date = DAYS.popover
  await anaApi.clearDay(date)
  const make = (room: number, start: string, end: string, requester: string, ticket: string) =>
    biaApi.createBooking({
      room_id: room,
      date,
      period_type: 'CUSTOM',
      start,
      end,
      ticket_number: ticket,
      requester_name: requester,
      title: 'Reunião de teste',
    })
  const checkin = await make(ids.rooms.s05, '09:00', '10:00', 'Helena Check-in', '990001')
  const noShow = await make(ids.rooms.s06, '09:00', '10:00', 'Otávio Ausente', '990002')
  const cancel = await make(ids.rooms.s07, '11:00', '12:00', 'Lúcia Cancela', '990003')
  const agenda = new Agenda(ana)
  await agenda.open(date)

  // Detalhes + check-in
  await agenda.block(checkin.id).click()
  let popover = agenda.popover('Helena Check-in')
  await expect(popover).toBeVisible()
  await expect(popover.getByRole('link', { name: /Chamado #990001/ })).toHaveAttribute('href', ticketUrl('990001'))
  await expect(popover).toContainText(roomName('s05'))
  await expect(popover).toContainText('09:00–10:00')
  await expect(popover).toContainText('Criada por Bia Recepção')
  await popover.getByRole('button', { name: 'Check-in' }).click()
  await expect(popover).toBeHidden()
  await expect(agenda.toast('Check-in registrado')).toBeVisible()
  await expect(agenda.block(checkin.id)).toHaveAccessibleName(/, Check-in$/)

  // Não compareceu
  await agenda.block(noShow.id).click()
  popover = agenda.popover('Otávio Ausente')
  await popover.getByRole('button', { name: 'Não compareceu' }).click()
  await expect(popover).toBeHidden()
  await expect(agenda.toast('Marcada como não compareceu')).toBeVisible()
  await expect(agenda.block(noShow.id)).toHaveAccessibleName(/, Não compareceu$/)

  // Cancelar com motivo: o horário fica livre e pode ser reservado de novo.
  await agenda.block(cancel.id).click()
  popover = agenda.popover('Lúcia Cancela')
  await popover.getByRole('button', { name: 'Cancelar', exact: true }).click()
  await expect(popover).toContainText('Cancelar esta reserva? O horário fica livre para outras pessoas.')
  await expect(popover.getByLabel('Motivo do cancelamento')).toBeFocused()
  await ana.keyboard.type('Cliente desmarcou por e-mail')
  await popover.getByRole('button', { name: 'Cancelar reserva' }).click()
  await expect(popover).toBeHidden()
  await expect(agenda.toast('Reserva cancelada')).toBeVisible()
  await expect(agenda.block(cancel.id)).toHaveCount(0)
  const cancelled = await anaApi.booking(cancel.id)
  expect(cancelled).toMatchObject({ status: 'CANCELLED', cancel_reason: 'Cliente desmarcou por e-mail' })

  await agenda.clickCell('s07', '11:00')
  await expect(agenda.composer()).toBeVisible()
  await ana.keyboard.press('Tab')
  await ana.keyboard.type('Novo no mesmo horário')
  await ana.keyboard.press('Enter')
  await expect(agenda.composer()).toBeHidden()
  await expect(agenda.blocks('s07', /Novo no mesmo horário, 11:00 às 11:30/)).toHaveCount(1)

  // Edição pelo popover e histórico (mais recente primeiro).
  await agenda.block(checkin.id).click()
  popover = agenda.popover('Helena Check-in')
  await popover.getByRole('button', { name: 'Editar' }).click()
  const editor = agenda.composer('Editar reserva')
  await editor.getByLabel('Assunto/Área').fill('Reunião de diretoria')
  await editor.getByRole('button', { name: 'Salvar alterações' }).click()
  await expect(editor).toBeHidden()

  await agenda.block(checkin.id).click()
  popover = agenda.popover('Helena Check-in')
  await popover.getByRole('tab', { name: 'Histórico' }).click()
  const entries = popover.locator('ol').first().locator(':scope > li')
  await expect(entries).toHaveCount(3)
  await expect(entries.nth(0)).toContainText('Editou · Ana Recepção')
  await expect(entries.nth(0)).toContainText('Assunto/Área: Reunião de teste → Reunião de diretoria')
  await expect(entries.nth(1)).toContainText('Mudou o status · Ana Recepção')
  await expect(entries.nth(2)).toContainText('Criou · Bia Recepção')
  await ana.keyboard.press('Escape')
  await expect(popover).toBeHidden()

  expect((await anaApi.logs(cancel.id)).map((l) => l.action)).toEqual(['CREATED', 'CANCELLED'])
  expect((await anaApi.logs(noShow.id)).map((l) => [l.action, l.new_state?.status])).toEqual([
    ['CREATED', 'CONFIRMED'],
    ['STATUS_CHANGED', 'NO_SHOW'],
  ])
})
