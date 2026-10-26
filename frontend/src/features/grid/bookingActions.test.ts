import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/errors'
import { useBookings } from '../../store/bookings'
import { useToasts } from '../../store/toasts'
import { makeBooking } from '../../test/fixtures'
import { cancelBooking, changeStatus, moveBooking, movePatch } from './bookingActions'

vi.mock('../../api/endpoints', () => ({
  api: {
    bookings: {
      update: vi.fn(),
      cancel: vi.fn(),
      setStatus: vi.fn(),
      listDay: vi.fn(async () => []),
      listRange: vi.fn(async () => []),
    },
  },
}))

const { api } = await import('../../api/endpoints')

beforeEach(() => {
  vi.clearAllMocks()
  useBookings.getState().reset()
  useToasts.getState().clear()
})

describe('movePatch', () => {
  const b = makeBooking({ room_id: 1, start: '14:00', end: '15:00', version: 4 })

  it('só a sala quando o horário não muda', () => {
    expect(movePatch(b, { roomId: 3, start: '14:00', end: '15:00' })).toEqual({ version: 4, room_id: 3 })
  })

  it('horário novo vira Horário Específico', () => {
    expect(movePatch(b, { roomId: 1, start: '16:00', end: '17:00' })).toEqual({
      version: 4,
      period_type: 'CUSTOM',
      start: '16:00',
      end: '17:00',
    })
  })
})

describe('moveBooking (arrastar e soltar)', () => {
  it('aplica o resultado do PATCH', async () => {
    const b = makeBooking({ version: 1 })
    vi.mocked(api.bookings.update).mockResolvedValue({ booking: { ...b, room_id: 3, version: 2 }, warnings: [] })
    const updated = await moveBooking(b, { roomId: 3, start: b.start, end: b.end })
    expect(updated?.room_id).toBe(3)
    expect(useBookings.getState().data.byId[b.id]?.version).toBe(2)
  })

  it('conflito → toast de erro e nada muda', async () => {
    const b = makeBooking()
    useBookings.getState().upsert(b)
    vi.mocked(api.bookings.update).mockRejectedValue(
      new ApiError(409, 'BOOKING_CONFLICT', 'Conflito', {
        conflicts: [makeBooking({ ticket_number: '777', requester_name: 'Juliana', start: '16:00', end: '17:00' })],
        alternatives: [],
      }),
    )
    expect(await moveBooking(b, { roomId: 2, start: b.start, end: b.end })).toBeNull()
    const t = useToasts.getState().toasts[0]
    expect(t).toMatchObject({ kind: 'error', title: 'Horário ocupado nesta sala' })
    expect(t?.description).toContain('#777')
    expect(useBookings.getState().data.byId[b.id]).toEqual(b)
  })

  it('trava de outra pessoa → toast com o nome', async () => {
    const b = makeBooking()
    vi.mocked(api.bookings.update).mockRejectedValue(
      new ApiError(409, 'SLOT_LOCKED', 'Carla está reservando este horário agora.', { locks: [] }),
    )
    await moveBooking(b, { roomId: 2, start: b.start, end: b.end })
    expect(useToasts.getState().toasts[0]?.title).toBe('Carla está reservando este horário agora.')
  })

  it('STALE_VERSION → aviso "alterada por outra pessoa" e recarrega', async () => {
    const b = makeBooking({ version: 1 })
    useBookings.getState().upsert(b)
    const current = { ...b, version: 3, requester_name: 'Atualizado' }
    vi.mocked(api.bookings.update).mockRejectedValue(new ApiError(409, 'STALE_VERSION', 'x', { current }))
    vi.mocked(api.bookings.listDay).mockResolvedValue([current])
    await moveBooking(b, { roomId: 2, start: b.start, end: b.end })
    expect(useToasts.getState().toasts[0]?.title).toBe('Esta reserva foi alterada por outra pessoa')
    expect(useBookings.getState().data.byId[b.id]?.requester_name).toBe('Atualizado')
    expect(api.bookings.listDay).toHaveBeenCalledWith(b.date)
  })

  it('não chama a API quando nada mudou', async () => {
    const b = makeBooking()
    await moveBooking(b, { roomId: b.room_id, start: b.start, end: b.end })
    expect(api.bookings.update).not.toHaveBeenCalled()
  })
})

describe('status e cancelamento', () => {
  it('check-in envia a version', async () => {
    const b = makeBooking({ version: 5 })
    vi.mocked(api.bookings.setStatus).mockResolvedValue({ ...b, status: 'CHECKED_IN', version: 6 })
    expect(await changeStatus(b, 'CHECKED_IN')).toBe(true)
    expect(api.bookings.setStatus).toHaveBeenCalledWith(b.id, { version: 5, status: 'CHECKED_IN' })
    expect(useToasts.getState().toasts[0]?.title).toBe('Check-in registrado')
  })

  it('cancelar envia o motivo (opcional) e tira da grade', async () => {
    const b = makeBooking({ version: 2 })
    useBookings.getState().upsert(b)
    vi.mocked(api.bookings.cancel).mockResolvedValue({ ...b, status: 'CANCELLED', version: 3 })
    expect(await cancelBooking(b, '  reunião remarcada  ')).toBe(true)
    expect(api.bookings.cancel).toHaveBeenCalledWith(b.id, { version: 2, reason: 'reunião remarcada' })
    expect(useBookings.getState().data.byId[b.id]?.status).toBe('CANCELLED')
    await cancelBooking({ ...b, version: 3 }, '   ')
    expect(vi.mocked(api.bookings.cancel).mock.calls[1]![1]).toEqual({ version: 3, reason: null })
  })
})
