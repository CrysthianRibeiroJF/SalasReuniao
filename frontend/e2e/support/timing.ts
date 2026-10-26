/**
 * Medição de atraso ponta a ponta entre duas páginas (duas recepcionistas): o relógio é o mesmo
 * (mesma máquina), então comparamos Date.now() do Node no instante da ação com o Date.now() do
 * navegador no instante em que a outra página passou a mostrar a mudança.
 */
import type { Page, TestInfo } from '@playwright/test'

export interface Watch {
  /** Espera a condição e devolve o atraso (ms) desde `start()`. */
  done(): Promise<number>
  start(): void
}

/**
 * Começa a observar `page` ANTES da ação. `predicate` roda no navegador (a cada mudança do DOM e a
 * cada 10 ms) e deve devolver true quando a mudança estiver visível. Ele é serializado: não pode usar
 * variáveis de fora da função — passe o que precisar em `arg` (JSON).
 *
 * Usa `page.evaluate` com uma expressão (compilada pelo protocolo do navegador) em vez de
 * `waitForFunction`, que compila o predicado com `new Function` dentro da página e é bloqueado pela
 * CSP da SPA (script-src 'self', sem 'unsafe-eval' — D7).
 */
export function watch<A>(page: Page, predicate: (arg: A) => boolean, arg: A, timeoutMs = 5_000): Watch {
  let t0 = 0
  const expression = `new Promise((resolve, reject) => {
    const check = () => (${predicate.toString()})(${JSON.stringify(arg)})
    if (check()) return resolve(Date.now())
    const finish = () => {
      if (!check()) return
      observer.disconnect()
      clearInterval(poll)
      clearTimeout(timer)
      resolve(Date.now())
    }
    const observer = new MutationObserver(finish)
    observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true })
    const poll = setInterval(finish, 10)
    const timer = setTimeout(() => {
      observer.disconnect()
      clearInterval(poll)
      reject(new Error('watch: a mudança não apareceu em ${timeoutMs} ms'))
    }, ${timeoutMs})
  })`
  const seen = page.evaluate(expression) as Promise<number>
  return {
    start() {
      t0 = Date.now()
    },
    async done() {
      const at = await seen
      return at - t0
    },
  }
}

declare global {
  interface Window {
    __e2eSocketArrivals?: { type: string; ts: string; at: number; data: unknown }[]
  }
}

/**
 * Sonda do RNF01: anota, dentro da própria página, o instante em que cada evento chega pelo
 * WebSocket (antes de a SPA processá-lo). Comparado com o `ts` do envelope (publicação no servidor),
 * dá o atraso servidor → navegador sem o custo do protocolo de automação. Guarda também o `data` de
 * cada evento (ver `socketEvents`). Chame antes de abrir a página.
 */
export async function installSocketProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const Native = window.WebSocket
    window.__e2eSocketArrivals = []
    class ProbedWebSocket extends Native {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        // Registrado antes do onmessage da SPA: roda primeiro.
        this.addEventListener('message', (event: MessageEvent) => {
          const at = Date.now()
          if (typeof event.data !== 'string') return
          try {
            const { type, ts, data } = JSON.parse(event.data) as { type?: string; ts?: string; data?: unknown }
            if (type && ts) window.__e2eSocketArrivals!.push({ type, ts, at, data })
          } catch {
            // quadro que não é JSON
          }
        })
      }
    }
    window.WebSocket = ProbedWebSocket
  })
}

/** Atrasos (ms) entre a publicação no servidor e a chegada na página, por tipo de evento. */
export async function socketLatencies(page: Page): Promise<Map<string, number[]>> {
  const arrivals = await page.evaluate(() => window.__e2eSocketArrivals ?? [])
  const result = new Map<string, number[]>()
  for (const { type, ts, at } of arrivals) {
    // Date.parse entende até milissegundos; o servidor manda microssegundos.
    const sentAt = Date.parse(ts.replace(/(\.\d{3})\d+/, '$1'))
    if (!Number.isNaN(sentAt)) result.set(type, [...(result.get(type) ?? []), at - sentAt])
  }
  return result
}

/** `data` dos eventos `type` que chegaram à página (sonda de `installSocketProbe`), na ordem. */
export async function socketEvents<T = unknown>(page: Page, type: string): Promise<T[]> {
  const arrivals = await page.evaluate(() => window.__e2eSocketArrivals ?? [])
  return arrivals.filter((a) => a.type === type).map((a) => a.data as T)
}

export function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2
}

/** Registra o atraso medido no relatório (anotação) e na saída do teste. */
export function report(testInfo: TestInfo, what: string, ms: number): void {
  testInfo.annotations.push({ type: 'atraso tempo real', description: `${what}: ${ms} ms` })
  console.log(`[tempo real] ${what}: ${ms} ms`)
}
