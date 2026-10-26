/**
 * Estado normalizado das reservas (id → reserva) e regras puras de aplicação de eventos.
 *
 * - Idempotente e seguro quanto à ordem: um evento com `version` menor ou igual à conhecida é ignorado.
 * - A visão por dia é derivada de `booking.date`; uma reserva que muda de data sai do dia antigo.
 * - Canceladas ficam como "lápide" em byId (para barrar eventos atrasados), mas não aparecem na grade.
 * - `seq` é um relógio lógico monotônico (recebimento × início de busca) usado ao mesclar buscas.
 */
import type { BookingOut, ISODate, RoomOut } from '../api/types'

export type LoadStatus = 'loading' | 'loaded' | 'error'

export interface DateLoad {
  status: LoadStatus
  /** seq da busca mais recente pedida para este dia. */
  requestSeq: number
  /** Já carregou ao menos uma vez (contagem conhecida mesmo durante uma recarga). */
  known: boolean
}

export interface BookingsData {
  byId: Record<string, BookingOut>
  /** seq em que cada reserva foi aceita pela última vez. */
  receivedSeq: Record<string, number>
  dates: Record<ISODate, DateLoad>
}

export const EMPTY_BOOKINGS: BookingsData = { byId: {}, receivedSeq: {}, dates: {} }

export function isActive(booking: Pick<BookingOut, 'status'>): boolean {
  return booking.status !== 'CANCELLED'
}

/** Aplica uma reserva vinda de evento ou de resposta de escrita. Versões antigas são ignoradas. */
export function upsertBooking(data: BookingsData, booking: BookingOut, seq: number): BookingsData {
  const existing = data.byId[booking.id]
  if (existing && booking.version <= existing.version) return data
  return {
    ...data,
    byId: { ...data.byId, [booking.id]: booking },
    receivedSeq: { ...data.receivedSeq, [booking.id]: seq },
  }
}

export function markLoading(data: BookingsData, dates: readonly ISODate[], seq: number): BookingsData {
  const next = { ...data.dates }
  for (const date of dates) next[date] = { status: 'loading', requestSeq: seq, known: data.dates[date]?.known ?? false }
  return { ...data, dates: next }
}

export function markError(data: BookingsData, dates: readonly ISODate[], requestSeq: number): BookingsData {
  const next = { ...data.dates }
  let changed = false
  for (const date of dates) {
    const current = next[date]
    if (current && current.requestSeq === requestSeq) {
      next[date] = { ...current, status: 'error' }
      changed = true
    }
  }
  return changed ? { ...data, dates: next } : data
}

/**
 * Mescla o resultado de uma busca dos dias `dates` iniciada em `requestSeq`.
 *
 * Só os dias cuja busca mais recente é esta são marcados como carregados; neles, reservas que não
 * vieram na resposta e não foram recebidas depois do início da busca saíram do dia (cancelamento ou
 * mudança perdidos, ex.: durante uma queda do WebSocket) e são descartadas. Reservas recebidas por
 * evento durante a busca são preservadas, e a resposta nunca rebaixa uma versão mais nova.
 */
export function applyFetchedDates(
  data: BookingsData,
  dates: readonly ISODate[],
  bookings: readonly BookingOut[],
  requestSeq: number,
  seq: number,
): BookingsData {
  const byId = { ...data.byId }
  const receivedSeq = { ...data.receivedSeq }
  const fetched = new Set<string>()
  for (const booking of bookings) {
    fetched.add(booking.id)
    const existing = byId[booking.id]
    // Mesma versão: a resposta é a visão mais fresca (ex.: selos recalculados após manutenção).
    if (!existing || booking.version >= existing.version) {
      byId[booking.id] = booking
      receivedSeq[booking.id] = seq
    }
  }

  const current = new Set(dates.filter((d) => data.dates[d]?.requestSeq === requestSeq))
  if (current.size > 0) {
    for (const [id, booking] of Object.entries(byId)) {
      if (current.has(booking.date) && !fetched.has(id) && (receivedSeq[id] ?? 0) < requestSeq) {
        delete byId[id]
        delete receivedSeq[id]
      }
    }
  }

  const nextDates = { ...data.dates }
  for (const date of dates) {
    if (current.has(date)) nextDates[date] = { status: 'loaded', requestSeq, known: true }
    // Busca superada por outra mais nova: o dia continua carregando, mas a contagem já é conhecida.
    else if (nextDates[date]) nextDates[date] = { ...nextDates[date], known: true }
  }
  return { byId, receivedSeq, dates: nextDates }
}

/**
 * Sala mudou (manutenção de vídeo, renomeação): recalcula localmente nome e selos das reservas dela,
 * como o backend faria (serializers.booking_out), sem mexer na versão.
 */
export function applyRoomToBookings(data: BookingsData, room: RoomOut): BookingsData {
  const roomIsVideo = room.effective_type === 'VIDEO'
  let byId: Record<string, BookingOut> | null = null
  for (const booking of Object.values(data.byId)) {
    if (booking.room_id !== room.id) continue
    const active = isActive(booking)
    const isRelocatable = active && !booking.requires_video && roomIsVideo
    const videoUnavailable = active && booking.requires_video && !roomIsVideo
    if (
      booking.room_name === room.name &&
      booking.is_relocatable === isRelocatable &&
      booking.video_unavailable === videoUnavailable
    ) {
      continue
    }
    byId ??= { ...data.byId }
    byId[booking.id] = {
      ...booking,
      room_name: room.name,
      is_relocatable: isRelocatable,
      video_unavailable: videoUnavailable,
    }
  }
  return byId ? { ...data, byId } : data
}

function byStartThenRoom(a: BookingOut, b: BookingOut): number {
  if (a.start !== b.start) return a.start < b.start ? -1 : 1
  return a.room_id - b.room_id
}

/** Reservas ativas de um dia, ordenadas por início e sala. */
export function bookingsForDate(byId: BookingsData['byId'], date: ISODate): BookingOut[] {
  return Object.values(byId)
    .filter((b) => b.date === date && isActive(b))
    .sort(byStartThenRoom)
}

/** Quantidade de reservas ativas no dia, ou null se o dia ainda não foi carregado. */
export function countForDate(data: BookingsData, date: ISODate): number | null {
  if (!data.dates[date]?.known) return null
  let count = 0
  for (const booking of Object.values(data.byId)) if (booking.date === date && isActive(booking)) count++
  return count
}

export function loadedDates(data: BookingsData): ISODate[] {
  return Object.entries(data.dates)
    .filter(([, load]) => load.known || load.status === 'loading')
    .map(([date]) => date)
}
