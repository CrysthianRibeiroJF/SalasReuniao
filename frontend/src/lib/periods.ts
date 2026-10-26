import type { ConfigOut, HHMM, PeriodType } from '../api/types'

export interface PeriodOption {
  type: PeriodType
  label: string
  /** Dígito do atalho Alt+N dentro do modal. */
  shortcut: '1' | '2' | '3' | '4'
}

/** Ordem do controle segmentado "Tipo de Período" (Alt+1..4). */
export const PERIOD_OPTIONS: readonly PeriodOption[] = [
  { type: 'CUSTOM', label: 'Horário Específico', shortcut: '1' },
  { type: 'FULL_DAY', label: 'Dia Inteiro', shortcut: '2' },
  { type: 'MORNING', label: 'Manhã', shortcut: '3' },
  { type: 'AFTERNOON', label: 'Tarde', shortcut: '4' },
]

/** Rótulos usados em selos, auditoria e relatórios. */
export const PERIOD_LABELS: Record<PeriodType, string> = {
  CUSTOM: 'Horário específico',
  FULL_DAY: 'Dia inteiro',
  MORNING: 'Manhã toda',
  AFTERNOON: 'Tarde toda',
}

export interface TimeRange {
  start: HHMM
  end: HHMM
}

type CalendarConfig = Pick<ConfigOut, 'day_start' | 'day_end' | 'morning' | 'afternoon'>

/** Horários fixos de cada preset (espelha timeutil.period_times); null para CUSTOM. */
export function presetRange(type: PeriodType, config: CalendarConfig): TimeRange | null {
  switch (type) {
    case 'FULL_DAY':
      return { start: config.day_start, end: config.day_end }
    case 'MORNING':
      return { start: config.morning.start, end: config.morning.end }
    case 'AFTERNOON':
      return { start: config.afternoon.start, end: config.afternoon.end }
    default:
      return null
  }
}

/** Faixa efetiva do formulário: presets ignoram o início/fim digitados. */
export function resolvePeriod(type: PeriodType, config: CalendarConfig, custom: TimeRange): TimeRange {
  return presetRange(type, config) ?? custom
}

interface ShortcutEvent {
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  shiftKey?: boolean
  code: string
  key: string
}

/** Alt+1..4 (linha numérica ou teclado numérico) → tipo de período. */
export function periodFromShortcut(event: ShortcutEvent): PeriodType | null {
  if (!event.altKey || event.ctrlKey || event.metaKey) return null
  const digit = /^(?:Digit|Numpad)([1-4])$/.exec(event.code)?.[1] ?? (/^[1-4]$/.test(event.key) ? event.key : null)
  return PERIOD_OPTIONS.find((o) => o.shortcut === digit)?.type ?? null
}
