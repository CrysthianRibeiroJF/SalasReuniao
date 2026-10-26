/**
 * D7: a SPA roda sob `Content-Security-Policy: script-src 'self'` (sem 'unsafe-inline' nem
 * 'unsafe-eval'). Estes testes impedem que um script inline no index.html ou um eval no código
 * quebrem a aplicação em produção (o E2E security.e2e.ts confere o build servido pelo backend).
 */
import { describe, expect, it } from 'vitest'

import indexHtml from '../../index.html?raw'

const sources = import.meta.glob<string>(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

describe('compatível com a CSP (D7)', () => {
  it('index.html: só scripts externos (src), sem código inline nem handlers on*=', () => {
    const scripts = [...indexHtml.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
    expect(scripts.length).toBeGreaterThan(0)
    for (const [, attributes, body] of scripts) {
      expect(attributes).toMatch(/\bsrc=/)
      expect(body!.trim()).toBe('')
    }
    expect(indexHtml).not.toMatch(/\son[a-z]+\s*=/i)
    expect(indexHtml).not.toMatch(/javascript:/i)
  })

  it('o código da SPA não usa eval nem new Function', () => {
    const offenders = Object.entries(sources)
      .filter(([, code]) => /\beval\s*\(|\bnew\s+Function\s*\(|setTimeout\(\s*['"`]/.test(code))
      .map(([file]) => file)
    expect(Object.keys(sources).length).toBeGreaterThan(20)
    expect(offenders).toEqual([])
  })
})
