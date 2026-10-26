import { request } from '@playwright/test'

import { DAYS } from './support/data'
import { BASE_URL } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda } from './support/grid'

/** Política decidida para a SPA (D7). */
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
  "font-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"

declare global {
  interface Window {
    __cspViolations?: string[]
  }
}

test.describe('segurança (D7)', () => {
  test('o servidor envia os cabeçalhos de segurança na SPA e na API', async () => {
    const http = await request.newContext({ baseURL: BASE_URL })
    try {
      const html = await http.get('/login')
      expect(html.headers()['x-content-type-options']).toBe('nosniff')
      expect(html.headers()['x-frame-options']).toBe('DENY')
      expect(html.headers()['referrer-policy']).toBe('strict-origin-when-cross-origin')
      const csp = html.headers()['content-security-policy'] ?? ''
      expect(csp).toContain("script-src 'self'")
      expect(csp).toContain("frame-ancestors 'none'")

      const api = await http.get('/api/health')
      expect(api.headers()['x-content-type-options']).toBe('nosniff')
    } finally {
      await http.dispose()
    }
  })

  test('a SPA funciona sob a CSP: sem script inline nem eval (login, grade, modal e tempo real)', async ({ ana }) => {
    // (Não dá para injetar a política com page.route: um documento entregue pelo interceptador perde
    // o acesso ao 127.0.0.1 no Chromium — "local network access" — e o WebSocket não conecta.)
    await ana.addInitScript(() => {
      window.__cspViolations = []
      document.addEventListener('securitypolicyviolation', (event) => {
        window.__cspViolations!.push(`${event.violatedDirective} ${event.blockedURI}`)
      })
    })
    const agenda = new Agenda(ana)
    const response = await ana.goto(`/?date=${DAYS.composerLifecycle}`)
    // A política do servidor é a decidida (D7)…
    const csp = response?.headers()['content-security-policy'] ?? ''
    for (const directive of CSP.split('; ')) expect(csp).toContain(directive)
    // …e com ela a SPA carrega, conecta o tempo real e abre o modal sem nenhuma violação.
    await agenda.waitForDay(DAYS.composerLifecycle)
    await agenda.waitConnected()
    await agenda.clickCell('s10', '08:00')
    await expect(agenda.composer()).toBeVisible()
    await ana.keyboard.press('Escape')
    await expect(agenda.composer()).toBeHidden()
    expect(await ana.evaluate(() => window.__cspViolations)).toEqual([])
  })
})
