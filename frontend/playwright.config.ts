/**
 * Testes ponta a ponta (Playwright) contra o build de produção servido pelo próprio backend.
 *
 *   npm run e2e                 build + todos os testes (headless, Microsoft Edge instalado)
 *   npm run e2e -- --headed     com a janela visível
 *   npm run e2e -- realtime     só os arquivos que casam com "realtime"
 *
 * O `webServer` sobe o uvicorn na porta 8100 apontando para o banco agenda_e2e; o global-setup
 * recria esse banco, aplica as migrations e cadastra os dados de teste pela API REST.
 * Variáveis: E2E_PORT, E2E_DB_NAME, E2E_PG_URL, E2E_PYTHON, E2E_WORKERS, E2E_REUSE_SERVER=1.
 */
import { existsSync } from 'node:fs'
import path from 'node:path'

import { defineConfig } from '@playwright/test'

import { USERS } from './e2e/support/data'
import { BACKEND_DIR, BASE_URL, DIST_DIR, PORT, PYTHON, backendEnv } from './e2e/support/env'

if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
  throw new Error(`Build do frontend não encontrado em ${DIST_DIR}. Rode "npm run build" (ou use "npm run e2e").`)
}

// Caminho relativo ao backend/ (cwd do servidor): evita aspas e acentos na linha de comando.
const python = process.env.E2E_PYTHON ? `"${PYTHON}"` : path.relative(BACKEND_DIR, PYTHON)

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  outputDir: './test-results',
  globalSetup: './e2e/global-setup.ts',
  // Um backend e um banco compartilhados por todos os testes: por padrão um worker (determinístico).
  // Os testes usam dias e salas próprios, então E2E_WORKERS=2 também funciona (arquivos em paralelo).
  fullyParallel: false,
  workers: Number(process.env.E2E_WORKERS ?? 1),
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 7_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: BASE_URL,
    channel: 'msedge',
    headless: true,
    viewport: { width: 1366, height: 768 },
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    acceptDownloads: true,
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'msedge' }],
  webServer: {
    command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT} --log-level warning`,
    cwd: BACKEND_DIR,
    env: backendEnv(USERS.admin),
    // A SPA (index.html) não depende do banco, que só existe depois do global-setup.
    url: `${BASE_URL}/login`,
    reuseExistingServer: process.env.E2E_REUSE_SERVER === '1',
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
