import { DAYS } from './support/data'
import { ticketUrl } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda, capture, roomName } from './support/grid'

/** A sala está no bloco "Salas Normais" (à direita do cabeçalho do grupo) ou no de Videoconferência. */
async function groupOf(agenda: Agenda): Promise<'video' | 'normal'> {
  const room = await agenda.header('s04').boundingBox()
  const normal = await agenda.page.getByRole('columnheader', { name: /^Salas Normais/ }).boundingBox()
  if (!room || !normal) throw new Error('cabeçalhos não encontrados')
  return room.x >= normal.x - 1 ? 'normal' : 'video'
}

test('RF02: vídeo em manutenção pelo cabeçalho da grade, visto pelas duas recepcionistas', async ({
  ana,
  bia,
  anaApi,
  ids,
}) => {
  const date = DAYS.maintenance
  await anaApi.clearDay(date)
  // Garante o estado inicial mesmo se uma execução anterior parou no meio.
  await anaApi.clearMaintenance(ids.rooms.s04)
  const needsVideo = await anaApi.createBooking({
    room_id: ids.rooms.s04,
    date,
    period_type: 'CUSTOM',
    start: '09:00',
    end: '10:30',
    ticket_number: '980001',
    requester_name: 'Reunião com Matriz',
    title: 'Precisa de vídeo',
    requires_video: true,
  })
  const a = new Agenda(ana)
  const b = new Agenda(bia)
  await a.open(date)
  await b.open(date)
  for (const agenda of [a, b]) {
    expect(await groupOf(agenda)).toBe('video')
    await expect(agenda.block(needsVideo.id)).not.toHaveAccessibleName(/Precisa de vídeo$/)
  }

  // Ana marca a manutenção pelo botão (chave) do cabeçalho da sala.
  await a.header('s04').hover()
  await ana.getByRole('button', { name: `Marcar vídeo da ${roomName('s04')} em manutenção` }).click()
  const dialog = ana.getByRole('dialog', { name: 'Vídeo em manutenção' })
  await expect(dialog).toContainText(roomName('s04'))
  await dialog.getByRole('button', { name: 'Marcar manutenção' }).click()
  await expect(dialog.getByText('Informe o número do chamado de TI.')).toBeVisible()
  await dialog.getByLabel('Nº do chamado de TI').fill('INC0099001')
  await dialog.getByRole('button', { name: 'Marcar manutenção' }).click()
  await expect(dialog).toBeHidden()
  await expect(a.toast(`${roomName('s04')}: vídeo em manutenção`)).toBeVisible()

  // Nas duas telas: a sala vai para o bloco das normais, com o selo e o link do chamado,
  // e a reunião que precisa de vídeo ganha o alerta.
  for (const agenda of [a, b]) {
    await expect.poll(() => groupOf(agenda)).toBe('normal')
    const badge = agenda.header('s04').getByRole('link', { name: 'Vídeo em manutenção · Chamado INC0099001' })
    await expect(badge).toHaveAttribute('href', ticketUrl('INC0099001'))
    await expect(agenda.block(needsVideo.id)).toHaveAccessibleName(/Precisa de vídeo$/)
  }
  expect(await anaApi.booking(needsVideo.id)).toMatchObject({ video_unavailable: true })
  await capture(bia, 'manutencao-video')

  // Bia encerra a manutenção: a sala volta para Videoconferência nas duas telas.
  await bia.getByRole('button', { name: `Encerrar manutenção do vídeo da ${roomName('s04')}` }).click()
  const close = bia.getByRole('dialog', { name: 'Vídeo voltou a funcionar?' })
  await expect(close).toContainText('pelo chamado INC0099001')
  await close.getByRole('button', { name: 'Encerrar manutenção' }).click()
  await expect(close).toBeHidden()
  await expect(b.toast(`${roomName('s04')} voltou para Videoconferência`)).toBeVisible()
  for (const agenda of [a, b]) {
    await expect.poll(() => groupOf(agenda)).toBe('video')
    await expect(agenda.header('s04').getByText(/Vídeo em manutenção/)).toHaveCount(0)
    await expect(agenda.block(needsVideo.id)).not.toHaveAccessibleName(/Precisa de vídeo$/)
  }
})
