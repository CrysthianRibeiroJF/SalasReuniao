import { DAYS, USERS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda } from './support/grid'

/**
 * Ciclo de vida do modal rápido em telas menores (barras de rolagem visíveis, como num notebook
 * 1366×768 com zoom): rolar a grade pela barra não descarta o que foi digitado (R16), e sair da grade
 * pelo histórico do navegador encerra a tentativa de reserva (R8).
 */
test.use({
  viewport: { width: 1280, height: 620 },
  // O Chromium headless esconde as barras de rolagem por padrão.
  launchOptions: { ignoreDefaultArgs: ['--hide-scrollbars'] },
})

test('R16: arrastar a barra de rolagem da grade não fecha o modal nem perde o que foi digitado', async ({
  ana,
  anaApi,
}) => {
  const date = DAYS.composerLifecycle
  await anaApi.clearDay(date)
  const agenda = new Agenda(ana)
  await agenda.open(date)
  const grid = agenda.grid(date)

  await agenda.clickCell('s05', '09:00')
  const composer = agenda.composer()
  await expect(composer).toBeVisible()
  await ana.keyboard.type('123456')
  await ana.keyboard.press('Tab')
  await ana.keyboard.type('Diretoria Financeira')

  const m = await grid.evaluate((el: HTMLElement) => ({
    sw: el.scrollWidth,
    cw: el.clientWidth,
    sh: el.scrollHeight,
    ch: el.clientHeight,
    ow: el.offsetWidth,
    oh: el.offsetHeight,
    top: el.scrollTop,
    left: el.scrollLeft,
  }))
  test.skip(m.sh <= m.ch && m.sw <= m.cw, 'a grade coube inteira na tela: não há barra de rolagem')
  const box = (await grid.boundingBox())!
  const mouse = ana.mouse
  if (m.sh > m.ch) {
    // barra vertical: faixa entre clientWidth e offsetWidth
    const x = box.x + m.cw + (m.ow - m.cw) / 2
    await mouse.move(x, box.y + 90)
    await mouse.down()
    await mouse.move(x, box.y + 150, { steps: 5 })
    await mouse.up()
  } else {
    const y = box.y + m.ch + (m.oh - m.ch) / 2
    await mouse.move(box.x + 120, y)
    await mouse.down()
    await mouse.move(box.x + 220, y, { steps: 5 })
    await mouse.up()
  }
  const after = await grid.evaluate((el) => ({ top: el.scrollTop, left: el.scrollLeft }))
  expect(after.top !== m.top || after.left !== m.left).toBe(true) // a grade rolou de fato
  await expect(composer).toBeVisible()
  await expect(composer.getByLabel('Nº do Chamado')).toHaveValue('123456')
  await expect(composer.getByLabel(/Solicitante/)).toHaveValue('Diretoria Financeira')
  await ana.keyboard.press('Escape')
  await expect(composer).toBeHidden()
})

test('R8: Voltar/Avançar com o modal aberto não traz o modal de volta sem trava', async ({ ana, bia, anaApi }) => {
  const date = DAYS.composerLifecycle
  await anaApi.clearDay(date)
  const a = new Agenda(ana)
  const b = new Agenda(bia)
  await b.open(date)
  await a.open(date)
  // Uma entrada da própria SPA no histórico: Auditoria, e Voltar para a grade.
  await ana.getByRole('button', { name: `Menu de ${USERS.ana.fullName}` }).click()
  await ana.getByRole('menuitem', { name: 'Auditoria' }).click()
  await expect(ana.getByRole('heading', { name: 'Auditoria', level: 1 })).toBeVisible()
  await ana.goBack()
  await a.waitForDay(date)
  await a.waitConnected()

  await a.clickCell('s06', '14:00')
  await expect(a.composer()).toBeVisible()
  await expect(b.locks('s06')).toHaveCount(1)

  await ana.goForward() // sai da grade com o modal aberto: a trava é liberada
  await expect(ana.getByRole('heading', { name: 'Auditoria', level: 1 })).toBeVisible()
  await expect(b.locks('s06')).toHaveCount(0)

  await ana.goBack() // volta: o modal não reaparece (sem trava e sem o que foi digitado)
  await a.waitForDay(date)
  await expect(a.composer()).toHaveCount(0)
  await expect(b.locks('s06')).toHaveCount(0)
})
