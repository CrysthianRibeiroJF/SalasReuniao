/**
 * Escritas de reservas fora do modal (arrastar, check-in, não compareceu, cancelar) com o
 * tratamento de erro comum: versão desatualizada recarrega, conflito/trava viram aviso.
 */
import { asConflict, asSlotLocked, asStaleVersion, errorMessage } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { BookingOut, BookingStatusIn, BookingUpdate, BusinessWarning, HHMM } from '../../api/types'
import { useBookings } from '../../store/bookings'
import { toast } from '../../store/toasts'

export const STALE_MESSAGE = 'Esta reserva foi alterada por outra pessoa'

/** Avisos não bloqueantes do servidor viram toasts; `subject` diz de qual reserva são (ex.: "Reunião de Ana"). */
export function showWarnings(warnings: readonly BusinessWarning[], subject?: string): void {
  for (const warning of warnings) toast.warning(subject ? `${subject}: ${warning.message}` : warning.message)
}

/** Trata erros de escrita de uma reserva; devolve a mensagem exibida. */
export function reportWriteError(error: unknown, booking: BookingOut, action: string): string {
  const stale = asStaleVersion(error)
  if (stale) {
    useBookings.getState().upsert(stale.current)
    void useBookings.getState().loadDates([booking.date, stale.current.date])
    toast.warning(STALE_MESSAGE, { description: 'Os dados foram recarregados. Confira e tente de novo.' })
    return STALE_MESSAGE
  }
  const locked = asSlotLocked(error)
  if (locked) {
    toast.error(locked.message || 'Outra pessoa está reservando este horário agora.')
    return locked.message
  }
  const conflict = asConflict(error)
  if (conflict) {
    const other = conflict.conflicts[0]
    const description = other
      ? `Já existe ${other.ticket_number ? `o chamado #${other.ticket_number}` : 'uma reserva'} de ${other.requester_name} das ${other.start} às ${other.end}.`
      : undefined
    toast.error('Horário ocupado nesta sala', { description })
    return conflict.message
  }
  const message = errorMessage(error, `Não foi possível ${action}.`)
  toast.error(message)
  return message
}

export interface MoveTarget {
  roomId: number
  start: HHMM
  end: HHMM
}

/** PATCH de remanejamento (arrastar e soltar ou "Mover para Sala Y"). */
export function movePatch(booking: BookingOut, target: MoveTarget): BookingUpdate {
  const patch: BookingUpdate = { version: booking.version }
  if (target.roomId !== booking.room_id) patch.room_id = target.roomId
  if (target.start !== booking.start || target.end !== booking.end) {
    // Mudar o horário de um preset (Dia inteiro/Manhã/Tarde) transforma a reserva em horário específico.
    patch.period_type = 'CUSTOM'
    patch.start = target.start
    patch.end = target.end
  }
  return patch
}

export async function moveBooking(booking: BookingOut, target: MoveTarget): Promise<BookingOut | null> {
  const patch = movePatch(booking, target)
  if (Object.keys(patch).length === 1) return booking
  try {
    const result = await api.bookings.update(booking.id, patch)
    useBookings.getState().upsert(result.booking)
    showWarnings(result.warnings)
    return result.booking
  } catch (error) {
    reportWriteError(error, booking, 'mover a reserva')
    return null
  }
}

export async function changeStatus(booking: BookingOut, status: BookingStatusIn['status']): Promise<boolean> {
  try {
    const updated = await api.bookings.setStatus(booking.id, { version: booking.version, status })
    useBookings.getState().upsert(updated)
    const label = {
      CHECKED_IN: 'Check-in registrado',
      NO_SHOW: 'Marcada como não compareceu',
      CONFIRMED: 'Reserva voltou a confirmada',
    }[status]
    toast.success(label)
    return true
  } catch (error) {
    reportWriteError(error, booking, 'mudar o status')
    return false
  }
}

export async function cancelBooking(booking: BookingOut, reason: string): Promise<boolean> {
  try {
    const updated = await api.bookings.cancel(booking.id, { version: booking.version, reason: reason.trim() || null })
    useBookings.getState().upsert(updated)
    toast.success('Reserva cancelada', {
      description: `${booking.room_name} · ${booking.start}–${booking.end} · ${booking.requester_name}`,
    })
    return true
  } catch (error) {
    reportWriteError(error, booking, 'cancelar a reserva')
    return false
  }
}
