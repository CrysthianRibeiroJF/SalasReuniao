import { describe, expect, it } from 'vitest'

import { makeBooking } from '../../../test/fixtures'
import type { ComposerState } from '../../../store/ui'
import {
  type ComposerForm,
  createPayload,
  initialForm,
  rebaseForm,
  updatePayload,
  validateForm,
} from './BookingComposer'

const composer: ComposerState = {
  key: 1,
  mode: 'create',
  bookingId: null,
  roomId: 3,
  date: '2026-10-26',
  period: 'CUSTOM',
  start: '14:00',
  end: '15:30',
  lockId: 'L1',
  anchor: null,
}

const form = (overrides: Partial<ComposerForm> = {}): ComposerForm => ({ ...initialForm(composer), ...overrides })

describe('formulário do modal', () => {
  it('nova reserva começa vazia, sem vídeo, com a seleção', () => {
    expect(initialForm(composer)).toEqual({
      roomId: 3,
      date: '2026-10-26',
      period: 'CUSTOM',
      start: '14:00',
      end: '15:30',
      ticket: '',
      requester: '',
      title: '',
      requiresVideo: false,
      attendees: '',
      notes: '',
    })
  })

  it('edição carrega a reserva', () => {
    const b = makeBooking({ ticket_number: null, attendees: 6, notes: 'Café', requires_video: true })
    expect(initialForm({ ...composer, mode: 'edit', bookingId: b.id }, b)).toMatchObject({
      ticket: '',
      attendees: '6',
      notes: 'Café',
      requiresVideo: true,
      period: 'CUSTOM',
    })
  })

  it('valida solicitante, participantes e intervalo', () => {
    expect(validateForm(form({ requester: '  ' }), { start: '14:00', end: '15:00' })).toEqual({
      requester: 'Informe o solicitante.',
    })
    expect(validateForm(form({ requester: 'Ana', attendees: '2.5' }), { start: '14:00', end: '15:00' })).toEqual({
      attendees: 'Use um número inteiro maior que zero.',
    })
    expect(validateForm(form({ requester: 'Ana' }), { start: '15:00', end: '15:00' })).toEqual({
      end: 'O fim deve ser depois do início.',
    })
    expect(validateForm(form({ requester: 'Ana', attendees: '4' }), { start: '14:00', end: '15:00' })).toEqual({})
  })

  it('POST: presets não mandam início/fim; textos vazios viram null', () => {
    expect(
      createPayload(
        form({ requester: ' Ana ', ticket: ' 77 ', period: 'FULL_DAY' }),
        { start: '08:00', end: '19:00' },
        null,
      ),
    ).toEqual({
      room_id: 3,
      date: '2026-10-26',
      period_type: 'FULL_DAY',
      ticket_number: '77',
      title: null,
      requester_name: 'Ana',
      requires_video: false,
      attendees: null,
      notes: null,
      lock_id: null,
    })
  })

  it('PATCH envia só o que mudou + version', () => {
    const b = makeBooking({ room_id: 3, start: '14:00', end: '15:00', ticket_number: '1', title: 'A', version: 9 })
    const base = initialForm({ ...composer, mode: 'edit', bookingId: b.id }, b)
    expect(updatePayload(b, base, { start: '14:00', end: '15:00' }, 9, 'L9')).toEqual({ version: 9 })
    expect(updatePayload(b, { ...base, title: '' }, { start: '14:00', end: '15:00' }, 9, null)).toEqual({
      version: 9,
      title: null,
    })
    expect(updatePayload(b, { ...base, end: '16:00' }, { start: '14:00', end: '16:00' }, 9, 'L9')).toEqual({
      version: 9,
      period_type: 'CUSTOM',
      start: '14:00',
      end: '16:00',
      lock_id: 'L9',
    })
    expect(updatePayload(b, { ...base, period: 'AFTERNOON' }, { start: '13:00', end: '19:00' }, 9, null)).toEqual({
      version: 9,
      period_type: 'AFTERNOON',
    })
    expect(
      updatePayload(b, { ...base, roomId: 5, requiresVideo: true }, { start: '14:00', end: '15:00' }, 9, null),
    ).toEqual({
      version: 9,
      room_id: 5,
      requires_video: true,
    })
  })

  it('edição concorrente: campos não mexidos vão para a versão nova, os alterados ficam', () => {
    const editComposer = { ...composer, mode: 'edit' as const, bookingId: 'b' }
    const v1 = makeBooking({ room_id: 3, title: 'Pauta', requester_name: 'Marcos', end: '15:00', version: 1 })
    const v2 = { ...v1, title: 'Pauta da Ana', end: '16:00', room_id: 4, version: 2 }
    const before = initialForm(editComposer, v1)
    const mine = { ...before, requester: 'Solicitante da Bia', notes: 'Trazer crachá' }
    expect(rebaseForm(mine, before, initialForm(editComposer, v2))).toEqual({
      ...initialForm(editComposer, v2),
      requester: 'Solicitante da Bia',
      notes: 'Trazer crachá',
    })
    // Os dois mudaram o mesmo campo: fica o valor desta pessoa.
    expect(rebaseForm({ ...before, title: 'Pauta da Bia' }, before, initialForm(editComposer, v2)).title).toBe(
      'Pauta da Bia',
    )
  })
})
