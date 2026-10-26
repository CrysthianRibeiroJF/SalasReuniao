import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ServerEvent } from '../api/types'
import { useBookings } from '../store/bookings'
import { bookingsForDate } from '../store/bookingsModel'
import { useLocks } from '../store/locks'
import { usePresence } from '../store/presence'
import { useRealtime } from '../store/realtime'
import { useRooms } from '../store/rooms'
import { makeBooking, makeLock, makeRoom } from '../test/fixtures'
import { applyServerEvent } from './applyEvent'

vi.mock('../api/endpoints', () => ({
  api: {
    bookings: {
      listDay: vi.fn(async () => []),
      listRange: vi.fn(async () => []),
    },
    rooms: { list: vi.fn(async () => []) },
  },
}))

const { api } = await import('../api/endpoints')

const ev = <T extends ServerEvent['type']>(type: T, data: Extract<ServerEvent, { type: T }>['data']) =>
  ({ type, data, ts: new Date().toISOString() }) as ServerEvent

const day = () => bookingsForDate(useBookings.getState().data.byId, '2026-10-26')

beforeEach(() => {
  useBookings.getState().reset()
  useLocks.getState().reset()
  usePresence.getState().reset()
  useRooms.getState().reset()
  useRealtime.getState().reset()
  vi.clearAllMocks()
})

describe('applyServerEvent', () => {
  it('HELLO carrega travas, presença e acerta o relógio do servidor', () => {
    const serverTime = new Date(Date.now() + 5_000).toISOString()
    applyServerEvent(
      ev('HELLO', {
        user: { id: 1, full_name: 'Ana' },
        instance_id: 'i1',
        server_time: serverTime,
        locks: [makeLock({ lock_id: 'a' })],
        online: [{ id: 2, full_name: 'Beatriz', connections: 1 }],
      }),
    )
    expect(Object.keys(useLocks.getState().data.byId)).toEqual(['a'])
    expect(usePresence.getState().online).toHaveLength(1)
    expect(useRealtime.getState().clockSkewMs).toBeGreaterThan(4_000)
  })

  it('BOOKING_CREATED → BOOKING_UPDATED → BOOKING_CANCELLED, aplicados de forma idempotente', () => {
    const b = makeBooking({ version: 1 })
    applyServerEvent(ev('BOOKING_CREATED', { booking: b }))
    applyServerEvent(ev('BOOKING_CREATED', { booking: b }))
    expect(day()).toHaveLength(1)

    const moved = { ...b, version: 2, start: '16:00', end: '17:00' }
    applyServerEvent(
      ev('BOOKING_UPDATED', { booking: moved, previous: { room_id: 1, date: b.date, start: b.start, end: b.end } }),
    )
    applyServerEvent(
      ev('BOOKING_UPDATED', { booking: b, previous: { room_id: 1, date: b.date, start: '16:00', end: '17:00' } }),
    )
    expect(day()[0]?.start).toBe('16:00')

    applyServerEvent(ev('BOOKING_CANCELLED', { booking: { ...moved, version: 3, status: 'CANCELLED' } }))
    expect(day()).toHaveLength(0)
  })

  it('BOOKING_UPDATED para outra data tira a reserva do dia antigo', () => {
    const b = makeBooking({ date: '2026-10-26' })
    applyServerEvent(ev('BOOKING_CREATED', { booking: b }))
    applyServerEvent(
      ev('BOOKING_UPDATED', {
        booking: { ...b, version: 2, date: '2026-10-27' },
        previous: { room_id: b.room_id, date: '2026-10-26', start: b.start, end: b.end },
      }),
    )
    expect(day()).toHaveLength(0)
    expect(bookingsForDate(useBookings.getState().data.byId, '2026-10-27')).toHaveLength(1)
  })

  it('SLOT_LOCKED / SLOT_UNLOCKED mantêm as travas', () => {
    const lock = makeLock({ lock_id: 'x' })
    applyServerEvent(ev('SLOT_LOCKED', { lock }))
    expect(useLocks.getState().data.byId.x).toBeDefined()
    applyServerEvent(ev('SLOT_UNLOCKED', { lock, reason: 'expired' }))
    expect(useLocks.getState().data.byId.x).toBeUndefined()
    applyServerEvent(ev('SLOT_LOCKED', { lock })) // atrasado
    expect(useLocks.getState().data.byId.x).toBeUndefined()
  })

  it('ROOM_UPDATED atualiza a sala e recalcula os selos das reservas dela', () => {
    useRooms.getState().upsert(makeRoom({ id: 3, room_type: 'VIDEO' }))
    const b = makeBooking({ room_id: 3, requires_video: true })
    applyServerEvent(ev('BOOKING_CREATED', { booking: b }))
    const maintenance = makeRoom({
      id: 3,
      room_type: 'VIDEO',
      video_status: 'MAINTENANCE',
      maintenance_ticket_id: 'INC1',
    })
    applyServerEvent(ev('ROOM_UPDATED', { room: maintenance }))
    expect(useRooms.getState().byId[3]?.effective_type).toBe('NORMAL')
    expect(day()[0]?.video_unavailable).toBe(true)
  })

  it('ROOM_CREATED adiciona a sala', () => {
    applyServerEvent(ev('ROOM_CREATED', { room: makeRoom({ id: 9, name: 'Sala Nova' }) }))
    expect(useRooms.getState().byId[9]?.name).toBe('Sala Nova')
  })

  it('PRESENCE substitui a lista de online', () => {
    applyServerEvent(ev('PRESENCE', { online: [{ id: 3, full_name: 'Carla', connections: 2 }] }))
    expect(usePresence.getState().online).toEqual([{ id: 3, full_name: 'Carla', connections: 2 }])
  })

  it('BOOKINGS_IMPORTED recarrega só os dias já carregados que foram afetados', async () => {
    await useBookings.getState().loadDates(['2026-10-26', '2026-10-27', '2026-10-28'])
    vi.mocked(api.bookings.listRange).mockClear()
    vi.mocked(api.bookings.listDay).mockClear()
    applyServerEvent(ev('BOOKINGS_IMPORTED', { dates: ['2026-10-27', '2026-12-01'], count: 4 }))
    await vi.waitFor(() => expect(api.bookings.listDay).toHaveBeenCalledWith('2026-10-27'))
    expect(api.bookings.listRange).not.toHaveBeenCalled()
  })
})
