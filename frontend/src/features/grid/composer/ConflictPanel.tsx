import { useMemo } from 'react'
import { ArrowRightLeft, CheckCircle2, OctagonAlert, Video } from 'lucide-react'

import type { BookingConflictDetail, BookingOut, ISODate, RoomBrief, RoomOut } from '../../../api/types'
import { Button } from '../../../components/ui/Button'
import { type MinuteRange, overlaps, rangeOf } from '../../../lib/grid'
import { fitsAttendees, inRoom, preferFitting } from '../../../lib/rooms'
import { Spinner } from '../../../components/ui/Spinner'
import type { TimeRange } from '../../../lib/periods'
import { useBooking, useDayBookings } from '../../../store/bookings'
import { roomOptionLabel, SeatsSuffix } from './SeatsSuffix'
import { useFreeRooms } from './useFreeRooms'

const MAX_OPTIONS = 6

function preferType<T extends Pick<RoomBrief, 'effective_type'>>(
  rooms: readonly T[],
  type: RoomBrief['effective_type'],
): T[] {
  return [...rooms].sort((a, b) => Number(b.effective_type === type) - Number(a.effective_type === type))
}

/** Salas com reserva ativa (no estado local, mantido pelo tempo real) que cruza `range` no dia. */
function busyRoomIds(bookings: readonly BookingOut[], range: MinuteRange, ignoreIds: readonly string[]): Set<number> {
  const busy = new Set<number>()
  for (const b of bookings) if (!ignoreIds.includes(b.id) && overlaps(rangeOf(b), range)) busy.add(b.room_id)
  return busy
}

/**
 * Conflito (fluxo 5.2): (1) reservar em outra sala livre no intervalo inteiro, ou
 * (2) remanejar a reunião conflitante para uma sala livre no horário dela e tentar de novo.
 *
 * As opções vêm da resposta 409 / da busca de salas livres, mas são conferidas com as reservas que
 * chegam pelo tempo real: sala que acabou de ser ocupada some, e reunião que outra pessoa já moveu ou
 * cancelou aparece como resolvida (R27). As salas que comportam os participantes vêm primeiro e cada
 * botão mostra a capacidade (R12).
 */
