import path from 'node:path'

import type { Page } from '@playwright/test'

import { DAYS, USERS } from './support/data'
import { SCREENSHOTS_DIR } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda, capture, escapeRegExp, tabLabel } from './support/grid'

/** Botões visíveis com esse nome que estão cobertos por outro elemento (ex.: um aviso) no centro deles. */
async function coveredButtons(page: Page, name: string): Promise<string[]> {
  return page.getByRole('button', { name, exact: true }).evaluateAll((buttons) =>
    buttons
      .filter((button) => {
        const r = button.getBoundingClientRect()
        if (r.bottom <= 0 || r.top >= window.innerHeight || r.width === 0) return false
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
        return !button.contains(hit)
      })
      .map((button) => button.closest('tr')?.textContent ?? '?'),
  )
}

test.describe('administração', () => {
  test('salas: cadastrar, editar e desativar (a grade da Bia acompanha)', async ({ adminPage: page, bia }) => {
    const suffix = Date.now().toString(36).slice(-4)
    const name = `Sala E2E ${suffix}`
    const renamed = `Sala E2E ${suffix} Editada`
    const b = new Agenda(bia)
    await b.open(DAYS.layout)
    const header = (room: string) => bia.getByRole('columnheader', { name: new RegExp(`^${escapeRegExp(room)}(,|$)`) })

    await page.goto('/admin/salas')
    await expect(page.getByRole('heading', { name: 'Salas', level: 1 })).toBeVisible()
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'admin-salas.png') })

    // Nome repetido é recusado no campo.
    await page.getByRole('button', { name: 'Nova sala' }).click()
    let dialog = page.getByRole('dialog', { name: 'Nova sala' })
    await dialog.getByLabel('Nome').fill('sala 01 - executiva')
    await dialog.getByRole('button', { name: 'Cadastrar sala' }).click()
    await expect(dialog.getByText('Já existe uma sala chamada "sala 01 - executiva".')).toBeVisible()
    await expect(dialog.getByLabel('Nome')).toBeFocused()

    // Cadastrar
    await dialog.getByLabel('Nome').fill(name)
    await dialog.getByRole('radiogroup', { name: 'Tipo de sala' }).getByRole('radio', { name: 'Normal' }).click()
    await dialog.getByLabel('Capacidade').fill('5')
    await dialog.getByLabel('Recursos fixos').fill('TV 40"')
    await dialog.getByLabel('Recursos fixos').press('Enter')
    await dialog.getByLabel('Recursos fixos').fill('Quadro branco')
    await dialog.getByLabel('Recursos fixos').press(',')
    await expect(dialog.getByRole('button', { name: 'Remover TV 40"' })).toBeVisible()
    await dialog.getByRole('button', { name: 'Cadastrar sala' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.locator('[aria-live="polite"]').getByText('Sala cadastrada')).toBeVisible()
    // K8: com o aviso na tela, os botões "Editar" das últimas linhas continuam clicáveis (tela baixa,
    // tabela rolada até o fim: o aviso fica longe da coluna de ações).
    await page.setViewportSize({ width: 1366, height: 560 })
    await page.locator('main').evaluate((el) => el.scrollTo(0, el.scrollHeight))
    expect(await coveredButtons(page, 'Editar')).toEqual([])
    await page.setViewportSize({ width: 1366, height: 768 })
    const row = page.getByRole('row').filter({ hasText: name })
    await expect(row).toContainText('Normal')
    await expect(row).toContainText('TV 40"Quadro branco')
    await expect(row).toContainText('Ativa')
    // Aparece na grade da Bia sem recarregar (ROOM_CREATED), com capacidade no rótulo.
    await expect(header(name)).toHaveAccessibleName(`${name}, 5 lugares`)

    // Editar nome e capacidade
    await row.getByRole('button', { name: 'Editar' }).click()
    dialog = page.getByRole('dialog', { name: `Editar ${name}` })
    await dialog.getByLabel('Nome').fill(renamed)
    await dialog.getByLabel('Capacidade').fill('7')
    await dialog.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.locator('[aria-live="polite"]').getByText('Sala atualizada')).toBeVisible()
    const renamedRow = page.getByRole('row').filter({ hasText: renamed })
    await expect(renamedRow).toContainText('7')
    await expect(header(renamed)).toHaveAccessibleName(`${renamed}, 7 lugares`)
    await expect(header(name)).toHaveCount(0)

    // Desativar: some da grade, continua listada como inativa.
    await renamedRow.getByRole('button', { name: 'Editar' }).click()
    dialog = page.getByRole('dialog', { name: `Editar ${renamed}` })
    const active = dialog.getByRole('switch', { name: /Sala ativa/ })
    await expect(active).toHaveAttribute('aria-checked', 'true')
    await active.click()
    await expect(dialog).toContainText('Some da grade (o histórico é mantido)')
    await dialog.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(dialog).toBeHidden()
    await expect(renamedRow).toContainText('Inativa')
    await expect(header(renamed)).toHaveCount(0)
    await expect(page.getByRole('switch', { name: /Mostrar salas inativas/ })).toBeVisible()
  })

  test('salas: desativar com reservas futuras lista as reservas (com link para a grade) e só desativa depois', async ({
    adminPage: page,
    adminApi,
    biaApi,
    bia,
  }) => {
    const suffix = Date.now().toString(36).slice(-4)
    const name = `Sala E2E Futura ${suffix}`
    const room = await adminApi.createRoom({ name, room_type: 'NORMAL', capacity: 6 })
    const future = await biaApi.createBooking({
      room_id: room.id,
      date: DAYS.roomDeactivation,
      period_type: 'CUSTOM',
      start: '10:00',
      end: '11:00',
      ticket_number: '950001',
      requester_name: 'Helena Futura',
    })
    // Reserva que já aconteceu: não impede a desativação e continua no histórico/grade daquele dia.
    const past = await biaApi.createBooking({
      room_id: room.id,
      date: DAYS.inactiveRoomPast,
      period_type: 'CUSTOM',
      start: '09:00',
      end: '10:00',
      requester_name: 'Reunião Antiga',
    })

    await page.goto('/admin/salas')
    const row = page.getByRole('row').filter({ hasText: name })
    await row.getByRole('button', { name: 'Editar' }).click()
    const dialog = page.getByRole('dialog', { name: `Editar ${name}` })
    await dialog.getByRole('switch', { name: /Sala ativa/ }).click()
    await dialog.getByRole('button', { name: 'Salvar alterações' }).click()

    // D1: 409 ROOM_HAS_FUTURE_BOOKINGS → lista com data, horário, solicitante e chamado; nada foi salvo.
    const notice = dialog.getByRole('alert', { name: 'Reservas que impedem a desativação' })
    await expect(notice).toBeVisible()
    const item = notice.getByRole('listitem').filter({ hasText: 'Helena Futura' })
    await expect(item).toContainText(`${tabLabel(DAYS.roomDeactivation)} · 10:00–11:00`)
    await expect(item).toContainText('#950001')
    await expect(notice).not.toContainText('Reunião Antiga')
    await expect(row).toContainText('Ativa')
    await capture(page, 'admin-sala-com-reservas-futuras')

    // O link abre a reserva na grade (outra aba), no dia dela e destacada; lá ela é cancelada.
    const [grid] = await Promise.all([
      page.context().waitForEvent('page'),
      item.getByRole('link', { name: /Abrir na grade/ }).click(),
    ])
    const agenda = new Agenda(grid)
    await agenda.waitForDay(DAYS.roomDeactivation)
    await expect(grid).toHaveURL(new RegExp(`\\?date=${DAYS.roomDeactivation}$`))
    await expect(agenda.block(future.id)).toHaveClass(/animate-highlight/)
    await agenda.block(future.id).click()
    const popover = agenda.popover('Helena Futura')
    await popover.getByRole('button', { name: 'Cancelar', exact: true }).click()
    await popover.getByRole('button', { name: 'Cancelar reserva' }).click()
    await expect(agenda.block(future.id)).toHaveCount(0)
    await grid.close()

    // Sem reservas por vir, a mesma ação desativa.
    await dialog.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(dialog).toBeHidden()
    await expect(row).toContainText('Inativa')

    // A reserva antiga aparece numa coluna "Sala desativada" só no dia dela (grade, abas e busca batem).
    const b = new Agenda(bia)
    await b.open(DAYS.inactiveRoomPast)
    const header = bia.getByRole('columnheader', { name: new RegExp(`^${escapeRegExp(name)}`) })
    await expect(header).toContainText('Sala desativada')
    await expect(b.block(past.id)).toBeVisible()
    await capture(bia, 'grade-sala-desativada')
    await b.open(DAYS.roomDeactivation)
    await expect(header).toHaveCount(0)
  })

  test('usuários: criar, editar papel, desativar (e o desativado não entra)', async ({ adminPage: page, browser }) => {
    const suffix = Date.now().toString(36).slice(-4)
    const username = `carla.${suffix}`
    await page.goto('/admin/usuarios')
    await expect(page.getByRole('heading', { name: 'Usuários', level: 1 })).toBeVisible()
    const meRow = page.getByRole('row').filter({ hasText: USERS.admin.fullName })
    await expect(meRow).toContainText('(você)')
    await expect(meRow.getByRole('button', { name: 'Desativar' })).toBeDisabled()

    // Usuário repetido
    await page.getByRole('button', { name: 'Novo usuário' }).click()
    let dialog = page.getByRole('dialog', { name: 'Novo usuário' })
    await dialog.getByLabel('Usuário').fill(USERS.ana.username)
    await dialog.getByLabel('Nome completo').fill('Outra Ana')
    await dialog.getByLabel('Senha').fill('senha-123')
    await dialog.getByRole('button', { name: 'Criar usuário' }).click()
    await expect(dialog.getByText(`O usuário "${USERS.ana.username}" já existe.`)).toBeVisible()

    // Criar
    await dialog.getByLabel('Usuário').fill(username)
    await dialog.getByLabel('Nome completo').fill('Carla Nova')
    await dialog.getByRole('button', { name: 'Criar usuário' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.locator('[aria-live="polite"]').getByText('Usuário criado')).toBeVisible()
    let row = page.getByRole('row').filter({ hasText: username })
    await expect(row).toContainText('Carla Nova')
    await expect(row).toContainText('Recepção')
    await expect(row).toContainText('Ativo')
    await expect(row).toContainText('nunca')

    // A conta nova entra.
    const context = await browser.newContext()
    const other = await context.newPage()
    await other.goto('/login')
    await other.getByLabel('Usuário').fill(username)
    await other.getByLabel('Senha', { exact: true }).fill('senha-123')
    await other.getByRole('button', { name: 'Entrar' }).click()
    await new Agenda(other).waitConnected()
    await other.getByRole('button', { name: 'Menu de Carla Nova' }).click()
    await other.getByRole('menuitem', { name: 'Sair' }).click()
    await expect(other).toHaveURL(/\/login$/)

    // Editar nome e papel
    await page.reload()
    row = page.getByRole('row').filter({ hasText: username })
    await expect(row).not.toContainText('nunca')
    await row.getByRole('button', { name: 'Editar' }).click()
    dialog = page.getByRole('dialog', { name: 'Editar Carla Nova' })
    await dialog.getByLabel('Nome completo').fill('Carla Editada')
    await dialog.getByRole('radiogroup', { name: 'Papel' }).getByRole('radio', { name: 'Administração' }).click()
    await dialog.getByRole('button', { name: 'Salvar alterações' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.locator('[aria-live="polite"]').getByText('Usuário atualizado')).toBeVisible()
    await expect(row).toContainText('Carla Editada')
    await expect(row).toContainText('Administração')

    // Desativar: não consegue mais entrar.
    await row.getByRole('button', { name: 'Desativar' }).click()
    await expect(page.locator('[aria-live="polite"]').getByText('Carla Editada foi desativado(a)')).toBeVisible()
    await expect(row).toContainText('Desativado')
    await expect(row.getByRole('button', { name: 'Reativar' })).toBeVisible()
    await other.getByLabel('Usuário').fill(username)
    await other.getByLabel('Senha', { exact: true }).fill('senha-123')
    await other.getByRole('button', { name: 'Entrar' }).click()
    await expect(other.getByRole('alert')).toHaveText('Usuário ou senha inválidos.')
    await context.close()
  })
})
