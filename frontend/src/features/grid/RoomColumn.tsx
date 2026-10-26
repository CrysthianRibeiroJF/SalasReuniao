import { memo, type PointerEvent as ReactPointerEvent } from 'react'
import { MoveRight } from 'lucide-react'

import type { BookingOut, LockOut, RoomOut } from '../../api/types'
import { cn } from '../../lib/cn'
import { blockBox, type DayWindow, type MinuteRange, offsetFor, rangeOf, slotCount, slotStart } from '../../lib/grid'
import { firstName, userColor } from '../../lib/people'
import { fromMinutes } from '../../lib/time'
import { BookingBlock } from './BookingBlock'
import { cellKey, type RoomGroupKind } from './gridLayout'

export interface DropPreview {
  range: MinuteRange
  valid: boolean
  message: string
}

export interface RoomColumnProps {
  room: RoomOut
  kind: RoomGroupKind
  window: DayWindow
  rowHeight: number
  bookings: readonly BookingOut[]
  locks: readonly LockOut[]
  /** Minha seleção / prévia do modal nesta sala. */
  selection: MinuteRange | null
  dropPreview: DropPreview | null
  draggingId: string | null
  pendingIds: Readonly<Record<string, unknown>>
  cursorSlot: number | null
  /** Slots que terminam até este minuto ficam esmaecidos (passado). */
  pastUntil: number | null
  nowMinutes: number | null
  highlightId: string | null
  onCellPointerDown: (event: ReactPointerEvent<HTMLDivElement>, roomId: number) => void
  onBookingPointerDown: (event: ReactPointerEvent<HTMLDivElement>, booking: BookingOut) => void
  registerColumn: (roomId: number, element: HTMLDivElement | null) => void
}

export const RoomColumn = memo(function RoomColumn({
  room,
  kind,
  window: w,
  rowHeight,
  bookings,
  locks,
  selection,
  dropPreview,
  draggingId,
  pendingIds,
  cursorSlot,
  pastUntil,
  nowMinutes,
  highlightId,
  onCellPointerDown,
  onBookingPointerDown,
  registerColumn,
}: RoomColumnProps) {
  const rows = slotCount(w)
  // Sala desativada (só aparece em dias com reservas antigas): sem destaque de célula livre, não aceita reservas.
  const hover = !room.is_active
    ? 'hatch-inactive'
    : kind === 'video'
      ? 'hover:bg-video-tint/70'
      : 'hover:bg-normal-tint/70'

  return (
    <div
      ref={(el) => registerColumn(room.id, el)}
      data-room-id={room.id}
      role="gridcell"
      aria-label={room.is_active ? room.name : `${room.name} (sala desativada)`}
      onPointerDown={(event) => onCellPointerDown(event, room.id)}
      className="relative touch-none border-l border-line-strong select-none"
      style={{ height: rows * rowHeight }}
    >
      {Array.from({ length: rows }, (_, i) => {
        const start = slotStart(w, i)
        const end = start + w.slot
        const past = pastUntil !== null && end <= pastUntil
        return (
          <div
            key={i}
            data-cell={cellKey(room.id, i)}
            className={cn(
              'group/cell relative scroll-mt-[86px] scroll-ml-[66px] border-b',
              end % 60 === 0 ? 'border-line' : 'border-line/60',
              past ? 'bg-[#f6f7f9]' : hover,
            )}
            style={{ height: rowHeight }}
          >
            {!past && room.is_active && rowHeight >= 22 && (
              <span className="pointer-events-none invisible absolute top-1 left-1.5 font-mono text-[10px] leading-none text-faint group-hover/cell:visible">
                {fromMinutes(start)}
              </span>
            )}
          </div>
        )
      })}

      {locks.map((lock) => {
        const box = blockBox(rangeOf(lock), w, rowHeight)
        if (!box) return null
        const verb = lock.booking_id ? 'Editando' : 'Reservando'
        return (
          <div
            key={lock.lock_id}
            data-lock-id={lock.lock_id}
            title={`${lock.user.full_name} está ${verb === 'Editando' ? 'editando uma reserva' : 'reservando'} ${lock.start}–${lock.end}`}
            className="hatch-lock absolute right-[3px] left-[3px] z-[5] overflow-hidden rounded-[5px] border border-lock/40 px-1.5 py-[3px]"
            style={{ top: box.top + 1, height: Math.max(box.height - 2, 14) }}
          >
            {box.height >= 40 ? (
              <span className="flex min-w-0 flex-col text-[11px] leading-tight text-lock-ink">
                <span>{verb}:</span>
                <span className="flex min-w-0 items-center gap-1 font-semibold">
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{ backgroundColor: userColor(lock.user.id) }}
                    aria-hidden="true"
                  />
                  <span className="truncate">{lock.user.full_name}</span>
                </span>
              </span>
            ) : (
              <span className="flex min-w-0 items-center gap-1 text-[11px] leading-tight font-medium text-lock-ink">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: userColor(lock.user.id) }}
                  aria-hidden="true"
                />
                <span className="truncate">
                  {verb}: {firstName(lock.user.full_name)}
                </span>
              </span>
            )}
          </div>
        )
      })}

      {bookings.map((booking) => {
        const box = blockBox(rangeOf(booking), w, rowHeight)
        if (!box) return null
        const end = rangeOf(booking).end
        return (
          <BookingBlock
            key={booking.id}
            booking={booking}
            // Números (e não o objeto `box`, novo a cada render) para o memo do bloco funcionar (R35).
            top={box.top}
            height={box.height}
            clippedTop={box.clippedTop}
            clippedBottom={box.clippedBottom}
            kind={kind}
            highlighted={highlightId === booking.id}
            past={pastUntil !== null && end <= pastUntil}
            ghosted={draggingId === booking.id}
            pending={booking.id in pendingIds}
            onPointerDown={onBookingPointerDown}
          />
        )
      })}

      {selection && <SelectionOverlay range={selection} window={w} rowHeight={rowHeight} kind={kind} />}

      {dropPreview && <DropOverlay preview={dropPreview} window={w} rowHeight={rowHeight} kind={kind} />}

      {cursorSlot !== null && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute right-px left-px z-[14] rounded-[3px] outline-2 -outline-offset-2 outline-ink"
          style={{ top: cursorSlot * rowHeight, height: rowHeight }}
        />
      )}

      {nowMinutes !== null && nowMinutes >= w.startMin && nowMinutes <= w.endMin && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute right-0 left-0 z-[15] h-0.5 -translate-y-px bg-now/85"
          style={{ top: offsetFor(nowMinutes, w, rowHeight) }}
        />
      )}
    </div>
  )
})

