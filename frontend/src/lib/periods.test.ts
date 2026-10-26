import { describe, expect, it } from 'vitest'

import { CONFIG } from '../test/fixtures'
import { PERIOD_OPTIONS, periodFromShortcut, presetRange, resolvePeriod } from './periods'

const key = (overrides: Partial<Parameters<typeof periodFromShortcut>[0]>) => ({
  altKey: true,
  ctrlKey: false,
  metaKey: false,
  code: 'Digit1',
  key: '1',
  ...overrides,
})

describe('períodos', () => {
  it('segue a ordem do segmentado: Específico | Dia Inteiro | Manhã | Tarde', () => {
    expect(PERIOD_OPTIONS.map((o) => [o.shortcut, o.label])).toEqual([
      ['1', 'Horário Específico'],
      ['2', 'Dia Inteiro'],
      ['3', 'Manhã'],
      ['4', 'Tarde'],
    ])
  })

  it('presets usam os horários da configuração (espelho de timeutil.period_times)', () => {
    expect(presetRange('FULL_DAY', CONFIG)).toEqual({ start: '08:00', end: '19:00' })
    expect(presetRange('MORNING', CONFIG)).toEqual({ start: '08:00', end: '12:00' })
    expect(presetRange('AFTERNOON', CONFIG)).toEqual({ start: '13:00', end: '19:00' })
    expect(presetRange('CUSTOM', CONFIG)).toBeNull()
  })

  it('presets ignoram o início/fim digitados', () => {
    const custom = { start: '14:00', end: '15:30' }
    expect(resolvePeriod('CUSTOM', CONFIG, custom)).toEqual(custom)
    expect(resolvePeriod('MORNING', CONFIG, custom)).toEqual({ start: '08:00', end: '12:00' })
  })

  it('Alt+1..4 (linha numérica ou teclado numérico)', () => {
    expect(periodFromShortcut(key({ code: 'Digit1', key: '1' }))).toBe('CUSTOM')
    expect(periodFromShortcut(key({ code: 'Digit2', key: '2' }))).toBe('FULL_DAY')
    expect(periodFromShortcut(key({ code: 'Numpad3', key: '3' }))).toBe('MORNING')
    expect(periodFromShortcut(key({ code: 'Digit4', key: '¢' }))).toBe('AFTERNOON')
    expect(periodFromShortcut(key({ code: 'Digit5', key: '5' }))).toBeNull()
    expect(periodFromShortcut(key({ altKey: false }))).toBeNull()
    // AltGr no Windows chega como Ctrl+Alt: não é atalho
    expect(periodFromShortcut(key({ ctrlKey: true }))).toBeNull()
  })
})
