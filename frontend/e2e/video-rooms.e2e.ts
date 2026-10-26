import { DAYS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, capture, roomName } from './support/grid'

test.describe('RF06: sala de vídeo sem necessidade de vídeo', () => {
  test.beforeEach(async ({ anaApi }) => {
    await anaApi.clearDay(DAYS.videoRooms)
  })

  test('o aviso lista salas normais livres e "Usar Sala X" troca a sala', async ({ ana, anaApi, ids }) => {
    const date = DAYS.videoRooms
    const agenda = new Agenda(ana)
    await agenda.open(date)

    await agenda.clickCell('s02', '10:00')
    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    const video = composer.getByRole('switch', { name: /Precisa de Videoconferência\?/ })
    await expect(video).toHaveAttribute('aria-checked', 'false')
    const notice = composer.getByRole('status').filter({ hasText: 'Esta é uma sala de videoconferência' })
    await expect(notice).toContainText('a reunião não precisa de vídeo. Há salas normais livres neste horário')
    await expect(notice.getByRole('button', { name: `Usar ${roomName('s05')}` })).toBeVisible()
    await expect(notice.getByRole('button', { name: /^Usar Sala 0[1-4]/ })).toHaveCount(0)
    await capture(ana, 'aviso-sala-de-video')

    // Marcar "Sim" some com o aviso; voltar para "Não" traz de volta.
    await video.click()
    await expect(video).toHaveAttribute('aria-checked', 'true')
    await expect(notice).toBeHidden()
    await video.click()
    await expect(notice).toBeVisible()

    await notice.getByRole('button', { name: `Usar ${roomName('s05')}` }).click()
    await expect(composer).toContainText(`${roomName('s05')}`)
    await expect(notice).toBeHidden()
    // A seleção (e a trava) acompanha a nova sala.
    await expect(agenda.column('s05').locator('[data-selection]')).toBeVisible()
    await expect(agenda.column('s02').locator('[data-selection]')).toHaveCount(0)

    await composer.getByLabel('Solicitante').fill('Equipe Presencial')
    await ana.keyboard.press('Enter')
    await expect(composer).toBeHidden()
    await expect(agenda.blocks('s05', /Equipe Presencial, 10:00 às 10:30/)).toHaveCount(1)
    await expect(agenda.blocks('s02')).toHaveCount(0)
    const [saved] = await anaApi.bookings(date)
    expect(saved).toMatchObject({ room_id: ids.rooms.s05, requires_video: false, is_relocatable: false })
  })

  test('reunião presencial salva em sala de vídeo fica "Remanejável" e pode ser arrastada para uma sala normal', async ({
    ana,
    anaApi,
    ids,
  }) => {
    const date = DAYS.videoRooms
    const agenda = new Agenda(ana)
    await agenda.open(date)

    await agenda.clickCell('s03', '14:00')
    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    await composer.getByLabel('Nº do Chamado').fill('950001')
    await composer.getByLabel('Solicitante').fill('Ricardo Alves')
    await composer.getByLabel('Assunto/Área').fill('Treinamento Comercial')
    await composer.getByLabel('Fim').selectOption('16:00')
    await composer.getByRole('button', { name: 'Salvar', exact: true }).click()
    await expect(composer).toBeHidden()
    await expect(agenda.toast('Reserva criada')).toBeVisible()
    // Aviso não bloqueante do servidor (VIDEO_ROOM_NOT_NEEDED).
    await expect(agenda.toast(/não precisa de videoconferência e há salas normais livres/)).toBeVisible()

    const block = agenda.blocks('s03', /Ricardo Alves/)
    await expect(block).toHaveAccessibleName(/14:00 às 16:00, Remanejável$/)
    await expect(block.getByText('Reunião Presencial — Remanejável')).toBeVisible()
    const [created] = await anaApi.bookings(date)
    expect(created).toMatchObject({ room_id: ids.rooms.s03, is_relocatable: true, requires_video: false })

    // Arrastar para uma sala normal livre no mesmo horário.
    await agenda.dragBlockTo(block, 's07', '14:00')
    await expect(agenda.toast('Reserva remanejada')).toBeVisible()
    const moved = agenda.blocks('s07', /Ricardo Alves, Treinamento Comercial, 14:00 às 16:00/)
    await expect(moved).toHaveCount(1)
    await expect(agenda.blocks('s03')).toHaveCount(0)
    await expect(moved).not.toHaveAccessibleName(/Remanejável/)

    // Persistido: recarregar mostra a reserva na sala nova; a auditoria registra MOVED_ROOM.
    await ana.reload()
    await agenda.waitForDay(date)
    await expect(agenda.blocks('s07', /Ricardo Alves, Treinamento Comercial, 14:00 às 16:00/)).toHaveCount(1)
    await expect(agenda.blocks('s03')).toHaveCount(0)
    const logs = await anaApi.logs(created!.id)
    expect(logs.map((l) => l.action)).toEqual(['CREATED', 'MOVED_ROOM'])
    expect(logs[1]).toMatchObject({
      user: { full_name: 'Ana Recepção' },
      previous_state: { room_name: roomName('s03') },
      new_state: { room_name: roomName('s07') },
    })

    // E o histórico da reserva mostra a troca de sala.
    await agenda.blocks('s07', /Ricardo Alves/).click()
    const popover = agenda.popover('Ricardo Alves')
    await popover.getByRole('tab', { name: 'Histórico' }).click()
    await expect(popover.getByRole('listitem').first()).toContainText('Trocou de sala · Ana Recepção')
    await expect(popover.getByRole('listitem').first()).toContainText(`${roomName('s03')} → ${roomName('s07')}`)
    await ana.keyboard.press('Escape')

    // A tela de Auditoria (aberta a todos) lista a troca de sala, com o diff nos detalhes.
    await ana.goto('/admin/auditoria')
    await ana.getByRole('combobox', { name: 'Ação', exact: true }).selectOption({ label: 'Trocou de sala' })
    await ana.getByLabel('Dia da reserva', { exact: true }).fill(date)
    const entry = ana.getByRole('row').filter({ hasText: 'Ricardo Alves' })
    await expect(entry).toHaveCount(1)
    await expect(entry).toContainText('Ana Recepção')
    await expect(entry).toContainText('Trocou de sala')
    await expect(entry).toContainText('#950001')
    await expect(entry).toContainText(`${roomName('s03')} → ${roomName('s07')}`)
    await entry.getByRole('button', { name: 'Detalhes' }).click()
    await expect(ana.getByRole('cell', { name: roomName('s03'), exact: true })).toBeVisible()
  })
})