function SelectionOverlay({
  range,
  window: w,
  rowHeight,
  kind,
}: {
  range: MinuteRange
  window: DayWindow
  rowHeight: number
  kind: RoomGroupKind
}) {
  const box = blockBox(range, w, rowHeight)
  if (!box) return null
  return (
    <div
      data-selection="true"
      aria-hidden="true"
      className={cn(
        'pointer-events-none absolute right-[2px] left-[2px] z-[12] rounded-[5px] border-2',
        kind === 'video' ? 'border-video bg-video/10' : 'border-normal bg-normal/10',
      )}
      style={{ top: box.top, height: box.height }}
    >
      <span
        className={cn(
          'tabular absolute top-0.5 left-1 rounded-[3px] px-1 font-mono text-[10.5px] leading-[15px] font-medium text-white',
          kind === 'video' ? 'bg-video' : 'bg-normal',
        )}
      >
        {fromMinutes(range.start)}–{fromMinutes(range.end)}
      </span>
    </div>
  )
}

function DropOverlay({
  preview,
  window: w,
  rowHeight,
  kind,
}: {
  preview: DropPreview
  window: DayWindow
  rowHeight: number
  kind: RoomGroupKind
}) {
  const box = blockBox(preview.range, w, rowHeight)
  if (!box) return null
  return (
    <div
      aria-hidden="true"
      className={cn(
        'pointer-events-none absolute right-[2px] left-[2px] z-[13] flex flex-col rounded-[5px] border-2 border-dashed px-1.5 py-0.5 shadow-lift',
        preview.valid
          ? kind === 'video'
            ? 'border-video bg-surface/90 text-video-ink'
            : 'border-normal bg-surface/90 text-normal-ink'
          : 'hatch-invalid border-danger text-danger-ink',
      )}
      style={{ top: box.top, height: box.height }}
    >
      <span className="flex items-center gap-1 text-[11px] leading-tight font-semibold">
        {preview.valid && <MoveRight className="size-3 shrink-0" aria-hidden="true" />}
        <span className="truncate">{preview.message}</span>
      </span>
      <span className="tabular font-mono text-[10.5px]">
        {fromMinutes(preview.range.start)}–{fromMinutes(preview.range.end)}
      </span>
    </div>
  )
}
