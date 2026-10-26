import { describe, expect, it } from 'vitest'

import type { BookingSnapshot } from '../api/types'
import { formatSnapshotValue, logDetailRows, snapshotDiff, summarizeLog } from './auditDiff'
import { ACTION_LABELS } from './labels'

const base: BookingSnapshot = {
  room_id: 1,
  room_name: 'Sala 01',
  ticket_number: '123',
  title: 'Comitê',
  requester_name: 'Marcos',
  notes: null,
  attendees: null,
  date: '2026-10-26',
  start: '14:00',
  end: '15:00',
  period_type: 'CUSTOM',
  is_full_day: false,
  requires_video: false,
  status: 'CONFIRMED',
  version: 1,
}

describe('auditoria', () => {
  it('rótulos de ação em pt-BR', () => {
    expect(ACTION_LABELS).toEqual({
      CREATED: 'Criou',
      IMPORTED: 'Importou',
      UPDATED: 'Editou',
      MOVED_ROOM: 'Trocou de sala',
      RESCHEDULED: 'Mudou o horário',
      STATUS_CHANGED: 'Mudou o status',
      CANCELLED: 'Cancelou',
    })
  })

  it('formata valores de forma legível', () => {
    expect(formatSnapshotValue('date', '2026-10-26')).toBe('26/10/2026')
    expect(formatSnapshotValue('requires_video', true)).toBe('Sim')
    expect(formatSnapshotValue('period_type', 'FULL_DAY')).toBe('Dia inteiro')
    expect(formatSnapshotValue('status', 'NO_SHOW')).toBe('Não compareceu')
    expect(formatSnapshotValue('ticket_number', '123')).toBe('#123')
    expect(formatSnapshotValue('notes', '')).toBeNull()
  })

  it('lista só os campos que mudaram (versão não conta)', () => {
    const next = { ...base, room_id: 3, room_name: 'Sala 03', start: '15:00', end: '16:00', version: 2 }
    expect(snapshotDiff(base, next)).toEqual([
      { field: 'room_name', label: 'Sala', before: 'Sala 01', after: 'Sala 03' },
      { field: 'start', label: 'Início', before: '14:00', after: '15:00' },
      { field: 'end', label: 'Fim', before: '15:00', after: '16:00' },
    ])
  })

  it('criação mostra os campos preenchidos', () => {
    const rows = snapshotDiff(null, base)
    expect(rows.map((r) => r.label)).toEqual([
      'Sala',
      'Data',
      'Início',
      'Fim',
      'Período',
      'Chamado',
      'Solicitante',
      'Assunto/Área',
      'Precisa de vídeo',
      'Status',
    ])
    expect(rows.every((r) => r.before === null)).toBe(true)
  })

  it('usa o id quando o nome da sala não veio no snapshot', () => {
    expect(snapshotDiff({ ...base, room_name: null }, { ...base, room_name: null, room_id: 4 })[0]).toMatchObject({
      before: 'Sala nº 1',
      after: 'Sala nº 4',
    })
  })

  it('resume cada tipo de ação', () => {
    expect(
      summarizeLog({ action: 'MOVED_ROOM', previous_state: base, new_state: { ...base, room_name: 'Sala 05' } }),
    ).toBe('Sala 01 → Sala 05')
    expect(
      summarizeLog({
        action: 'RESCHEDULED',
        previous_state: base,
        new_state: { ...base, start: '16:00', end: '17:00' },
      }),
    ).toBe('14:00–15:00 → 16:00–17:00')
    expect(
      summarizeLog({ action: 'RESCHEDULED', previous_state: base, new_state: { ...base, date: '2026-10-27' } }),
    ).toBe('26/10/2026 14:00–15:00 → 27/10/2026 14:00–15:00')
    expect(
      summarizeLog({ action: 'STATUS_CHANGED', previous_state: base, new_state: { ...base, status: 'CHECKED_IN' } }),
    ).toBe('Confirmada → Check-in feito')
    expect(
      summarizeLog({ action: 'UPDATED', previous_state: base, new_state: { ...base, title: 'Outro', notes: 'x' } }),
    ).toBe('Alterou assunto/área, observações')
    expect(summarizeLog({ action: 'CREATED', previous_state: null, new_state: base })).toBe(
      'Sala 01 · 26/10/2026 · 14:00–15:00',
    )
    expect(
      summarizeLog({ action: 'CANCELLED', previous_state: base, new_state: { ...base, status: 'CANCELLED' } }),
    ).toBe('Reserva cancelada')
  })
})

describe('histórico da reserva (R31)', () => {
  it('logDetailRows: o que mudou além do resumo (troca de sala + horário + chamado)', () => {
    const rows = logDetailRows({
      action: 'MOVED_ROOM',
      previous_state: { room_name: 'Sala 01', start: '14:00', end: '15:00', ticket_number: '111' },
      new_state: { room_name: 'Sala 03', start: '15:00', end: '16:00', ticket_number: '222' },
    })
    expect(rows.map((r) => r.field)).toEqual(['start', 'end', 'ticket_number'])
  })

  it('RESCHEDULED sem outras mudanças não repete o horário; criação não tem detalhe', () => {
    expect(
      logDetailRows({
        action: 'RESCHEDULED',
        previous_state: { date: '2026-10-26', start: '14:00', end: '15:00' },
        new_state: { date: '2026-10-27', start: '14:00', end: '15:00' },
      }),
    ).toEqual([])
    expect(logDetailRows({ action: 'CREATED', previous_state: null, new_state: { room_name: 'Sala 01' } })).toEqual([])
  })
})
