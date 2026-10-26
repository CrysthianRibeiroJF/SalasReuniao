import { DAYS, WEEK_A_COUNTS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, longDate, tabLabel } from './support/grid'

const WEEK = Object.values(DAYS.weekA)

function plural(n: number): string {
  return `${n} reserva${n === 1 ? '' : 's'}`
}

test('abas diárias: um clique por dia, contagens, setas de semana, Hoje e + Calendário', async ({ ana, anaApi }) => {
  const agenda = new Agenda(ana)
  await agenda.open(DAYS.weekA.mon)
  const tablist = ana.getByRole('tablist', { name: 'Dia exibido' })

  // Seg..Sex da semana exibida, cada aba com a quantidade de reservas do dia.
  await expect(tablist.getByRole('tab')).toHaveText(WEEK.map((d) => new RegExp(`^${tabLabel(d)}\\d+$`)))
  for (const date of WEEK) {
    await expect(agenda.tab(date)).toHaveAccessibleName(`${longDate(date)}, ${plural(WEEK_A_COUNTS[date]!)}`)
  }

  // Um clique em cada aba troca o dia exibido (URL, aba selecionada e grade).
  for (const date of [...WEEK.slice(1), WEEK[0]!]) {
    await agenda.tab(date).click()
    await expect(ana).toHaveURL(new RegExp(`\\?date=${date}$`))
    await expect(agenda.tab(date)).toHaveAttribute('aria-selected', 'true')
    await agenda.waitForDay(date)
    await expect(ana.locator('[data-booking-id]')).toHaveCount(WEEK_A_COUNTS[date]!)
  }

  // Setas: mesma posição na semana seguinte/anterior.
  await agenda.tab(DAYS.weekA.wed).click()
  await ana.getByRole('button', { name: 'Próxima semana' }).click()
  await expect(ana).toHaveURL(/\?date=2026-11-18$/)
  await expect(tablist.getByRole('tab')).toHaveText([
    /^Seg 16\/11/,
    /^Ter 17\/11/,
    /^Qua 18\/11/,
    /^Qui 19\/11/,
    /^Sex 20\/11/,
  ])
  await ana.getByRole('button', { name: 'Semana anterior' }).click()
  await ana.getByRole('button', { name: 'Semana anterior' }).click()
  await expect(ana).toHaveURL(/\?date=2026-11-04$/)
  await ana.getByRole('button', { name: 'Próxima semana' }).click()
  await expect(ana).toHaveURL(/\?date=2026-11-11$/)
  await agenda.waitForDay(DAYS.weekA.wed)

  // Hoje: o dia do servidor (fim de semana vira aba extra) e o botão fica desabilitado.
  const today = await anaApi.today()
  await ana.getByRole('button', { name: 'Hoje', exact: true }).click()
  await expect(ana).toHaveURL(new RegExp(`\\?date=${today}$`))
  await expect(agenda.tab(today)).toHaveAttribute('aria-selected', 'true')
  await expect(agenda.tab(today)).toHaveAccessibleName(/, hoje$/)
  await expect(ana.getByRole('button', { name: 'Hoje', exact: true })).toBeDisabled()

  // + Calendário até um sábado: aba extra temporária, selecionada.
  await ana.getByRole('button', { name: '+ Calendário' }).click()
  const picker = ana.getByRole('dialog', { name: 'Escolher data' })
  await picker.getByLabel('Ir para a data').fill(DAYS.weekASaturday)
  await picker.getByRole('button', { name: 'Abrir dia' }).click()
  await expect(picker).toBeHidden()
  await expect(ana).toHaveURL(new RegExp(`\\?date=${DAYS.weekASaturday}$`))
  await expect(tablist.getByRole('tab')).toHaveCount(6)
  await expect(agenda.tab(DAYS.weekASaturday)).toHaveAttribute('aria-selected', 'true')
  await expect(agenda.tab(DAYS.weekASaturday)).toHaveText(/^Sáb 14\/11/)
  await agenda.waitForDay(DAYS.weekASaturday)

  // A data fica na URL: recarregar mantém o dia (e a aba extra).
  await ana.reload()
  await agenda.waitForDay(DAYS.weekASaturday)
  await expect(agenda.tab(DAYS.weekASaturday)).toHaveAttribute('aria-selected', 'true')
  await expect(tablist.getByRole('tab')).toHaveCount(6)

  // Voltar a um dia útil some com a aba extra.
  await agenda.tab(DAYS.weekA.fri).click()
  await expect(tablist.getByRole('tab')).toHaveCount(5)
  await expect(agenda.tab(DAYS.weekA.fri)).toHaveAccessibleName(`${longDate(DAYS.weekA.fri)}, ${plural(4)}`)
})

test('teclado troca de dia (PageDown/PageUp e Ctrl+setas)', async ({ ana }) => {
  const agenda = new Agenda(ana)
  await agenda.open(DAYS.weekA.fri, { realtime: false })
  await agenda.grid(DAYS.weekA.fri).focus()
  await ana.keyboard.press('PageDown')
  // Sexta → segunda seguinte (pula o fim de semana).
  await expect(ana).toHaveURL(/\?date=2026-11-16$/)
  await ana.keyboard.press('PageUp')
  await expect(ana).toHaveURL(/\?date=2026-11-13$/)
  await ana.keyboard.press('Control+ArrowLeft')
  await expect(ana).toHaveURL(/\?date=2026-11-12$/)
  await ana.keyboard.press('Control+ArrowRight')
  await expect(ana).toHaveURL(/\?date=2026-11-13$/)
})

test.describe('navegador em outro fuso horário', () => {
  // Às 23h em São Paulo já é o dia seguinte em Tóquio: "hoje" tem de ser o da recepção.
  test.use({ timezoneId: 'Asia/Tokyo' })

  test('sem ?date abre o "hoje" da recepção (fuso do servidor), não o do navegador', async ({ ana, anaApi }) => {
    const agenda = new Agenda(ana)
    const today = await anaApi.today()
    await ana.goto('/')
    await expect(ana).toHaveURL(new RegExp(`\\?date=${today}$`))
    await agenda.waitForDay(today)
    await expect(agenda.tab(today)).toHaveAccessibleName(/, hoje$/)
    await expect(ana.getByRole('button', { name: 'Hoje', exact: true })).toBeDisabled()
  })
})
