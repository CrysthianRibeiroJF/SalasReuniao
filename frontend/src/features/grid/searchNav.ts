/** Regras puras da busca rápida: navegação por teclado na lista e destino ao escolher um resultado. */
import type { BookingOut, ISODate, RoomOut } from '../../api/types'
import type { RoomFilter } from '../../lib/rooms'

/** Próximo índice ativo com ↑/↓ (circular); -1 quando a lista está vazia. */
export function moveActiveIndex(current: number, delta: number, length: number): number {
  if (length <= 0) return -1
  if (current < 0) return delta > 0 ? 0 : length - 1
  return (current + delta + length) % length
}

/** Índice do primeiro resultado anterior a hoje (o backend lista hoje/futuras e depois passadas). */
export function firstPastIndex(results: readonly BookingOut[], today: ISODate): number {
  return results.findIndex((b) => b.date < today)
}

export interface SearchNavigation {
  date: ISODate
  highlightId: string
  /** O filtro atual esconderia a sala da reserva: volta para "Todas as Salas". */
  resetFilter: boolean
}

export function planSearchNavigation(
  booking: BookingOut,
  filter: RoomFilter,
  room: Pick<RoomOut, 'effective_type'> | undefined,
): SearchNavigation {
  let resetFilter = false
  if (filter === 'free') resetFilter = true
  else if (filter === 'video') resetFilter = room?.effective_type !== 'VIDEO'
  else if (filter === 'normal') resetFilter = room?.effective_type !== 'NORMAL'
  return { date: booking.date, highlightId: booking.id, resetFilter }
}
