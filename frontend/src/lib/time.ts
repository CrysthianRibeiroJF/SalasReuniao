/**
 * Datas e horas locais da recepção. Datas trafegam como "YYYY-MM-DD" e horas como "HH:MM";
 * a aritmética de calendário usa Date em UTC para nunca depender do fuso do navegador.
 */
import type { HHMM, ISODate } from '../api/types'

export const WEEKDAY_SHORT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'] as const
export const WEEKDAY_LONG = [
  'segunda-feira',
  'terça-feira',
  'quarta-feira',
  'quinta-feira',
  'sexta-feira',
  'sábado',
  'domingo',
] as const
export const MONTH_SHORT = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'] as const
export const MONTH_LONG = [
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
] as const

const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/
const HHMM_RE = /^(\d{1,2}):(\d{2})$/

// ------------------------------------------------------------------ horas

export function toMinutes(hhmm: HHMM): number {
  const match = HHMM_RE.exec(hhmm)
  if (!match) throw new Error(`Horário inválido: ${hhmm}`)
  return Number(match[1]) * 60 + Number(match[2])
}

export function fromMinutes(minutes: number): HHMM {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

export function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h === 0) return `${m} min`
  return m === 0 ? `${h} h` : `${h} h ${m} min`
}

// ------------------------------------------------------------------ datas

export function isValidISODate(value: string | null | undefined): value is ISODate {
  if (!value) return false
  const match = ISO_DATE_RE.exec(value)
  if (!match) return false
  const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(Date.UTC(y, m - 1, d))
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
}

function parts(iso: ISODate): [number, number, number] {
  const match = ISO_DATE_RE.exec(iso)
  if (!match) throw new Error(`Data inválida: ${iso}`)
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

function toUTC(iso: ISODate): Date {
  const [y, m, d] = parts(iso)
  return new Date(Date.UTC(y, m - 1, d))
}

function fromUTC(date: Date): ISODate {
  return date.toISOString().slice(0, 10)
}

export function addDays(iso: ISODate, days: number): ISODate {
  const date = toUTC(iso)
  date.setUTCDate(date.getUTCDate() + days)
  return fromUTC(date)
}

/** 0 = segunda-feira … 6 = domingo (mesma convenção de `work_days` do backend). */
export function weekdayIndex(iso: ISODate): number {
  return (toUTC(iso).getUTCDay() + 6) % 7
}

export function weekMonday(iso: ISODate): ISODate {
  return addDays(iso, -weekdayIndex(iso))
}

/** Segunda a domingo da semana de `iso`. */
export function weekDates(iso: ISODate): ISODate[] {
  const monday = weekMonday(iso)
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i))
}

export function daysBetween(a: ISODate, b: ISODate): number {
  return Math.round((toUTC(b).getTime() - toUTC(a).getTime()) / 86_400_000)
}

/** Rótulo da aba diária: "Seg 26/10". */
export function formatTabLabel(iso: ISODate): string {
  const [, m, d] = parts(iso)
  return `${WEEKDAY_SHORT[weekdayIndex(iso)]} ${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}`
}

/** "26/10/2026" */
export function formatDateBR(iso: ISODate): string {
  const [y, m, d] = parts(iso)
  return `${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}/${y}`
}

/** "segunda-feira, 26 de outubro" */
export function formatDateLong(iso: ISODate, withYear = false): string {
  const [y, m, d] = parts(iso)
  const base = `${WEEKDAY_LONG[weekdayIndex(iso)]}, ${d} de ${MONTH_LONG[m - 1]}`
  return withYear ? `${base} de ${y}` : base
}

/** "26 – 30 out 2026" (ou "28 out – 1 nov 2026" quando cruza o mês). */
export function formatWeekRange(first: ISODate, last: ISODate): string {
  const [y1, m1, d1] = parts(first)
  const [y2, m2, d2] = parts(last)
  if (y1 !== y2) return `${d1} ${MONTH_SHORT[m1 - 1]} ${y1} – ${d2} ${MONTH_SHORT[m2 - 1]} ${y2}`
  if (m1 !== m2) return `${d1} ${MONTH_SHORT[m1 - 1]} – ${d2} ${MONTH_SHORT[m2 - 1]} ${y2}`
  return `${d1} – ${d2} ${MONTH_SHORT[m2 - 1]} ${y2}`
}

// ------------------------------------------------------------------ "agora" no fuso da recepção

export interface ZonedNow {
  date: ISODate
  /** Minutos desde a meia-noite local (com fração de segundos). */
  minutes: number
}

const formatterCache = new Map<string, Intl.DateTimeFormat>()

function zonedFormatter(timeZone: string): Intl.DateTimeFormat {
  let fmt = formatterCache.get(timeZone)
  if (!fmt) {
    fmt = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    })
    formatterCache.set(timeZone, fmt)
  }
  return fmt
}

export function zonedParts(epochMs: number, timeZone: string): ZonedNow {
  const values: Record<string, string> = {}
  for (const part of zonedFormatter(timeZone).formatToParts(new Date(epochMs))) values[part.type] = part.value
  const hour = Number(values.hour) % 24
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    minutes: hour * 60 + Number(values.minute) + Number(values.second) / 60,
  }
}

/** "26/10/2026 14:03" no fuso da recepção. */
export function formatDateTime(isoDateTime: string, timeZone: string): string {
  const epoch = Date.parse(isoDateTime)
  if (Number.isNaN(epoch)) return isoDateTime
  const { date, minutes } = zonedParts(epoch, timeZone)
  return `${formatDateBR(date)} ${fromMinutes(Math.floor(minutes))}`
}

/** "há 3 min", "há 2 h", "ontem 14:03"… para históricos curtos. */
export function formatRelative(isoDateTime: string, nowMs: number, timeZone: string): string {
  const epoch = Date.parse(isoDateTime)
  if (Number.isNaN(epoch)) return isoDateTime
  const diff = Math.max(0, nowMs - epoch)
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return 'agora há pouco'
  if (minutes < 60) return `há ${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 12) return `há ${hours} h`
  return formatDateTime(isoDateTime, timeZone)
}

/**
 * Agrupa datas em faixas contíguas [início, fim] de no máximo `maxSpan` dias
 * (o endpoint de listagem aceita no máximo 31 dias por chamada).
 */
export function chunkDateRanges(dates: Iterable<ISODate>, maxSpan = 31): [ISODate, ISODate][] {
  const sorted = [...new Set(dates)].sort()
  const ranges: [ISODate, ISODate][] = []
  for (const date of sorted) {
    const last = ranges[ranges.length - 1]
    if (last && daysBetween(last[1], date) === 1 && daysBetween(last[0], date) < maxSpan) {
      last[1] = date
    } else {
      ranges.push([date, date])
    }
  }
  return ranges
}
