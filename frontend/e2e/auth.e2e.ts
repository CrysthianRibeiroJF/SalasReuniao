import type { Browser } from '@playwright/test'

import type { AgendaApi } from './support/api'
import { DAYS, USERS } from './support/data'
import { BASE_URL } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda } from './support/grid'
import { storageStatePath } from './support/seed'

test.describe('login e permissões', () => {
  // Estas páginas começam sem sessão salva.
  test.use({ storageState: { cookies: [], origins: [] } })

  test('senha errada mostra a mensagem e não entra', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login$/)
    await page.getByLabel('Usuário').fill(USERS.ana.username)
    await page.getByLabel('Senha', { exact: true }).fill('senha-errada')
    await page.getByRole('button', { name: 'Entrar' }).click()
    await expect(page.getByRole('alert')).toHaveText('Usuário ou senha inválidos.')
    await expect(page).toHaveURL(/\/login$/)
    await page.getByLabel('Senha', { exact: true }).fill('')
    await page.getByRole('button', { name: 'Entrar' }).click()
    await expect(page.getByRole('alert')).toHaveText('Informe usuário e senha.')
  })

  test('recepcionista entra, não vê a administração e sai', async ({ page }) => {
    const agenda = new Agenda(page)
    await page.goto('/admin/salas')
    await expect(page).toHaveURL(/\/login$/)
    await page.getByLabel('Usuário').fill(USERS.ana.username)
    await page.getByLabel('Senha', { exact: true }).fill(USERS.ana.password)
    await page.keyboard.press('Enter')

    // Volta para onde ia, mas /admin/salas é só de admin: cai na Auditoria, sem o menu de seções.
    await expect(page).toHaveURL(/\/admin\/auditoria$/)
    await expect(page.getByRole('heading', { name: 'Auditoria', level: 1 })).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Seções da administração' })).toHaveCount(0)
    for (const section of ['/admin/usuarios', '/admin/importar']) {
      await page.goto(section)
      await expect(page).toHaveURL(/\/admin\/auditoria$/)
    }

    await page.getByRole('link', { name: 'Grade' }).click()
    await agenda.waitConnected()
    const menu = page.getByRole('button', { name: `Menu de ${USERS.ana.fullName}` })
    await menu.click()
    const items = page.getByRole('menu').getByRole('menuitem')
    await expect(items).toHaveText(['Grade de salas', 'Auditoria', 'Sair'])

    await page.getByRole('menuitem', { name: 'Sair' }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: 'Entrar' })).toBeVisible()
    // A sessão foi apagada: recarregar continua no login.
    await page.reload()
    await expect(page).toHaveURL(/\/login$/)
    await page.goto('/')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('sessão inválida/expirada volta ao login com aviso e, ao entrar, ao mesmo dia', async ({ browser }) => {
    const context = await browser.newContext({
      storageState: {
        cookies: [],
        origins: [
          {
            origin: BASE_URL,
            localStorage: [
              {
                name: 'agenda-salas:auth',
                value: JSON.stringify({
                  token: 'token-que-o-servidor-nao-reconhece',
                  expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
                  user: { id: 999, username: 'ana', full_name: 'Ana Recepção', role: 'RECEPTIONIST' },
                }),
              },
            ],
          },
        ],
      },
    })
    const page = await context.newPage()
    await page.goto(`/?date=${DAYS.weekA.tue}`)
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('status')).toHaveText('Sua sessão expirou. Entre novamente.')
    await page.getByLabel('Usuário').fill(USERS.ana.username)
    await page.getByLabel('Senha', { exact: true }).fill(USERS.ana.password)
    await page.getByRole('button', { name: 'Entrar' }).click()
    await expect(page).toHaveURL(new RegExp(`\\?date=${DAYS.weekA.tue}$`))
    await new Agenda(page).waitForDay(DAYS.weekA.tue)
    await context.close()
  })

  /** Cria um usuário só para o teste e abre a grade logada como ele (contexto próprio). */
  async function freshUser(browser: Browser, adminApi: AgendaApi, prefix: string) {
    const suffix = Date.now().toString(36).slice(-5)
    const username = `${prefix}.${suffix}`
    const password = 'senha-e2e-123'
    const user = await adminApi.createUser({ username, full_name: `Teste ${prefix} ${suffix}`, password })
    const context = await browser.newContext()
    const page = await context.newPage()
    await page.goto(`/login`)
    await page.getByLabel('Usuário').fill(username)
    await page.getByLabel('Senha', { exact: true }).fill(password)
    await page.getByRole('button', { name: 'Entrar' }).click()
    await new Agenda(page).waitConnected()
    return { user, username, page, context }
  }

  test('D2/D3: senha trocada pelo admin encerra a sessão aberta com o aviso próprio', async ({ browser, adminApi }) => {
    const { user, username, page, context } = await freshUser(browser, adminApi, 'troca')
    const agenda = new Agenda(page)
    await page.goto(`/?date=${DAYS.weekA.wed}`)
    await agenda.waitForDay(DAYS.weekA.wed)
    await adminApi.updateUser(user.id, { password: 'senha-nova-456' })
    // Próxima chamada ao servidor (semana seguinte → REST 401 SESSION_REVOKED; ou o tempo real fecha
    // com 4401 na revalidação): volta ao login com o aviso de senha alterada.
    await page.getByRole('button', { name: 'Próxima semana' }).click()
    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 })
    await expect(page.getByRole('status')).toHaveText(
      'Sua sessão foi encerrada porque a senha foi alterada. Entre novamente.',
    )
    // A mesma pessoa, com a senha nova, volta para onde estava.
    await page.getByLabel('Usuário').fill(username)
    await page.getByLabel('Senha', { exact: true }).fill('senha-nova-456')
    await page.getByRole('button', { name: 'Entrar' }).click()
    await expect(page).toHaveURL(/\?date=\d{4}-\d{2}-\d{2}$/)
    await agenda.waitConnected()
    await context.close()
  })

  test('D2: conta desativada encerra a sessão aberta com "acesso desativado"', async ({ browser, adminApi }) => {
    const { user, page, context } = await freshUser(browser, adminApi, 'desligada')
    const agenda = new Agenda(page)
    await page.goto(`/?date=${DAYS.weekA.wed}`)
    await agenda.waitForDay(DAYS.weekA.wed)
    await adminApi.updateUser(user.id, { is_active: false })
    await page.getByRole('button', { name: 'Próxima semana' }).click()
    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 })
    await expect(page.getByRole('status')).toHaveText('Seu acesso foi desativado. Fale com o administrador.')
    await context.close()
  })

  test('R14: sair numa aba sai também nas outras abas do mesmo navegador', async ({ browser }) => {
    const context = await browser.newContext({ storageState: storageStatePath('ana') })
    const a = await context.newPage()
    const b = await context.newPage()
    const agendaB = new Agenda(b)
    await new Agenda(a).open(DAYS.weekA.mon)
    await agendaB.open(DAYS.weekA.tue)
    await a.getByRole('button', { name: `Menu de ${USERS.ana.fullName}` }).click()
    await a.getByRole('menuitem', { name: 'Sair' }).click()
    await expect(a).toHaveURL(/\/login$/)
    // A outra aba não continua conectada (nem gravando) como a Ana.
    await expect(b).toHaveURL(/\/login$/)
    await expect(b.getByRole('button', { name: 'Entrar' })).toBeVisible()
    // Entrar de novo numa aba entra nas duas.
    await a.getByLabel('Usuário').fill(USERS.ana.username)
    await a.getByLabel('Senha', { exact: true }).fill(USERS.ana.password)
    await a.getByRole('button', { name: 'Entrar' }).click()
    await new Agenda(a).waitConnected()
    await agendaB.waitConnected()
    await context.close()
  })

  test('administrador vê todas as seções', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Usuário').fill(USERS.admin.username)
    await page.getByLabel('Senha', { exact: true }).fill(USERS.admin.password)
    await page.getByRole('button', { name: 'Entrar' }).click()
    await new Agenda(page).waitConnected()

    await page.getByRole('button', { name: `Menu de ${USERS.admin.fullName}` }).click()
    await expect(page.getByRole('menu').getByRole('menuitem')).toHaveText(['Grade de salas', 'Administração', 'Sair'])
    await page.getByRole('menuitem', { name: 'Administração' }).click()
    await expect(page).toHaveURL(/\/admin\/salas$/)
    const nav = page.getByRole('navigation', { name: 'Seções da administração' })
    await expect(nav.getByRole('link')).toHaveText(['Salas', 'Usuários', 'Importar planilha', 'Auditoria'])
  })
})
