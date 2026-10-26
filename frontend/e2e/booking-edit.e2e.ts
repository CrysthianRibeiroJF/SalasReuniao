import { DAYS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, longDate, roomName, tabLabel } from './support/grid'

test.describe('edição e remanejamento', () => {
  test.beforeEach(async ({ anaApi }) => {
    await anaApi.clearDay(DAYS.editing)
    await anaApi.clearDay(DAYS.moveTarget)
  })

  test('editar a data no modal leva a reserva para outro dia (abas e a outra tela acompanham)', async ({
    ana,
    bia,
    anaApi,
    ids,
  }) => {
    const booking = await anaApi.createBooking({
      room_id: ids.rooms.s06,
      date: DAYS.editing,
      period_type: 'CUSTOM',
      start: '10:00',
      end: '11:00',
      ticket_number: '930001',
      requester_name: 'Muda de Dia',
    })
    const a = new Agenda(ana)
    const b = new Agenda(bia)
    await a.open(DAYS.editing)
    await b.open(DAYS.editing)
    await expect(a.tab(DAYS.editing)).toHaveAccessibleName(`${longDate(DAYS.editing)}, 1 reserva`)
    await expect(a.tab(DAYS.moveTarget)).toHaveAccessibleName(`${longDate(DAYS.moveTarget)}, 0 reservas`)

    await a.block(booking.id).click()
    await a.popover('Muda de Dia').getByRole('button', { name: 'Editar' }).click()
    const editor = a.composer('Editar reserva')
    await expect(editor.getByLabel('Sala')).toHaveValue(String(ids.rooms.s06))
    await editor.getByLabel('Data').fill(DAYS.moveTarget)
    await expect(editor).toContainText(`${roomName('s06')}${tabLabel(DAYS.moveTarget)}10:00–11:00`)
    await editor.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(editor).toBeHidden()
    await expect(a.toast(`${roomName('s06')} · ${tabLabel(DAYS.moveTarget)} · 10:00–11:00 · Muda de Dia`)).toBeVisible()

    for (const agenda of [a, b]) {
      await expect(agenda.block(booking.id)).toHaveCount(0)
      await expect(agenda.tab(DAYS.editing)).toHaveAccessibleName(`${longDate(DAYS.editing)}, 0 reservas`)
      await expect(agenda.tab(DAYS.moveTarget)).toHaveAccessibleName(`${longDate(DAYS.moveTarget)}, 1 reserva`)
    }
    await b.tab(DAYS.moveTarget).click()
    await b.waitForDay(DAYS.moveTarget)
    await expect(b.column('s06').locator(`[data-booking-id="${booking.id}"]`)).toBeVisible()
    expect((await anaApi.logs(booking.id)).map((l) => l.action)).toEqual(['CREATED', 'RESCHEDULED'])
  })

  test('arrastar para um horário ocupado não muda nada; para um livre, remaneja', async ({ ana, anaApi, ids }) => {
    const make = (start: string, end: string, requester: string) =>
      anaApi.createBooking({
        room_id: ids.rooms.s07,
        date: DAYS.editing,
        period_type: 'CUSTOM',
        start,
        end,
        requester_name: requester,
      })
    const moving = await make('14:00', '15:00', 'Vai Arrastar')
    const blocker = await make('16:00', '17:00', 'Já Ocupa')
    const agenda = new Agenda(ana)
    await agenda.open(DAYS.editing)

    await agenda.dragBlockTo(agenda.block(moving.id), 's07', '16:00')
    await expect(agenda.toast('Horário ocupado nesta sala')).toBeVisible()
    await expect(agenda.toast('A reserva voltou para o lugar original.')).toBeVisible()
    await expect(agenda.block(moving.id)).toHaveAccessibleName(/14:00 às 15:00/)
    expect(await anaApi.booking(moving.id)).toMatchObject({ start: '14:00', end: '15:00', version: 1 })
    expect(await anaApi.booking(blocker.id)).toMatchObject({ version: 1 })

    // O mesmo bloco, para cima, num horário livre da mesma sala: muda só o horário.
    await agenda.dragBlockTo(agenda.block(moving.id), 's07', '09:00')
    await expect(agenda.toast('Reserva remanejada')).toBeVisible()
    await expect(agenda.block(moving.id)).toHaveAccessibleName(/09:00 às 10:00/)
    expect(await anaApi.booking(moving.id)).toMatchObject({ room_id: ids.rooms.s07, start: '09:00', end: '10:00' })
    expect((await anaApi.logs(moving.id)).map((l) => l.action)).toEqual(['CREATED', 'RESCHEDULED'])
  })

  test('avisos do modal: capacidade e vídeo indisponível, com troca de sala em 1 clique', async ({
    ana,
    anaApi,
    ids,
  }) => {
    const agenda = new Agenda(ana)
    await agenda.open(DAYS.editing)
    await agenda.clickCell('s10', '11:00')
    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    await composer.getByLabel('Solicitante').fill('Banca de Entrevista')
    await composer.getByRole('button', { name: 'Mais detalhes' }).click()
    await composer.getByLabel('Participantes').fill('5')
    await expect(composer.getByRole('status').filter({ hasText: 'comporta' })).toHaveText(
      `${roomName('s10')} comporta 3 pessoas; foram informados 5 participantes.`,
    )

    await composer.getByRole('switch', { name: /Precisa de Videoconferência\?/ }).click()
    const noVideo = composer.getByRole('status').filter({ hasText: 'não tem videoconferência' })
    await expect(noVideo).toContainText(`${roomName('s10')} não tem videoconferência funcionando agora.`)
    await noVideo.getByRole('button', { name: `Usar ${roomName('s01')}` }).click()

    // Na Sala 01 (vídeo, 12 lugares) os dois avisos somem.
    await expect(composer).toContainText(`${roomName('s01')}${tabLabel(DAYS.editing)}11:00–11:30`)
    await expect(composer.getByRole('status').filter({ hasText: /comporta|não tem videoconferência/ })).toHaveCount(0)
    await composer.getByRole('button', { name: 'Salvar', exact: true }).click()
    await expect(composer).toBeHidden()
    const [saved] = await anaApi.bookings(DAYS.editing)
    expect(saved).toMatchObject({
      room_id: ids.rooms.s01,
      requires_video: true,
      attendees: 5,
      video_unavailable: false,
    })
  })
})
