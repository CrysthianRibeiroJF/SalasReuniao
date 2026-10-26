import type { Locator, Page } from '@playwright/test'

import { DAYS, type RoomKey } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, capture, roomName, tabLabel } from './support/grid'

/** A caixa do elemento está inteira dentro da área visível da grade (que rola sozinha). */
async function expectInsideGrid(page: Page, grid: Locator, element: Locator) {
  await expect
    .poll(async () => {
      const g = await grid.boundingBox()
      const e = await element.boundingBox()
      if (!g || !e) return false
      return (
        e.x >= g.x - 1 && e.y >= g.y - 1 && e.x + e.width <= g.x + g.width + 1 && e.y + e.height <= g.y + g.height + 1
      )
    })
    .toBe(true)
  // A página em si não rolou: só a grade.
  expect(await page.evaluate(() => document.scrollingElement!.scrollTop)).toBe(0)
}

test.describe('busca', () => {
  // Tela menor que a grade: para mostrar a reserva encontrada a grade precisa rolar.
  test.use({ viewport: { width: 1024, height: 640 } })

  test('por nº do chamado de outro dia: abre a aba do dia, rola até a reserva e destaca', async ({
    ana,
    anaApi,
    ids,
  }) => {
    const agenda = new Agenda(ana)
    await agenda.open(DAYS.weekA.mon)
    const grid = agenda.grid(DAYS.weekA.fri)

    // Atalho "/" foca a busca.
    await agenda.grid(DAYS.weekA.mon).focus()
    await ana.keyboard.press('/')
    const search = ana.getByRole('combobox', { name: 'Buscar reserva por chamado, solicitante ou assunto' })
    await expect(search).toBeFocused()
    await ana.keyboard.type('#700503')
    const results = ana.getByRole('listbox', { name: 'Resultados da busca' })
    const option = results.getByRole('option')
    await expect(option).toHaveCount(1)
    await expect(option).toContainText('#700503')
    await expect(option).toContainText('Camila Teixeira')
    await expect(option).toContainText(`${tabLabel(DAYS.weekA.fri)} · 17:30–18:30`)
    await expect(option).toContainText(roomName('s10'))
    await ana.keyboard.press('Enter')

    await expect(ana).toHaveURL(new RegExp(`\\?date=${DAYS.weekA.fri}$`))
    await expect(agenda.tab(DAYS.weekA.fri)).toHaveAttribute('aria-selected', 'true')
    await agenda.waitForDay(DAYS.weekA.fri)
    const block = agenda.block(ids.bookings.camila!)
    await expect(block).toHaveClass(/animate-highlight/)
    await expectInsideGrid(ana, grid, block)
    expect(await grid.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)
    await capture(ana, 'busca-destaque')
    // O destaque é temporário — mesmo que cheguem eventos do tempo real no meio dele (uma colega
    // agendando e cancelando em outro dia).
    const other = await anaApi.createBooking({
      room_id: ids.rooms.s10,
      date: DAYS.editing,
      period_type: 'CUSTOM',
      start: '08:00',
      end: '08:30',
      requester_name: 'Evento durante o destaque',
    })
    await anaApi.cancelBooking(other.id, 'limpeza do teste E2E')
    await expect(block).not.toHaveClass(/animate-highlight/, { timeout: 6_000 })
  })

  test('Enter logo depois de digitar (antes de a busca responder) abre o resultado certo', async ({ ana, ids }) => {
    const agenda = new Agenda(ana)
    await agenda.open(DAYS.weekA.fri, { realtime: false })
    const search = ana.getByRole('combobox', { name: /Buscar reserva/ })
    await search.click()
    // Primeiro um termo que acha várias reservas; depois, rápido, o chamado exato + Enter.
    await search.pressSequentially('7005')
    await expect(ana.getByRole('listbox', { name: 'Resultados da busca' }).getByRole('option')).toHaveCount(4)
    await search.pressSequentially('03')
    await search.press('Enter')
    await expect(agenda.block(ids.bookings.camila!)).toHaveClass(/animate-highlight/)
    await expect(agenda.block(ids.bookings.rafael!)).not.toHaveClass(/animate-highlight/)

    // Do zero: digitar e Enter na hora, sem esperar a lista aparecer.
    await search.click()
    await search.pressSequentially('#700301')
    await search.press('Enter')
    await expect(ana).toHaveURL(new RegExp(`\\?date=${DAYS.weekA.wed}$`))
    await agenda.waitForDay(DAYS.weekA.wed)
    await expect(agenda.block(ids.bookings.sonia!)).toHaveClass(/animate-highlight/)
  })

  test('por nome do solicitante, sem acentos nem maiúsculas', async ({ ana, ids }) => {
    const agenda = new Agenda(ana)
    await agenda.open(DAYS.weekA.fri, { realtime: false })
    const search = ana.getByRole('combobox', { name: /Buscar reserva/ })
    await ana.keyboard.press('Control+k')
    await expect(search).toBeFocused()
    await ana.keyboard.type('SONIA arauj')
    const option = ana.getByRole('listbox', { name: 'Resultados da busca' }).getByRole('option')
    await expect(option).toHaveCount(1)
    await expect(option).toContainText('Sônia Araújo')
    await option.click()

    await expect(ana).toHaveURL(new RegExp(`\\?date=${DAYS.weekA.wed}$`))
    await agenda.waitForDay(DAYS.weekA.wed)
    await expect(agenda.block(ids.bookings.sonia!)).toHaveClass(/animate-highlight/)
    await expectInsideGrid(ana, agenda.grid(DAYS.weekA.wed), agenda.block(ids.bookings.sonia!))

    // "joao brand" acha "João Brandão"; termo sem resultado mostra o aviso.
    await search.fill('joao brand')
    await expect(ana.getByRole('option')).toHaveCount(1)
    await expect(ana.getByRole('option')).toContainText('João Brandão')
    await search.fill('ninguém-com-esse-nome')
    await expect(ana.getByText('Nenhuma reserva encontrada para “ninguém-com-esse-nome”.')).toBeVisible()
    await ana.keyboard.press('Escape')
  })
})

