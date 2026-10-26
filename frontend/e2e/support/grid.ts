/**
 * Page object da grade de salas: navegação por dia, células, blocos de reserva, travas, modal
 * rápido e avisos. Usa os ganchos estáveis da SPA ([data-cell], [data-room-id], [data-booking-id],
 * [data-lock-id], [data-selection]) e papéis/rótulos acessíveis.
 */
import path from 'node:path'

import { type Locator, type Page, expect } from '@playwright/test'

import { ROOMS, type RoomKey } from './data'
import { SCREENSHOTS_DIR } from './env'
import { seed } from './seed'

const WEEKDAYS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
const DAY_START_MIN = 8 * 60
const SLOT_MIN = 30

/** "Seg 16/11" — rótulo das abas e do nome acessível da grade. */
export function tabLabel(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number) as [number, number, number]
  const weekday = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
  return `${WEEKDAYS[weekday]} ${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}`
}

/** Índice da linha (slot de 30 min) que começa em "HH:MM". */
export function slotIndex(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number) as [number, number]
  return (h * 60 + m - DAY_START_MIN) / SLOT_MIN
}

export function roomName(room: RoomKey): string {
  return ROOMS[room].name
}

export class Agenda {
  constructor(readonly page: Page) {}

  /** Abre a grade no dia e espera as reservas carregarem. */
  async open(date: string, { realtime = true } = {}): Promise<void> {
    await this.page.goto(`/?date=${date}`)
    await this.waitForDay(date)
    if (realtime) await this.waitConnected()
  }

  grid(date: string): Locator {
    return this.page.getByRole('grid', { name: `Grade de salas de ${tabLabel(date)}` })
  }

  async waitForDay(date: string): Promise<void> {
    await expect(this.grid(date)).toBeVisible()
    await expect(this.page.getByRole('progressbar', { name: 'Carregando reservas' })).toHaveCount(0)
  }

  connection(label: 'Tempo real conectado' | 'Reconectando…' | 'Conectando…'): Locator {
    return this.page.getByRole('status', { name: label, exact: true })
  }

  async waitConnected(): Promise<void> {
    await expect(this.connection('Tempo real conectado')).toBeVisible()
  }

  column(room: RoomKey): Locator {
    return this.page.locator(`[data-room-id="${seed().rooms[room]}"]`)
  }

  header(room: RoomKey): Locator {
    return this.page.getByRole('columnheader', { name: new RegExp(`^${escapeRegExp(roomName(room))}`) })
  }

  cell(room: RoomKey, hhmm: string): Locator {
    return this.page.locator(`[data-cell="${seed().rooms[room]}:${slotIndex(hhmm)}"]`)
  }

  /** Blocos de reserva da sala (opcionalmente filtrados por um texto do rótulo acessível). */
  blocks(room: RoomKey, label?: string | RegExp): Locator {
    const all = this.column(room).locator('[data-booking-id]')
    if (label === undefined) return all
    return all.and(this.page.getByRole('button', { name: label }))
  }

  block(id: string): Locator {
    return this.page.locator(`[data-booking-id="${id}"]`)
  }

  locks(room: RoomKey): Locator {
    return this.column(room).locator('[data-lock-id]')
  }

  /** Um clique numa célula vazia (RNF03: o único clique antes de digitar). */
  async clickCell(room: RoomKey, hhmm: string): Promise<void> {
    await this.cell(room, hhmm).click()
  }

  /** Arrasta a seleção de `from` até `to` (inclusive) na mesma sala. */
  async dragCells(room: RoomKey, from: string, to: string): Promise<void> {
    const start = await this.cell(room, from).boundingBox()
    const end = await this.cell(room, to).boundingBox()
    if (!start || !end) throw new Error(`células ${from}/${to} fora da tela`)
    const mouse = this.page.mouse
    await mouse.move(start.x + start.width / 2, start.y + start.height / 2)
    await mouse.down()
    await mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 6 })
    await mouse.up()
  }

  /**
   * Arrasta um bloco de reserva para que ele comece na célula `hhmm` da sala `room`.
   * Pega o bloco no meio da altura, à esquerda (longe do link do chamado, que não arrasta).
   */
  async dragBlockTo(block: Locator, room: RoomKey, hhmm: string): Promise<void> {
    const from = await block.boundingBox()
    const to = await this.cell(room, hhmm).boundingBox()
    if (!from || !to) throw new Error('bloco ou destino fora da tela')
    const grabX = from.width * 0.25
    const grabY = Math.min(from.height / 2, 40)
    const mouse = this.page.mouse
    await mouse.move(from.x + grabX, from.y + grabY)
    await mouse.down()
    await mouse.move(from.x + grabX + 12, from.y + grabY + 2, { steps: 3 })
    await mouse.move(to.x + grabX, to.y + grabY, { steps: 12 })
    await mouse.up()
  }

  /** Modal rápido de agendamento ("Nova reserva" ou "Editar reserva"). */
  composer(kind: 'Nova reserva' | 'Editar reserva' = 'Nova reserva'): Locator {
    return this.page.getByRole('dialog', { name: kind, exact: true })
  }

  /** Popover de detalhes de uma reserva (título = solicitante). */
  popover(requester: string): Locator {
    return this.page.getByRole('dialog', { name: requester, exact: true })
  }

  toast(text: string | RegExp): Locator {
    return this.page.locator('[aria-live="polite"]').getByText(text)
  }

  tab(date: string): Locator {
    return this.page.getByRole('tablist', { name: 'Dia exibido' }).getByRole('tab', { name: longDateRegExp(date) })
  }
}

const WEEKDAYS_LONG = [
  'segunda-feira',
  'terça-feira',
  'quarta-feira',
  'quinta-feira',
  'sexta-feira',
  'sábado',
  'domingo',
]
const MONTHS_LONG = [
  'janeiro',
  'fevereiro',
  'março',
  'abril',
  'maio',
  'junho',
  'julho',
  'agosto',
  'setembro',
  'outubro',
  'novembro',
  'dezembro',
]

/** Nome acessível das abas: "segunda-feira, 9 de novembro[, 3 reservas][, hoje]". */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number) as [number, number, number]
  const weekday = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
  return `${WEEKDAYS_LONG[weekday]}, ${d} de ${MONTHS_LONG[m - 1]}`
}

function longDateRegExp(iso: string): RegExp {
  return new RegExp(`^${escapeRegExp(longDate(iso))}(,|$)`)
}

export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Captura de tela para o relatório (e2e/screenshots/, ignorada pelo git). */
export async function capture(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: path.join(SCREENSHOTS_DIR, `${name}.png`) })
}

declare global {
  interface Window {
    __e2eClicks?: number
  }
}

/** Passa a contar os cliques (pointerdown) na página; devolve a função que lê a contagem. */
export async function countClicks(page: Page): Promise<() => Promise<number>> {
  await page.evaluate(() => {
    window.__e2eClicks = 0
    document.addEventListener('pointerdown', () => (window.__e2eClicks = (window.__e2eClicks ?? 0) + 1), true)
  })
  return () => page.evaluate(() => window.__e2eClicks ?? 0)
}
