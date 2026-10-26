import type { Page } from '@playwright/test'

import { DAYS, USERS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, capture, tabLabel } from './support/grid'

/** A página em si nunca rola: só a grade, por dentro. */
async function expectPageDoesNotScroll(page: Page) {
  const metrics = await page.evaluate(() => {
    const doc = document.scrollingElement!
    return { scrollHeight: doc.scrollHeight, scrollWidth: doc.scrollWidth, height: innerHeight, width: innerWidth }
  })
  expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.height + 1)
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.width + 1)
}

test('layout em 1366×768: 4 zonas fixas, página sem rolagem e grade inteira à vista', async ({ ana, ids }) => {
  const agenda = new Agenda(ana)
  await agenda.open(DAYS.layout)
  await expectPageDoesNotScroll(ana)

  const grid = agenda.grid(DAYS.layout)
  const top = ana.getByRole('banner')
  const tabs = ana.getByRole('navigation', { name: 'Dias da semana' })
  const viewport = ana.viewportSize()!
  const [topBox, gridBox, tabsBox] = await Promise.all([top.boundingBox(), grid.boundingBox(), tabs.boundingBox()])
  // Barra superior no topo, abas no rodapé e a grade ocupando o meio inteiro.
  expect(topBox!.y).toBe(0)
  expect(Math.round(tabsBox!.y + tabsBox!.height)).toBe(viewport.height)
  expect(Math.round(gridBox!.y)).toBe(Math.round(topBox!.y + topBox!.height))
  expect(Math.round(gridBox!.y + gridBox!.height)).toBe(Math.round(tabsBox!.y))
  expect(await grid.evaluate((el) => getComputedStyle(el).overflow)).toBe('auto')

  // Nesta resolução as 22 linhas (08:00–18:30) e as 10 salas cabem sem rolar.
  await expect(agenda.cell('s10', '18:30')).toBeInViewport({ ratio: 1 })
  await expect(agenda.cell('s01', '08:00')).toBeInViewport({ ratio: 1 })
  await expect(ana.getByRole('columnheader', { name: /^Videoconferência/ })).toBeVisible()
  await expect(ana.getByRole('columnheader', { name: /^Salas Normais/ })).toBeVisible()
  await expect(agenda.block(ids.bookings.layout2!)).toHaveAccessibleName(/Remanejável$/)
  // Nenhuma linha de cabeçalho de bloco (chamado + horário) é cortada no meio pela largura da coluna.
  const clipped = await ana
    .locator('[data-booking-id] > div:first-child')
    .evaluateAll((rows) => rows.filter((row) => row.scrollWidth > row.clientWidth + 1).length)
  expect(clipped).toBe(0)
  await capture(ana, 'grade-dia')

  // Modal rápido ancorado na célula e popover de uma reserva.
  await agenda.clickCell('s06', '14:00')
  await expect(agenda.composer()).toBeVisible()
  await ana.keyboard.type('123456')
  await ana.keyboard.press('Tab')
  await ana.keyboard.type('Maria Souza')
  await expectPageDoesNotScroll(ana)
  await capture(ana, 'modal-nova-reserva')
  await ana.keyboard.press('Escape')
  await expect(agenda.composer()).toBeHidden()

  // No canto inferior direito da grade o modal continua inteiro dentro da tela.
  await agenda.clickCell('s10', '18:30')
  const composerBox = (await agenda.composer().boundingBox())!
  const viewportSize = ana.viewportSize()!
  expect(composerBox.x).toBeGreaterThanOrEqual(0)
  expect(composerBox.y).toBeGreaterThanOrEqual(0)
  expect(composerBox.x + composerBox.width).toBeLessThanOrEqual(viewportSize.width)
  expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(viewportSize.height)
  await expect(agenda.composer().getByRole('button', { name: 'Salvar', exact: true })).toBeInViewport()
  await ana.keyboard.press('Escape')
  await expect(agenda.composer()).toBeHidden()

  await agenda.block(ids.bookings.layout4!).click()
  await expect(agenda.popover('Integração')).toBeVisible()
  await expectPageDoesNotScroll(ana)
  await capture(ana, 'popover-reserva')
  await ana.keyboard.press('Escape')
})

test('tela mais baixa: a grade rola por dentro com cabeçalho e horários fixos', async ({ ana }) => {
  await ana.setViewportSize({ width: 1366, height: 600 })
  const agenda = new Agenda(ana)
  await agenda.open(DAYS.layout, { realtime: false })
  await expectPageDoesNotScroll(ana)
  const grid = agenda.grid(DAYS.layout)
  const sizes = await grid.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
  expect(sizes.scroll).toBeGreaterThan(sizes.client)
  await expect(agenda.cell('s01', '18:30')).not.toBeInViewport()

  const header = agenda.header('s01')
  const before = (await header.boundingBox())!.y
  await grid.hover()
  await ana.mouse.wheel(0, 2_000)
  await expect.poll(() => grid.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
  await expect(agenda.cell('s01', '18:30')).toBeInViewport()
  // Cabeçalho das salas e coluna de horários são "sticky"; a página continua parada.
  expect((await header.boundingBox())!.y).toBe(before)
  await expect(ana.getByText('18:30', { exact: true }).first()).toBeInViewport()
  expect(await ana.evaluate(() => document.scrollingElement!.scrollTop)).toBe(0)
  await expectPageDoesNotScroll(ana)
})

test('capturas: login e abas da semana', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'Entrar' })).toBeVisible()
  await capture(page, 'login')
  await page.getByLabel('Usuário').fill(USERS.bia.username)
  await page.getByLabel('Senha', { exact: true }).fill(USERS.bia.password)
  await page.keyboard.press('Enter')
  const agenda = new Agenda(page)
  await agenda.waitConnected()
  await page.goto(`/?date=${DAYS.weekA.mon}`)
  await agenda.waitForDay(DAYS.weekA.mon)
  await agenda.waitConnected()
  await expect(agenda.tab(DAYS.weekA.mon)).toHaveText(new RegExp(`^${tabLabel(DAYS.weekA.mon)}`))
  await capture(page, 'grade-semana-abas')
})
