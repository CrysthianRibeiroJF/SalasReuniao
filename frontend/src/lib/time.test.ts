import { describe, expect, it } from 'vitest'

import {
  addDays,
  chunkDateRanges,
  daysBetween,
  formatDateBR,
  formatDateLong,
  formatDuration,
  formatTabLabel,
  formatWeekRange,
  fromMinutes,
  isValidISODate,
  toMinutes,
  weekDates,
  weekdayIndex,
  weekMonday,
  zonedParts,
} from './time'

describe('horas', () => {
  it('converte HH:MM ↔ minutos', () => {
    expect(toMinutes('08:00')).toBe(480)
    expect(toMinutes('19:00')).toBe(1140)
    expect(toMinutes('9:05')).toBe(545)
    expect(fromMinutes(480)).toBe('08:00')
    expect(fromMinutes(785)).toBe('13:05')
    expect(() => toMinutes('8h')).toThrow()
  })

  it('formata durações', () => {
    expect(formatDuration(30)).toBe('30 min')
    expect(formatDuration(60)).toBe('1 h')
    expect(formatDuration(90)).toBe('1 h 30 min')
    expect(formatDuration(660)).toBe('11 h')
  })
})

describe('datas', () => {
  it('valida datas ISO reais', () => {
    expect(isValidISODate('2026-10-26')).toBe(true)
    expect(isValidISODate('2026-02-30')).toBe(false)
    expect(isValidISODate('26/10/2026')).toBe(false)
    expect(isValidISODate(null)).toBe(false)
  })

  it('faz aritmética de calendário sem depender do fuso do navegador', () => {
    expect(addDays('2026-10-30', 3)).toBe('2026-11-02')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(daysBetween('2026-10-26', '2026-11-02')).toBe(7)
  })

  it('usa segunda = 0 como o backend (work_days)', () => {
    expect(weekdayIndex('2026-10-26')).toBe(0) // segunda
    expect(weekdayIndex('2026-10-30')).toBe(4) // sexta
    expect(weekdayIndex('2026-11-01')).toBe(6) // domingo
    expect(weekMonday('2026-11-01')).toBe('2026-10-26')
    expect(weekDates('2026-10-28')).toEqual([
      '2026-10-26',
      '2026-10-27',
      '2026-10-28',
      '2026-10-29',
      '2026-10-30',
      '2026-10-31',
      '2026-11-01',
    ])
  })

  it('formata rótulos pt-BR', () => {
    expect(formatTabLabel('2026-10-26')).toBe('Seg 26/10')
    expect(formatTabLabel('2026-10-31')).toBe('Sáb 31/10')
    expect(formatDateBR('2026-10-05')).toBe('05/10/2026')
    expect(formatDateLong('2026-10-26')).toBe('segunda-feira, 26 de outubro')
    expect(formatDateLong('2026-03-04', true)).toBe('quarta-feira, 4 de março de 2026')
    expect(formatWeekRange('2026-10-26', '2026-10-30')).toBe('26 – 30 out 2026')
    expect(formatWeekRange('2026-10-28', '2026-11-03')).toBe('28 out – 3 nov 2026')
  })

  it('calcula "agora" no fuso da recepção', () => {
    // 2026-10-26 02:30 UTC = 25/10 23:30 em São Paulo (UTC−3)
    const parts = zonedParts(Date.UTC(2026, 9, 26, 2, 30), 'America/Sao_Paulo')
    expect(parts.date).toBe('2026-10-25')
    expect(parts.minutes).toBe(23 * 60 + 30)
  })

  it('agrupa datas em faixas contíguas de até 31 dias', () => {
    expect(chunkDateRanges(['2026-10-28', '2026-10-26', '2026-10-27', '2026-11-05'])).toEqual([
      ['2026-10-26', '2026-10-28'],
      ['2026-11-05', '2026-11-05'],
    ])
    const many = Array.from({ length: 40 }, (_, i) => addDays('2026-10-01', i))
    const ranges = chunkDateRanges(many)
    expect(ranges).toHaveLength(2)
    expect(daysBetween(ranges[0]![0], ranges[0]![1])).toBe(30)
  })
})
