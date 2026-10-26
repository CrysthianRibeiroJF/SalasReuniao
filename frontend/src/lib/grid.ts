/**
 * Geometria da grade: minutos ↔ linhas/pixels, faixas de seleção e encaixe (snap) do arrastar.
 * Funções puras; a grade posiciona tudo em pixels a partir de `day_start`.
 */
import type { ConfigOut, HHMM } from '../api/types'
import { fromMinutes, toMinutes } from './time'

export interface DayWindow {
  /** Abertura em minutos desde a meia-noite. */
  startMin: number
  /** Fechamento em minutos desde a meia-noite. */
  endMin: number
  /** Tamanho do slot (linha) em minutos. */
  slot: number
}

/** Intervalo semiaberto [start, end) em minutos desde a meia-noite. */
export interface MinuteRange {
  start: number
  end: number
}

export function dayWindow(config: Pick<ConfigOut, 'day_start' | 'day_end' | 'slot_minutes'>): DayWindow {
  return { startMin: toMinutes(config.day_start), endMin: toMinutes(config.day_end), slot: config.slot_minutes }
}

export function slotCount(w: DayWindow): number {
  return Math.max(0, Math.ceil((w.endMin - w.startMin) / w.slot))
}

export function slotStart(w: DayWindow, index: number): number {
  return w.startMin + index * w.slot
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** Índice do slot que contém `minutes` (limitado à janela do dia). */
export function slotIndexAt(w: DayWindow, minutes: number): number {
  return clamp(Math.floor((minutes - w.startMin) / w.slot), 0, slotCount(w) - 1)
}

export function slotLabels(w: DayWindow): HHMM[] {
  return Array.from({ length: slotCount(w) }, (_, i) => fromMinutes(slotStart(w, i)))
}

/** Deslocamento vertical (px) de um instante em relação ao topo da grade. */
export function offsetFor(minutes: number, w: DayWindow, rowHeight: number): number {
  return ((minutes - w.startMin) / w.slot) * rowHeight
}

export function minutesAtOffset(y: number, w: DayWindow, rowHeight: number): number {
  return w.startMin + (y / rowHeight) * w.slot
}

export function slotIndexFromOffset(y: number, w: DayWindow, rowHeight: number): number {
  return clamp(Math.floor(y / rowHeight), 0, slotCount(w) - 1)
}

export interface BlockBox {
  top: number
  height: number
  /** A reserva começa antes da abertura (só acontece em importações). */
  clippedTop: boolean
  /** A reserva termina depois do fechamento. */
  clippedBottom: boolean
}

/** Caixa de um bloco [start, end) recortada à janela do dia; null se ficar totalmente fora. */
export function blockBox(range: MinuteRange, w: DayWindow, rowHeight: number): BlockBox | null {
  const start = Math.max(range.start, w.startMin)
  const end = Math.min(range.end, w.endMin)
  if (end <= start) return null
  const top = offsetFor(start, w, rowHeight)
  return {
    top,
    height: offsetFor(end, w, rowHeight) - top,
    clippedTop: range.start < w.startMin,
    clippedBottom: range.end > w.endMin,
  }
}

/** Faixa coberta pelos slots entre `a` e `b` (inclusivos, em qualquer ordem). */
export function rangeFromSlots(w: DayWindow, a: number, b: number): MinuteRange {
  const lo = Math.min(a, b)
  const hi = Math.max(a, b)
  return { start: slotStart(w, lo), end: slotStart(w, hi) + w.slot }
}

export function overlaps(a: MinuteRange, b: MinuteRange): boolean {
  return a.start < b.end && b.start < a.end
}

export function isSlotFree(w: DayWindow, index: number, occupied: readonly MinuteRange[]): boolean {
  const slot = rangeFromSlots(w, index, index)
  return !occupied.some((r) => overlaps(r, slot))
}

/**
 * Estende a seleção da âncora em direção ao foco, parando antes do primeiro slot ocupado.
 * Retorna [âncora, foco efetivo]. Se a própria âncora estiver ocupada, retorna [âncora, âncora].
 */
export function extendSelection(
  w: DayWindow,
  anchor: number,
  focus: number,
  occupied: readonly MinuteRange[],
): [number, number] {
  const target = clamp(focus, 0, slotCount(w) - 1)
  const step = target >= anchor ? 1 : -1
  let reached = anchor
  for (let i = anchor + step; step > 0 ? i <= target : i >= target; i += step) {
    if (!isSlotFree(w, i, occupied)) break
    reached = i
  }
  return [anchor, reached]
}

export function snapMinutes(minutes: number, step: number, mode: 'round' | 'floor' | 'ceil' = 'round'): number {
  const fn = mode === 'floor' ? Math.floor : mode === 'ceil' ? Math.ceil : Math.round
  return fn(minutes / step) * step
}

/**
 * Destino de um bloco arrastado: mantém a duração, encaixa o início no slot mais próximo
 * e mantém o bloco dentro do horário de funcionamento.
 * `grabOffsetMin` = distância (min) entre o início do bloco e o ponto onde ele foi agarrado.
 */
export function dragTarget(w: DayWindow, durationMin: number, pointerMin: number, grabOffsetMin: number): MinuteRange {
  const latestStart = Math.max(w.startMin, w.endMin - durationMin)
  const start = clamp(snapMinutes(pointerMin - grabOffsetMin, w.slot), w.startMin, latestStart)
  return { start, end: start + durationMin }
}

export interface SlotOptions {
  starts: HHMM[]
  ends: HHMM[]
}

/** Opções de início/fim em passos de slot dentro do horário de funcionamento. */
export function slotTimeOptions(w: DayWindow): SlotOptions {
  const starts: HHMM[] = []
  const ends: HHMM[] = []
  for (let m = w.startMin; m < w.endMin; m += w.slot) starts.push(fromMinutes(m))
  for (let m = w.startMin + w.slot; m <= w.endMin; m += w.slot) ends.push(fromMinutes(m))
  return { starts, ends }
}

/** Altura de linha que preenche o espaço disponível sem ficar apertada nem esticada demais. */
export function fitRowHeight(available: number, rows: number, min = 24, max = 44): number {
  if (rows <= 0 || available <= 0) return min
  return clamp(Math.floor(available / rows), min, max)
}

export function rangeOf(item: { start: HHMM; end: HHMM }): MinuteRange {
  return { start: toMinutes(item.start), end: toMinutes(item.end) }
}