export function ConflictPanel({
  conflict,
  wholeDay,
  requiresVideo,
  attendees,
  currentRoomId,
  date,
  range,
  ownBookingId,
  busyKey,
  onUseRoom,
  onRelocate,
}: {
  conflict: Pick<BookingConflictDetail, 'message' | 'conflicts' | 'alternatives'>
  wholeDay: boolean
  requiresVideo: boolean
  /** Participantes da reserva sendo criada/editada (null = não informado). */
  attendees: number | null
  currentRoomId: number
  date: ISODate
  range: TimeRange
  /** Reserva em edição (não conta como ocupando a sala). */
  ownBookingId: string | null
  busyKey: string | null
  onUseRoom: (room: RoomBrief) => void
  onRelocate: (booking: BookingOut, room: RoomOut) => void
}) {
  const dayBookings = useDayBookings(date)
  const wanted = useMemo(() => rangeOf({ start: range.start, end: range.end }), [range.start, range.end])
  const busy = useMemo(
    () => busyRoomIds(dayBookings, wanted, ownBookingId ? [ownBookingId] : []),
    [dayBookings, wanted, ownBookingId],
  )
  const alternatives = preferFitting(
    preferType(
      conflict.alternatives.filter((r) => r.id !== currentRoomId && !busy.has(r.id)),
      requiresVideo ? 'VIDEO' : 'NORMAL',
    ),
    attendees,
  ).slice(0, MAX_OPTIONS)

  return (
    <section
      role="alert"
      aria-label="Conflito de horário"
      className="rounded-lg border border-danger/30 bg-danger-tint/60 p-3 text-[12.5px]"
    >
      <p className="flex items-start gap-2 font-semibold text-danger-ink">
        <OctagonAlert className="mt-px size-4 shrink-0" aria-hidden="true" />
        {conflict.message || 'Já existe uma reserva neste horário.'}
      </p>

      <div className="mt-3 space-y-3">
        <div>
          <p className="flex items-center gap-1.5 font-medium text-ink">
            <span className="inline-flex size-4 items-center justify-center rounded-full bg-ink font-mono text-[10px] text-white">
              1
            </span>
            Escolher outra sala livre {wholeDay ? 'o dia todo' : 'neste horário'}
          </p>
          {alternatives.length > 0 ? (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {alternatives.map((room) => (
                <Button
                  key={room.id}
                  size="xs"
                  variant={room.effective_type === 'VIDEO' ? 'video' : 'normal'}
                  loading={busyKey === `use-${room.id}`}
                  disabled={busyKey !== null}
                  icon={room.effective_type === 'VIDEO' ? <Video className="size-3" aria-hidden="true" /> : undefined}
                  title={fitsAttendees(room, attendees) ? undefined : `Pequena para ${attendees} participantes`}
                  aria-label={roomOptionLabel(`Reservar ${inRoom(room.name)}`, room.capacity)}
                  onClick={() => onUseRoom(room)}
                >
                  Reservar {inRoom(room.name)}
                  <SeatsSuffix capacity={room.capacity} tooSmall={!fitsAttendees(room, attendees)} />
                </Button>
              ))}
            </div>
          ) : (
            <p className="mt-1 text-muted">Nenhuma sala está livre no intervalo inteiro.</p>
          )}
        </div>

        {conflict.conflicts.length > 0 && (
          <div>
            <p className="flex items-center gap-1.5 font-medium text-ink">
              <span className="inline-flex size-4 items-center justify-center rounded-full bg-ink font-mono text-[10px] text-white">
                2
              </span>
              Remanejar {conflict.conflicts.length === 1 ? 'a reunião que está' : 'as reuniões que estão'} no caminho
            </p>
            <ul className="mt-1.5 space-y-2">
              {conflict.conflicts.map((booking) => (
                <ConflictRow
                  key={booking.id}
                  booking={booking}
                  currentRoomId={currentRoomId}
                  date={date}
                  wanted={wanted}
                  dayBookings={dayBookings}
                  busyKey={busyKey}
                  onRelocate={onRelocate}
                />
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}

function ConflictRow({
  booking: snapshot,
  currentRoomId,
  date,
  wanted,
  dayBookings,
  busyKey,
  onRelocate,
}: {
  booking: BookingOut
  currentRoomId: number
  date: ISODate
  wanted: MinuteRange
  dayBookings: readonly BookingOut[]
  busyKey: string | null
  onRelocate: (booking: BookingOut, room: RoomOut) => void
}) {
  // Versão mais nova que chegou pelo tempo real (o PATCH precisa da `version` atual).
  const live = useBooking(snapshot.id)
  const booking = live && live.version >= snapshot.version ? live : snapshot
  const resolved =
    booking.status === 'CANCELLED' ||
    booking.date !== date ||
    booking.room_id !== currentRoomId ||
    !overlaps(rangeOf(booking), wanted)

  const { rooms, loading } = useFreeRooms(
    resolved ? null : { date: booking.date, start: booking.start, end: booking.end, exclude_booking_id: booking.id },
    currentRoomId,
  )
  const ownRange = rangeOf(booking)
  const taken = busyRoomIds(dayBookings, ownRange, [booking.id])
  const options = preferFitting(
    preferType(rooms, booking.requires_video ? 'VIDEO' : 'NORMAL').filter(
      (r) => r.id !== booking.room_id && !taken.has(r.id),
    ),
    booking.attendees,
  ).slice(0, MAX_OPTIONS)

  return (
    <li className="rounded-md border border-line bg-surface px-2.5 py-2">
      <p className="flex min-w-0 items-center gap-1.5 text-ink">
        {booking.ticket_number && <span className="font-mono font-medium text-ink-2">#{booking.ticket_number}</span>}
        <span className="truncate font-medium">{booking.requester_name}</span>
        <span className="tabular ml-auto shrink-0 font-mono text-[11.5px] text-muted">
          {snapshot.start}–{snapshot.end}
        </span>
      </p>
      {(booking.requires_video || booking.attendees) && !resolved && (
        <p className="text-[11.5px] text-muted">
          {[
            booking.requires_video ? 'Precisa de videoconferência' : null,
            booking.attendees ? `${booking.attendees} participantes` : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {resolved ? (
          <span className="flex items-center gap-1.5 text-success">
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
            {booking.status === 'CANCELLED'
              ? 'Cancelada por outra pessoa. Tente salvar de novo.'
              : 'Já saiu do caminho (alterada por outra pessoa). Tente salvar de novo.'}
          </span>
        ) : loading ? (
          <span className="flex items-center gap-1.5 text-muted">
            <Spinner className="size-3" /> Procurando salas livres…
          </span>
        ) : options.length > 0 ? (
          options.map((room) => (
            <Button
              key={room.id}
              size="xs"
              variant="secondary"
              loading={busyKey === `move-${booking.id}-${room.id}`}
              disabled={busyKey !== null}
              icon={<ArrowRightLeft className="size-3" aria-hidden="true" />}
              title={
                fitsAttendees(room, booking.attendees) ? undefined : `Pequena para ${booking.attendees} participantes`
              }
              aria-label={roomOptionLabel(`Mover para ${room.name}`, room.capacity)}
              onClick={() => onRelocate(booking, room)}
            >
              Mover para {room.name}
              <SeatsSuffix capacity={room.capacity} tooSmall={!fitsAttendees(room, booking.attendees)} />
            </Button>
          ))
        ) : (
          <span className="text-muted">Nenhuma sala livre nesse horário.</span>
        )}
      </div>
    </li>
  )
}
