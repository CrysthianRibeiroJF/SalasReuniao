import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'

import { api } from '../api/endpoints'
import type { BookingOut, ISODate, RoomOut } from '../api/types'
import { chunkDateRanges, weekDates } from '../lib/time'
import {
  applyFetchedDates,
  applyRoomToBookings,
  type BookingsData,
  bookingsForDate,
  countForDate,
  EMPTY_BOOKINGS,
  loadedDates,
  markError,
  markLoading,
  upsertBooking,
} from './bookingsModel'

let seqCounter = 0
/** Relógio lógico compartilhado por recebimentos e buscas (ver bookingsModel). */
function nextSeq(): number {
  return ++seqCounter
}

interface BookingsState {
  data: BookingsData
  /** Busca os dias informados (em faixas contíguas de até 31 dias). */
  loadDates: (dates: readonly ISODate[]) => Promise<void>
  /** Semana inteira (seg–dom) da data: alimenta a grade e as contagens das abas. */
  loadWeek: (date: ISODate) => Promise<void>
  /** Recarrega os dias já carregados (todos ou só os informados). */
  refetchLoaded: (onlyDates?: readonly ISODate[]) => Promise<void>
  upsert: (booking: BookingOut) => void
  applyRoom: (room: RoomOut) => void
  reset: () => void
}

export const useBookings = create<BookingsState>()((set, get) => ({
  data: EMPTY_BOOKINGS,

  loadDates: async (dates) => {
    const ranges = chunkDateRanges(dates)
    await Promise.all(
      ranges.map(async ([start, end]) => {
        const rangeDates = dates.filter((d) => d >= start && d <= end)
        const requestSeq = nextSeq()
        set((s) => ({ data: markLoading(s.data, rangeDates, requestSeq) }))
        try {
          const list = start === end ? await api.bookings.listDay(start) : await api.bookings.listRange(start, end)
          set((s) => ({ data: applyFetchedDates(s.data, rangeDates, list, requestSeq, nextSeq()) }))
        } catch {
          set((s) => ({ data: markError(s.data, rangeDates, requestSeq) }))
        }
      }),
    )
  },

  loadWeek: (date) => get().loadDates(weekDates(date)),

  refetchLoaded: async (onlyDates) => {
    const loaded = loadedDates(get().data)
    const target = onlyDates ? loaded.filter((d) => onlyDates.includes(d)) : loaded
    if (target.length > 0) await get().loadDates(target)
  },

  upsert: (booking) => set((s) => ({ data: upsertBooking(s.data, booking, nextSeq()) })),

  applyRoom: (room) =>
    set((s) => {
      const data = applyRoomToBookings(s.data, room)
      return data === s.data ? s : { data }
    }),

  reset: () => set({ data: EMPTY_BOOKINGS }),
}))

/**
 * Reservas ativas do dia. A comparação rasa devolve o mesmo array enquanto as reservas do dia forem
 * as mesmas: um evento de outro dia/semana não re-renderiza a grade inteira (R35).
 */
export function useDayBookings(date: ISODate): BookingOut[] {
  return useBookings(useShallow((s) => bookingsForDate(s.data.byId, date)))
}

/** Contagem por dia (abas); só re-renderiza quando algum número muda. */
export function useDateCounts(dates: readonly ISODate[]): (number | null)[] {
  return useBookings(useShallow((s) => dates.map((d) => countForDate(s.data, d))))
}

export function useDateStatus(date: ISODate): BookingsData['dates'][string] | undefined {
  return useBookings((s) => s.data.dates[date])
}

export function useBooking(id: string | null | undefined): BookingOut | undefined {
  return useBookings((s) => (id ? s.data.byId[id] : undefined))
}