test('filtros: Videoconferência, Salas Normais e Livres Dia Inteiro', async ({ ana }) => {
  const agenda = new Agenda(ana)
  await agenda.open(DAYS.weekA.mon, { realtime: false })
  const filters = ana.getByRole('radiogroup', { name: 'Filtrar salas' })

  // Sala 04 fica de fora das asserções: o teste de manutenção (RF02) muda o tipo efetivo dela.
  const video: RoomKey[] = ['s01', 's02', 's03']
  const normal: RoomKey[] = ['s05', 's06', 's07', 's08', 's09', 's10']
  async function expectRooms(visible: RoomKey[], hidden: RoomKey[]) {
    for (const room of visible) await expect(agenda.header(room)).toBeVisible()
    for (const room of hidden) await expect(agenda.header(room)).toHaveCount(0)
  }

  await filters.getByRole('radio', { name: 'Vídeo' }).click()
  await expect(filters.getByRole('radio', { name: 'Vídeo' })).toHaveAttribute('aria-checked', 'true')
  await expectRooms(video, normal)
  await expect(ana.getByRole('columnheader', { name: /Salas Normais/ })).toHaveCount(0)

  await filters.getByRole('radio', { name: 'Normais' }).click()
  await expectRooms(normal, video)

  // Seg 09/11 tem reservas nas salas 01, 05 e 09.
  await filters.getByRole('radio', { name: 'Livres Dia Inteiro' }).click()
  await expectRooms(['s02', 's03', 's06', 's07', 's08', 's10'], ['s01', 's05', 's09'])
  await expect(ana.locator('[data-booking-id]')).toHaveCount(0)

  // O filtro vale por dia: na terça as salas 02 e 06 é que estão ocupadas.
  await agenda.tab(DAYS.weekA.tue).click()
  await agenda.waitForDay(DAYS.weekA.tue)
  await expectRooms(['s01', 's05', 's09'], ['s02', 's06'])

  await filters.getByRole('radio', { name: 'Todas' }).click()
  await expectRooms([...video, ...normal], [])
  await expect(ana.locator('[data-booking-id]')).toHaveCount(2)
})
