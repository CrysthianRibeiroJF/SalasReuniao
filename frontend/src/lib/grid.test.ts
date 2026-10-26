import { describe, expect, it } from 'vitest'

import {
  blockBox,
  dayWindow,
  dragTarget,
  extendSelection,
  fitRowHeight,
  isSlotFree,
  minutesAtOffset,
  offsetFor,
  rangeFromSlots,
  slotCount,
  slotIndexAt,
  slotIndexFromOffset,
  slotLabels,
  slotTimeOptions,
  snapMinutes,
} from './grid'

const w = dayWindow({ day_start: '08:00', day_end: '19:00', slot_minutes: 30 })
const ROW = 24

describe('janela do dia', () => {
  it('gera as linhas de slot_minutes do funcionamento', () => {
    expect(w).toEqual({ startMin: 480, endMin: 1140, slot: 30 })
    expect(slotCount(w)).toBe(22)
    expect(slotLabels(w)[0]).toBe('08:00')
    expect(slotLabels(w).at(-1)).toBe('18:30')
    expect(slotCount(dayWindow({ day_start: '08:00', day_end: '19:00', slot_minutes: 15 }))).toBe(44)
  })

  it('localiza o slot de um minuto (limitado ao dia)', () => {
    expect(slotIndexAt(w, 480)).toBe(0)
    expect(slotIndexAt(w, 509)).toBe(0)
    expect(slotIndexAt(w, 510)).toBe(1)
    expect(slotIndexAt(w, 400)).toBe(0)
    expect(slotIndexAt(w, 2000)).toBe(21)
  })
})

describe('minutos → pixels', () => {
  it('posiciona pelo minuto, mesclando visualmente as linhas', () => {
    expect(offsetFor(480, w, ROW)).toBe(0)
    expect(offsetFor(540, w, ROW)).toBe(48)
    expect(offsetFor(555, w, ROW)).toBe(60) // 09:15 = meio slot
    expect(minutesAtOffset(60, w, ROW)).toBe(555)
  })

  it('calcula a caixa de um bloco e recorta fora do horário', () => {
    expect(blockBox({ start: 600, end: 690 }, w, ROW)).toEqual({
      top: 96,
      height: 72,
      clippedTop: false,
      clippedBottom: false,
    })
    // Dia inteiro ocupa todas as 22 linhas
    expect(blockBox({ start: 480, end: 1140 }, w, ROW)?.height).toBe(22 * ROW)
    // Importação fora do horário: recorta e marca
    expect(blockBox({ start: 450, end: 510 }, w, ROW)).toEqual({
      top: 0,
      height: 24,
      clippedTop: true,
      clippedBottom: false,
    })
    expect(blockBox({ start: 1110, end: 1200 }, w, ROW)?.clippedBottom).toBe(true)
    expect(blockBox({ start: 400, end: 470 }, w, ROW)).toBeNull()
  })

  it('converte a posição do ponteiro em slot', () => {
    expect(slotIndexFromOffset(0, w, ROW)).toBe(0)
    expect(slotIndexFromOffset(47, w, ROW)).toBe(1)
    expect(slotIndexFromOffset(-10, w, ROW)).toBe(0)
    expect(slotIndexFromOffset(10_000, w, ROW)).toBe(21)
  })

  it('ajusta a altura da linha ao espaço disponível', () => {
    expect(fitRowHeight(484, 22)).toBe(24) // mínimo padrão
    expect(fitRowHeight(484, 22, 22)).toBe(22)
    expect(fitRowHeight(780, 22, 22, 44)).toBe(35)
    expect(fitRowHeight(5000, 22, 22, 44)).toBe(44)
    expect(fitRowHeight(0, 22, 22)).toBe(22)
  })
})

describe('seleção', () => {
  it('monta a faixa entre âncora e foco em qualquer ordem', () => {
    expect(rangeFromSlots(w, 2, 4)).toEqual({ start: 540, end: 630 })
    expect(rangeFromSlots(w, 4, 2)).toEqual({ start: 540, end: 630 })
    expect(rangeFromSlots(w, 0, 0)).toEqual({ start: 480, end: 510 })
  })

  it('estende a seleção só até antes de um slot ocupado', () => {
    const occupied = [{ start: 600, end: 660 }] // 10:00–11:00 = slots 4 e 5
    expect(isSlotFree(w, 3, occupied)).toBe(true)
    expect(isSlotFree(w, 4, occupied)).toBe(false)
    expect(extendSelection(w, 0, 10, occupied)).toEqual([0, 3])
    expect(extendSelection(w, 8, 1, occupied)).toEqual([8, 6])
    expect(extendSelection(w, 1, 2, occupied)).toEqual([1, 2])
    expect(extendSelection(w, 20, 40, [])).toEqual([20, 21])
  })
})

describe('arrastar e soltar', () => {
  it('encaixa em slot_minutes', () => {
    expect(snapMinutes(554, 30)).toBe(540)
    expect(snapMinutes(556, 30)).toBe(570)
    expect(snapMinutes(556, 30, 'floor')).toBe(540)
    expect(snapMinutes(541, 30, 'ceil')).toBe(570)
  })

  it('mantém a duração e o bloco dentro do dia', () => {
    // Bloco de 60 min agarrado 10 min abaixo do início, ponteiro em 14:07 → início 13:57 → 14:00
    expect(dragTarget(w, 60, 847, 10)).toEqual({ start: 840, end: 900 })
    // Não passa do fechamento
    expect(dragTarget(w, 120, 1135, 0)).toEqual({ start: 1020, end: 1140 })
    // Nem da abertura
    expect(dragTarget(w, 90, 470, 30)).toEqual({ start: 480, end: 570 })
  })
})

describe('opções de horário do modal', () => {
  it('lista inícios e fins em passos de slot', () => {
    const { starts, ends } = slotTimeOptions(w)
    expect(starts[0]).toBe('08:00')
    expect(starts.at(-1)).toBe('18:30')
    expect(ends[0]).toBe('08:30')
    expect(ends.at(-1)).toBe('19:00')
    expect(starts).toHaveLength(22)
  })
})
