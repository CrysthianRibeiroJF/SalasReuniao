/**
 * Fixtures dos testes E2E:
 * - `ana`, `bia`, `adminPage`: uma página por usuário, cada uma no seu próprio contexto
 *   (sessões independentes, como duas recepcionistas em computadores diferentes);
 * - `anaApi`, `biaApi`, `adminApi`: clientes REST autenticados para arranjar/conferir dados;
 * - `ids`: salas, usuários e reservas criados pelo global-setup.
 * Erros de JavaScript não tratados em qualquer dessas páginas fazem o teste falhar.
 */
import { type Browser, type Page, test as base, expect } from '@playwright/test'

import { AgendaApi } from './api'
import { USERS, type UserKey } from './data'
import { type SeedManifest, seed, storageStatePath } from './seed'

const uncaughtErrors = new WeakMap<Page, string[]>()

/** Abre uma página já autenticada como `user` num contexto novo. */
export async function openAs(browser: Browser, user: UserKey): Promise<Page> {
  const context = await browser.newContext({ storageState: storageStatePath(user) })
  const page = await context.newPage()
  const errors: string[] = []
  uncaughtErrors.set(page, errors)
  page.on('pageerror', (error) => errors.push(`${USERS[user].fullName}: ${error.message}`))
  return page
}

async function closeChecked(page: Page): Promise<void> {
  const errors = [...(uncaughtErrors.get(page) ?? [])]
  await page.context().close()
  expect(errors, 'erros de JavaScript na página').toEqual([])
}

interface TestFixtures {
  ana: Page
  bia: Page
  adminPage: Page
}

interface WorkerFixtures {
  ids: SeedManifest
  anaApi: AgendaApi
  biaApi: AgendaApi
  adminApi: AgendaApi
}

type WorkerFixture<T> = [(deps: object, use: (value: T) => Promise<void>) => Promise<void>, { scope: 'worker' }]

/** Cliente REST logado uma vez por worker. */
function apiFixture(user: UserKey): WorkerFixture<AgendaApi> {
  return [
    // eslint-disable-next-line no-empty-pattern -- o Playwright exige a desestruturação, mesmo vazia
    async ({}, use) => {
      const api = await AgendaApi.login(USERS[user].username, USERS[user].password)
      await use(api)
      await api.dispose()
    },
    { scope: 'worker' },
  ]
}

function pageFixture(user: UserKey) {
  return async ({ browser }: { browser: Browser }, use: (page: Page) => Promise<void>) => {
    const page = await openAs(browser, user)
    await use(page)
    await closeChecked(page)
  }
}

export const test = base.extend<TestFixtures, WorkerFixtures>({
  // eslint-disable-next-line no-empty-pattern -- o Playwright exige a desestruturação, mesmo vazia
  ids: [async ({}, use) => use(seed()), { scope: 'worker' }],
  anaApi: apiFixture('ana'),
  biaApi: apiFixture('bia'),
  adminApi: apiFixture('admin'),
  ana: pageFixture('ana'),
  bia: pageFixture('bia'),
  adminPage: pageFixture('admin'),
})

export { expect }
