/** Diferença legível entre previous_state e new_state dos logs (auditoria e histórico). */
import type { BookingLogOut, BookingSnapshot, BookingStatus, PeriodType } from '../api/types'
import { ACTION_LABELS, STATUS_LABELS } from './labels'
import { PERIOD_LABELS } from './periods'
import { formatDateBR, isValidISODate } from './time'

type SnapshotField = keyof BookingSnapshot

const FIELDS: readonly { field: SnapshotField; label: string }[] = [
  { field: 'room_name', label: 'Sala' },
  { field: 'date', label: 'Data' },
  { field: 'start', label: 'Início' },
  { field: 'end', label: 'Fim' },
  { field: 'period_type', label: 'Período' },
  { field: 'ticket_number', label: 'Chamado' },
  { field: 'requester_name', label: 'Solicitante' },
  { field: 'title', label: 'Assunto/Área' },
  { field: 'requires_video', label: 'Precisa de vídeo' },
  { field: 'attendees', label: 'Participantes' },
  { field: 'notes', label: 'Observações' },
  { field: 'status', label: 'Status' },
]

export interface DiffRow {
  field: SnapshotField
  label: string
  before: string | null
  after: string | null
}

type Snapshot = Partial<BookingSnapshot> | null | undefined

export function formatSnapshotValue(field: SnapshotField, value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  switch (field) {
    case 'date':
      return typeof value === 'string' && isValidISODate(value) ? formatDateBR(value) : String(value)
    case 'requires_video':
    case 'is_full_day':
      return value ? 'Sim' : 'Não'
    case 'period_type':
      return PERIOD_LABELS[value as PeriodType] ?? String(value)
    case 'status':
      return STATUS_LABELS[value as BookingStatus] ?? String(value)
    case 'ticket_number':
      return `#${String(value)}`
    default:
      return String(value)
  }
}

function roomLabel(snapshot: Snapshot): string | null {
  if (!snapshot) return null
  if (snapshot.room_name) return snapshot.room_name
  return snapshot.room_id !== undefined ? `Sala nº ${snapshot.room_id}` : null
}

function valueOf(snapshot: Snapshot, field: SnapshotField): string | null {
  if (!snapshot) return null
  if (field === 'room_name') return roomLabel(snapshot)
  return formatSnapshotValue(field, snapshot[field])
}

/** Campos que mudaram (ou, sem estado anterior, os preenchidos no novo estado). */
export function snapshotDiff(previous: Snapshot, next: Snapshot): DiffRow[] {
  const rows: DiffRow[] = []
  for (const { field, label } of FIELDS) {
    const before = valueOf(previous, field)
    const after = valueOf(next, field)
    if (previous && next && before === after) continue
    if (!previous && after === null) continue
    if (!next && before === null) continue
    rows.push({ field, label, before: previous ? before : null, after: next ? after : null })
  }
  return rows
}

/** Campos que o resumo (summarizeLog) de cada ação já mostra. */
const SUMMARY_FIELDS: Partial<Record<BookingLogOut['action'], readonly SnapshotField[]>> = {
  MOVED_ROOM: ['room_name'],
  RESCHEDULED: ['date', 'start', 'end'],
  STATUS_CHANGED: ['status'],
  CANCELLED: ['status'],
}

/**
 * Detalhe de uma linha do histórico: tudo o que mudou além do que o resumo já diz. Um MOVED_ROOM
 * pode ter mudado também horário, chamado ou solicitante na mesma edição (models.py), e isso
 * precisa aparecer no histórico da reserva, não só na Auditoria.
 */
export function logDetailRows(log: Pick<BookingLogOut, 'action' | 'previous_state' | 'new_state'>): DiffRow[] {
  if (!log.previous_state) return []
  const shown = SUMMARY_FIELDS[log.action] ?? []
  return snapshotDiff(log.previous_state, log.new_state).filter((row) => !shown.includes(row.field))
}

/** Resumo de uma linha do log: "Sala 01 → Sala 03", "14:00–15:00 → 15:00–16:00"… */
export function summarizeLog(log: Pick<BookingLogOut, 'action' | 'previous_state' | 'new_state'>): string {
  const prev = log.previous_state
  const next = log.new_state
  const range = (s: Snapshot) => (s?.start && s?.end ? `${s.start}–${s.end}` : '')
  switch (log.action) {
    case 'MOVED_ROOM':
      return `${roomLabel(prev) ?? '?'} → ${roomLabel(next) ?? '?'}`
    case 'RESCHEDULED': {
      const dateChanged = prev?.date !== next?.date
      const before = `${dateChanged && prev?.date ? `${formatSnapshotValue('date', prev.date)} ` : ''}${range(prev)}`
      const after = `${dateChanged && next?.date ? `${formatSnapshotValue('date', next.date)} ` : ''}${range(next)}`
      return `${before} → ${after}`
    }
    case 'STATUS_CHANGED':
      return `${formatSnapshotValue('status', prev?.status) ?? '?'} → ${formatSnapshotValue('status', next?.status) ?? '?'}`
    case 'UPDATED': {
      const fields = snapshotDiff(prev, next).map((r) => r.label.toLowerCase())
      return fields.length ? `Alterou ${fields.join(', ')}` : 'Dados atualizados'
    }
    case 'CREATED':
    case 'IMPORTED':
      return [roomLabel(next), next?.date ? formatSnapshotValue('date', next.date) : null, range(next)]
        .filter(Boolean)
        .join(' · ')
    case 'CANCELLED':
      return 'Reserva cancelada'
    default:
      return ACTION_LABELS[log.action] ?? ''
  }
}
