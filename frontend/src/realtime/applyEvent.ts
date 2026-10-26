/**
 * Aplica eventos do servidor nos stores. Cada caso é idempotente: reaplicar o mesmo evento
 * (ou recebê-lo fora de ordem) não altera o resultado — as regras ficam em bookingsModel/locksModel.
 */
import type { ServerEvent } from '../api/types'
import { useBookings } from '../store/bookings'
import { useLocks } from '../store/locks'
import { usePresence } from '../store/presence'
import { useRealtime } from '../store/realtime'
import { useRooms } from '../store/rooms'

export function applyServerEvent(event: ServerEvent): void {
  switch (event.type) {
    case 'HELLO':
      useRealtime.getState().setServerTime(event.data.server_time)
      useLocks.getState().replaceAll(event.data.locks ?? [])
      usePresence.getState().setOnline(event.data.online ?? [])
      break

    case 'LOCK_ACQUIRED':
    case 'LOCK_REFRESHED':
    case 'SLOT_LOCKED':
      useLocks.getState().upsert(event.data.lock)
      break

    case 'SLOT_UNLOCKED':
      useLocks.getState().remove(event.data.lock.lock_id)
      break

    case 'BOOKING_CREATED':
    case 'BOOKING_UPDATED':
    case 'BOOKING_CANCELLED':
      useBookings.getState().upsert(event.data.booking)
      break

    case 'BOOKINGS_IMPORTED':
      void useBookings.getState().refetchLoaded(event.data.dates?.length ? event.data.dates : undefined)
      break

    case 'ROOM_CREATED':
    case 'ROOM_UPDATED':
      useRooms.getState().upsert(event.data.room)
      useBookings.getState().applyRoom(event.data.room)
      break

    case 'PRESENCE':
      usePresence.getState().setOnline(event.data.online ?? [])
      break

    case 'LOCK_DENIED':
    case 'PONG':
    case 'ERROR':
      // respostas a pedidos: tratadas pelo RealtimeClient (request_id)
      break
  }
}
