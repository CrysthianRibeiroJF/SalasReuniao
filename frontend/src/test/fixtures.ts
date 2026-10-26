/** Fábricas de objetos da API para os testes (formato idêntico ao backend). */
import type { BookingOut, ConfigOut, LockOut, RoomOut, UserOut } from '../api/types'

export const CONFIG: ConfigOut = {
  timezone: 'America/Sao_Paulo',
  today: '2026-10-26',
  day_start: '08:00',
  day_end: '19:00',
  slot_minutes: 30,
  morning: { start: '08:00', end: '12:00' },
  afternoon: { start: '13:00', end: '19:00' },
  work_days: [0, 1, 2, 3, 4],
  lock_ttl_seconds: 120,
  ticket_url_template: 'https://chamados.example.com/ticket/{ticket}',
  realtime_backend: 'memory',
}

export const ADMIN: UserOut = {
  id: 1,
  username: 'ana',
  full_name: 'Ana Paula Ribeiro',
  role: 'ADMIN',
  is_active: true,
  created_at: '2026-01-01T10:00:00-03:00',
  last_login_at: null,
}

export function makeRoom(overrides: Partial<RoomOut> = {}): RoomOut {
  const roomType = overrides.room_type ?? 'NORMAL'
  const videoStatus = overrides.video_status ?? 'OPERATIONAL'
  return {
    id: 1,
    name: 'Sala 01',
    room_type: roomType,
    effective_type: roomType === 'VIDEO' && videoStatus === 'OPERATIONAL' ? 'VIDEO' : 'NORMAL',
    capacity: 8,
    resources: [],
    video_status: videoStatus,
    maintenance_ticket_id: null,
    maintenance_ticket_url: null,
    maintenance_since: null,
    display_order: 1,
    is_active: true,
    ...overrides,
  }
}

let bookingSeq = 0

export function makeBooking(overrides: Partial<BookingOut> = {}): BookingOut {
  bookingSeq++
  const date = overrides.date ?? '2026-10-26'
  const start = overrides.start ?? '14:00'
  const end = overrides.end ?? '15:00'
  return {
    id: `b-${bookingSeq}`,
    room_id: 1,
    room_name: 'Sala 01',
    ticket_number: '123456',
    ticket_url: 'https://chamados.example.com/ticket/123456',
    title: 'Comitê de Crédito',
    requester_name: 'Marcos Lima',
    notes: null,
    attendees: null,
    date,
    start,
    end,
    start_time: `${date}T${start}:00-03:00`,
    end_time: `${date}T${end}:00-03:00`,
    period_type: 'CUSTOM',
    is_full_day: false,
    requires_video: false,
    status: 'CONFIRMED',
    version: 1,
    is_relocatable: false,
    video_unavailable: false,
    created_by: { id: 2, full_name: 'Beatriz Santos' },
    updated_by: null,
    created_at: '2026-10-20T09:00:00-03:00',
    updated_at: '2026-10-20T09:00:00-03:00',
    cancelled_at: null,
    cancel_reason: null,
    ...overrides,
  }
}

export function makeLock(overrides: Partial<LockOut> = {}): LockOut {
  return {
    lock_id: 'lock-1',
    room_id: 1,
    date: '2026-10-26',
    start: '14:00',
    end: '15:00',
    user: { id: 2, full_name: 'Beatriz Santos' },
    expires_at: new Date(Date.now() + 120_000).toISOString(),
    booking_id: null,
    ...overrides,
  }
}
