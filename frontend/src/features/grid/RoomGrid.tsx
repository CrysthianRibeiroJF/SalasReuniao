import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { AlertTriangle, CalendarX2, DoorClosed, Filter, RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router'

import type { BookingOut, ISODate, LockOut, RoomOut } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { EmptyState } from '../../components/ui/EmptyState'
import { Spinner } from '../../components/ui/Spinner'
import { useElementSize } from '../../hooks/useElementSize'
import { useLatest } from '../../hooks/useLatest'
import { useNow } from '../../hooks/useNow'
import {
  dayWindow,
  extendSelection,
  fitRowHeight,
  isSlotFree,
  type MinuteRange,
  offsetFor,
  overlaps,
  rangeFromSlots,
  rangeOf,
  slotCount,
  slotIndexAt,
  slotIndexFromOffset,
} from '../../lib/grid'
import { filterRooms, gridRoomsForDay, groupRooms, ROOM_FILTERS } from '../../lib/rooms'
import { formatTabLabel, fromMinutes, zonedParts } from '../../lib/time'
import { othersLocksForDate } from '../../store/locksModel'
import { useAuth, useIsAdmin } from '../../store/auth'
import { useBookings, useDayBookings, useDateStatus } from '../../store/bookings'
import { useLoadedConfig } from '../../store/config'
import { useLocks } from '../../store/locks'
import { useRealtime } from '../../store/realtime'
import { useRooms } from '../../store/rooms'
import { toast } from '../../store/toasts'
import { type AnchorRect, useUi } from '../../store/ui'
import { beginBooking } from './beginBooking'
import { moveBooking } from './bookingActions'
import { GroupHeader, RoomHeader } from './GridHeader'
import {
  cellKey,
  GROUP_HEADER_HEIGHT,
  HEADER_HEIGHT,
  MAX_ROW_HEIGHT,
  MIN_ROOM_WIDTH,
  MIN_ROW_HEIGHT,
  type RoomGroupKind,
  TIME_COL_WIDTH,
} from './gridLayout'
import { MaintenanceDialog } from './MaintenanceDialog'
import { RoomColumn } from './RoomColumn'
import { TimeColumn } from './TimeColumn'
import { useBookingDrag } from './useBookingDrag'
import { useGridKeyboard } from './useGridKeyboard'

const EMPTY_BOOKINGS: readonly BookingOut[] = []
/** Duração do destaque da reserva encontrada pela busca. */
export const HIGHLIGHT_MS = 2_800
const EMPTY_LOCKS: readonly LockOut[] = []

function groupBy<T>(items: readonly T[], key: (item: T) => number): Map<number, T[]> {
  const map = new Map<number, T[]>()
  for (const item of items) {
    const k = key(item)
    const list = map.get(k)
    if (list) list.push(item)
    else map.set(k, [item])
  }
  return map
}

function sameItems<T>(a: readonly T[], b: readonly T[]): boolean {
  return a.length === b.length && a.every((item, i) => item === b[i])
}

/**
 * Agrupa reaproveitando os arrays do agrupamento anterior quando o conteúdo de um grupo não mudou:
 * as colunas (memo) das salas que não mudaram não re-renderizam a cada evento (R35).
 */
export function groupByStable<T>(
  items: readonly T[],
  key: (item: T) => number,
  previous: ReadonlyMap<number, T[]>,
): Map<number, T[]> {
  const next = groupBy(items, key)
  for (const [k, list] of next) {
    const before = previous.get(k)
    if (before && sameItems(before, list)) next.set(k, before)
  }
  return next
}

/** Agrupamento estável entre renderizações (ver groupByStable). */
function useStableGroups<T>(items: readonly T[], key: (item: T) => number): Map<number, T[]> {
  const previous = useRef<Map<number, T[]>>(new Map())
  return useMemo(() => {
    const next = groupByStable(items, key, previous.current)
    previous.current = next
    return next
    // `key` é uma função fixa (definida fora do componente)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items])
}

const byRoom = (item: { room_id: number }) => item.room_id

/** Texto lido pelo leitor de tela quando o cursor do teclado se move (R42). */
export function describeCursor(
  room: RoomOut,
  start: string,
  end: string,
  booking: BookingOut | undefined,
  lock: LockOut | undefined,
): string {
  const where = `${room.name}${room.is_active ? '' : ' (sala desativada)'}, ${start} às ${end}`
  if (booking) {
    const ticket = booking.ticket_number ? `, chamado ${booking.ticket_number}` : ''
    return `${where}: ocupada por ${booking.requester_name}${ticket}, ${booking.start} às ${booking.end}`
  }
  if (lock) return `${where}: ${lock.user.full_name} está reservando`
  return `${where}: livre`
}

function rectToAnchor(rect: DOMRect): AnchorRect {
  return { top: rect.top, left: rect.left, width: rect.width, height: rect.height }
}

export function RoomGrid({ date }: { date: ISODate }) {
  const config = useLoadedConfig()
  const w = useMemo(() => dayWindow(config), [config])
  const rows = slotCount(w)
  const myId = useAuth((s) => s.user?.id ?? null)
  const isAdmin = useIsAdmin()
  const navigate = useNavigate()

  const roomRecords = useRooms((s) => s.byId)
  const roomsStatus = useRooms((s) => s.status)
  const roomsError = useRooms((s) => s.error)
  const dayBookings = useDayBookings(date)
  const dateStatus = useDateStatus(date)
  const lockData = useLocks((s) => s.data)
  const filter = useUi((s) => s.filter)
  const selection = useUi((s) => s.selection)
  const composer = useUi((s) => s.composer)
  const cursor = useUi((s) => s.cursor)
  const highlightId = useUi((s) => s.highlightId)
  const pendingMoves = useUi((s) => s.pendingMoves)
  const skew = useRealtime((s) => s.clockSkewMs)

  // ------------------------------------------------------------ dados derivados

  const busyRoomIds = useMemo(() => new Set(dayBookings.map((b) => b.room_id)), [dayBookings])
  // Salas ativas + as desativadas que ainda têm reservas neste dia (coluna "Sala desativada", D1).
  const allRooms = useMemo(() => gridRoomsForDay(Object.values(roomRecords), busyRoomIds), [roomRecords, busyRoomIds])
  const rooms = useMemo(() => {
    const { video, normal } = groupRooms(filterRooms(allRooms, filter, busyRoomIds))
    return { video, normal, ordered: [...video, ...normal] }
  }, [allRooms, filter, busyRoomIds])
  const kindOf = useCallback(
    (room: RoomOut): RoomGroupKind => (room.effective_type === 'VIDEO' ? 'video' : 'normal'),
    [],
  )

  const displayBookings = useMemo(
    () =>
      dayBookings.map((b) => {
        const move = pendingMoves[b.id]
        return move ? { ...b, room_id: move.roomId, start: move.start, end: move.end } : b
      }),
    [dayBookings, pendingMoves],
  )
  const bookingsByRoom = useStableGroups(displayBookings, byRoom)
  const locks = useMemo(() => othersLocksForDate(lockData, date, myId), [lockData, date, myId])
  const locksByRoom = useStableGroups(locks, byRoom)

  const now = useNow(30_000)
  const zoned = zonedParts(now + skew, config.timezone)
  const isToday = zoned.date === date
  const nowMinutes = isToday ? zoned.minutes : null
  const pastUntil = date < zoned.date ? w.endMin : isToday ? zoned.minutes : null

  // ------------------------------------------------------------ geometria

  const scrollRef = useRef<HTMLDivElement>(null)
  const columnsRef = useRef(new Map<number, HTMLDivElement>())
  const [measureRef, { height: viewportHeight }] = useElementSize<HTMLDivElement>()
  const setScrollEl = useCallback(
    (el: HTMLDivElement | null) => {
      scrollRef.current = el
      measureRef(el)
    },
    [measureRef],
  )
  const rowHeight = fitRowHeight(viewportHeight - HEADER_HEIGHT - 2, rows, MIN_ROW_HEIGHT, MAX_ROW_HEIGHT)

  const registerColumn = useCallback((roomId: number, el: HTMLDivElement | null) => {
    if (el) columnsRef.current.set(roomId, el)
    else columnsRef.current.delete(roomId)
  }, [])

  const inactiveIds = useMemo(() => new Set(allRooms.filter((r) => !r.is_active).map((r) => r.id)), [allRooms])

  // Sala desativada não aceita reservas novas: o dia inteiro conta como ocupado (só as reservas que
  // restam nela podem ser abertas, movidas para outra sala ou canceladas).
  const occupiedFor = useCallback(
    (roomId: number): MinuteRange[] =>
      inactiveIds.has(roomId)
        ? [{ start: w.startMin, end: w.endMin }]
        : [...(bookingsByRoom.get(roomId) ?? []).map(rangeOf), ...(locksByRoom.get(roomId) ?? []).map(rangeOf)],
    [bookingsByRoom, locksByRoom, inactiveIds, w],
  )

  /** Slot bloqueado: avisa quem está reservando, ou que a sala está desativada (clique ou Enter). */
  const notifyLocked = useCallback(
    (roomId: number, slot: number) => {
      if (inactiveIds.has(roomId)) {
        toast.info('Sala desativada: não aceita reservas novas.', {
          description: 'As reservas que restam nela podem ser movidas para outra sala ou canceladas.',
        })
        return
      }
      const lock = (locksByRoom.get(roomId) ?? []).find((l) => overlaps(rangeOf(l), rangeFromSlots(w, slot, slot)))
      if (lock) toast.info(`${lock.user.full_name} está reservando este horário agora.`)
    },
    [locksByRoom, inactiveIds, w],
  )

  const anchorFor = useCallback(
    (roomId: number, range: MinuteRange): AnchorRect | null => {
      const col = columnsRef.current.get(roomId)
      if (!col) return null
      const rect = col.getBoundingClientRect()
      const top = rect.top + offsetFor(Math.max(range.start, w.startMin), w, rowHeight)
      const height =
        offsetFor(Math.min(range.end, w.endMin), w, rowHeight) -
        offsetFor(Math.max(range.start, w.startMin), w, rowHeight)
      const scroller = scrollRef.current?.getBoundingClientRect()
      // Mantém a âncora dentro da área visível da grade (a seleção pode ser maior que a tela).
      const visibleTop = scroller ? Math.max(top, scroller.top + HEADER_HEIGHT) : top
      return {
        top: visibleTop,
        left: rect.left,
        width: rect.width,
        height: Math.max(rowHeight, height - (visibleTop - top)),
      }
    },
    [w, rowHeight],
  )

  // ------------------------------------------------------------ seleção por clique/arrasto

  const selectDrag = useRef<{ roomId: number; anchor: number; pointerId: number } | null>(null)
  const latest = useLatest({ occupiedFor, date, w, rowHeight, anchorFor, notifyLocked })

  const onCellPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>, roomId: number) => {
      if (event.button !== 0) return
      const target = event.target as HTMLElement
      if (target.closest('[data-booking-id], a, button')) return
      const p = latest.current
      const rect = event.currentTarget.getBoundingClientRect()
      const slot = slotIndexFromOffset(event.clientY - rect.top, p.w, p.rowHeight)
      const ui = useUi.getState()
      ui.setCursor({ roomId, slot })
      const occupied = p.occupiedFor(roomId)
      if (!isSlotFree(p.w, slot, occupied)) {
        p.notifyLocked(roomId, slot)
        return
      }
      event.preventDefault()
      selectDrag.current = { roomId, anchor: slot, pointerId: event.pointerId }
      ui.setSelection({ roomId, date: p.date, anchor: slot, focus: slot })
    },
    [latest],
  )

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = selectDrag.current
      if (!drag || drag.pointerId !== event.pointerId) return
      const col = columnsRef.current.get(drag.roomId)
      const p = latest.current
      if (!col) return
      const slot = slotIndexFromOffset(event.clientY - col.getBoundingClientRect().top, p.w, p.rowHeight)
      const [, focus] = extendSelection(p.w, drag.anchor, slot, p.occupiedFor(drag.roomId))
      const ui = useUi.getState()
      if (ui.selection?.focus !== focus) {
        ui.setSelection({ roomId: drag.roomId, date: p.date, anchor: drag.anchor, focus })
        ui.setCursor({ roomId: drag.roomId, slot: focus })
      }
    }
    const onUp = (event: PointerEvent) => {
      const drag = selectDrag.current
      if (!drag || drag.pointerId !== event.pointerId) return
      selectDrag.current = null
      const sel = useUi.getState().selection
      if (!sel || sel.roomId !== drag.roomId) return
      const p = latest.current
      const range = rangeFromSlots(p.w, sel.anchor, sel.focus)
      void beginBooking({
        roomId: sel.roomId,
        date: p.date,
        start: fromMinutes(range.start),
        end: fromMinutes(range.end),
        anchor: p.anchorFor(sel.roomId, range),
      })
    }
    const onCancel = (event: PointerEvent) => {
      if (selectDrag.current?.pointerId === event.pointerId) {
        selectDrag.current = null
        useUi.getState().setSelection(null)
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
    }
  }, [latest])

  // ------------------------------------------------------------ arrastar reservas

  const roomsById = useMemo(() => new Map(allRooms.map((r) => [r.id, r])), [allRooms])
  const { preview: drag, onBookingPointerDown } = useBookingDrag({
    scrollRef,
    columnsRef,
    window: w,
    rowHeight,
    checkDrop: (booking, roomId, range) => {
      if (inactiveIds.has(roomId)) {
        const original = rangeOf(booking)
        if (roomId === booking.room_id && original.start === range.start && original.end === range.end) {
          return { valid: true, message: 'Sem mudança' }
        }
        return {
          valid: false,
          message: 'Sala desativada',
          error: 'Esta sala está desativada e não aceita reservas: mova a reserva para uma sala ativa.',
        }
      }
      const lock = (locksByRoom.get(roomId) ?? []).find((l) => overlaps(rangeOf(l), range))
      if (lock) {
        return {
          valid: false,
          message: `Reservando: ${lock.user.full_name}`,
          error: `${lock.user.full_name} está reservando este horário agora.`,
        }
      }
      const clash = (bookingsByRoom.get(roomId) ?? []).find((b) => b.id !== booking.id && overlaps(rangeOf(b), range))
      if (clash) return { valid: false, message: 'Horário ocupado', error: 'Horário ocupado nesta sala' }
      const room = roomsById.get(roomId)
      return { valid: true, message: room && roomId !== booking.room_id ? room.name : 'Novo horário' }
    },
    onClick: (booking, anchor) => {
      useUi.getState().setCursor(null)
      useUi.getState().openPopover({ bookingId: booking.id, anchor })
    },
    onDrop: (booking, roomId, range, check) => {
      if (!check.valid) {
        toast.error(check.error ?? check.message, { description: 'A reserva voltou para o lugar original.' })
        return
      }
      const target = { roomId, start: fromMinutes(range.start), end: fromMinutes(range.end) }
      const ui = useUi.getState()
      ui.setPendingMove(booking.id, target)
      void moveBooking(booking, target).then((updated) => {
        useUi.getState().setPendingMove(booking.id, null)
        if (updated) {
          const room = roomsById.get(roomId)
          toast.success('Reserva remanejada', {
            description: `${room?.name ?? updated.room_name} · ${formatTabLabel(updated.date)} · ${updated.start}–${updated.end}`,
          })
        }
      })
    },
  })

  // ------------------------------------------------------------ teclado

  const revealCell = useCallback((roomId: number, slot: number) => {
    requestAnimationFrame(() => {
      const cell = scrollRef.current?.querySelector(`[data-cell="${cellKey(roomId, slot)}"]`)
      cell?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    })
  }, [])

  useGridKeyboard({
    enabled: drag === null,
    rooms: rooms.ordered,
    date,
    window: w,
    occupiedFor: (roomId) => occupiedFor(roomId),
    bookingAt: (roomId, slot) =>
      (bookingsByRoom.get(roomId) ?? []).find((b) => overlaps(rangeOf(b), rangeFromSlots(w, slot, slot))),
    anchorFor,
    notifyBlocked: notifyLocked,
    openBooking: (booking) => {
      const el = scrollRef.current?.querySelector(`[data-booking-id="${booking.id}"]`)
      if (el) useUi.getState().openPopover({ bookingId: booking.id, anchor: rectToAnchor(el.getBoundingClientRect()) })
    },
    initialSlot: () => (nowMinutes !== null ? slotIndexAt(w, nowMinutes) : 0),
    revealCell,
  })

  // ------------------------------------------------------------ rolagem: hora atual e destaque da busca

  const scrolledFor = useRef<string | null>(null)
  useLayoutEffect(() => {
    const el = scrollRef.current
    // Só depois de medir a área visível (a altura da linha depende dela).
    if (!el || viewportHeight === 0 || scrolledFor.current === date) return
    scrolledFor.current = date
    if (nowMinutes !== null) {
      const y = offsetFor(nowMinutes, w, rowHeight)
      el.scrollTop = Math.max(0, y - (el.clientHeight - HEADER_HEIGHT) / 3)
    } else {
      el.scrollTop = 0
    }
  }, [date, nowMinutes, rowHeight, viewportHeight, w])

  const highlightedFor = useRef<string | null>(null)
  // O temporizador não fica preso ao ciclo do efeito: qualquer evento do tempo real muda
  // `displayBookings`, e cancelar o timer aí deixaria o destaque aceso para sempre.
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => {
    if (!highlightId) {
      highlightedFor.current = null
      return
    }
    if (highlightedFor.current === highlightId) return
    const el = scrollRef.current?.querySelector(`[data-booking-id="${highlightId}"]`)
    if (!el) return
    highlightedFor.current = highlightId
    el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
    clearTimeout(highlightTimer.current)
    highlightTimer.current = setTimeout(() => {
      if (useUi.getState().highlightId === highlightId) useUi.getState().highlight(null)
    }, HIGHLIGHT_MS)
  }, [highlightId, displayBookings])
  useEffect(() => () => clearTimeout(highlightTimer.current), [])

  // ------------------------------------------------------------ manutenção (cabeçalho)

  const [maintenanceRoom, setMaintenanceRoom] = useState<RoomOut | null>(null)

  // ------------------------------------------------------------ render

  const selected = useMemo((): { roomId: number; range: MinuteRange } | null => {
    if (composer) return composer.date === date ? { roomId: composer.roomId, range: rangeOf(composer) } : null
    if (selection && selection.date === date) {
      return { roomId: selection.roomId, range: rangeFromSlots(w, selection.anchor, selection.focus) }
    }
    return null
  }, [composer, selection, date, w])

  // Leitor de tela: o cursor do teclado é só visual; esta região anuncia sala, horário e situação (R42).
  const cursorRoom = cursor && !composer ? rooms.ordered.find((r) => r.id === cursor.roomId) : undefined
  let announcement = ''
  if (cursor && cursorRoom) {
    const slot = rangeFromSlots(w, cursor.slot, cursor.slot)
    const booking = (bookingsByRoom.get(cursorRoom.id) ?? []).find((b) => overlaps(rangeOf(b), slot))
    const lock = (locksByRoom.get(cursorRoom.id) ?? []).find((l) => overlaps(rangeOf(l), slot))
    announcement = describeCursor(cursorRoom, fromMinutes(slot.start), fromMinutes(slot.end), booking, lock)
    if (selected && selected.roomId === cursorRoom.id && selected.range.end - selected.range.start > w.slot) {
      announcement += `. Seleção: ${fromMinutes(selected.range.start)} às ${fromMinutes(selected.range.end)}`
    }
  }

  // Nunca carregou a lista de salas: esqueleto, ou o erro com "Tentar de novo" (o AppShell também
  // tenta sozinho). Uma sala avulsa recebida por evento não vira uma grade incompleta (R15).
  if (roomsStatus !== 'ready') {
    if (roomsStatus !== 'error') return <GridSkeleton />
    return (
      <EmptyState
        className="h-full"
        icon={<AlertTriangle className="size-5" />}
        title="Não foi possível carregar as salas"
        description={`${roomsError ?? 'O servidor não respondeu.'} Tentando de novo automaticamente…`}
        action={
          <Button icon={<RefreshCw className="size-4" />} onClick={() => void useRooms.getState().load()}>
            Tentar de novo
          </Button>
        }
      />
    )
  }

  if (allRooms.length === 0) {
    return (
      <EmptyState
        className="h-full"
        icon={<DoorClosed className="size-5" />}
        title="Nenhuma sala cadastrada"
        description={
          isAdmin
            ? 'Cadastre as salas da recepção para começar a reservar.'
            : 'Peça a um administrador para cadastrar as salas da recepção.'
        }
        action={
          isAdmin ? (
            <Button variant="primary" onClick={() => navigate('/admin/salas')}>
              Cadastrar salas
            </Button>
          ) : undefined
        }
      />
    )
  }

  // "Livres Dia Inteiro" só pode responder depois que as reservas do dia chegaram: sem elas toda sala
  // pareceria livre (R41).
  if (filter === 'free' && !dateStatus?.known) {
    return dateStatus?.status === 'error' ? (
      <EmptyState
        className="h-full"
        icon={<AlertTriangle className="size-5" />}
        title={`Não foi possível carregar as reservas de ${formatTabLabel(date)}`}
        description="Sem as reservas do dia não dá para saber quais salas estão livres o dia inteiro."
        action={<Button onClick={() => void useBookings.getState().loadWeek(date)}>Tentar de novo</Button>}
      />
    ) : (
      <EmptyState
        className="h-full"
        icon={<Spinner className="size-5" />}
        title="Procurando salas livres o dia inteiro…"
        description={`Carregando as reservas de ${formatTabLabel(date)}.`}
      />
    )
  }

  if (rooms.ordered.length === 0) {
    const label = ROOM_FILTERS.find((f) => f.value === filter)?.label ?? ''
    return (
      <EmptyState
        className="h-full"
        icon={filter === 'free' ? <CalendarX2 className="size-5" /> : <Filter className="size-5" />}
        title={
          filter === 'free'
            ? `Nenhuma sala livre o dia inteiro em ${formatTabLabel(date)}`
            : `Nenhuma sala em “${label}”`
        }
        description={filter === 'free' ? 'Todas as salas têm ao menos uma reserva neste dia.' : undefined}
        action={<Button onClick={() => useUi.getState().setFilter('all')}>Mostrar todas as salas</Button>}
      />
    )
  }

  const loading = dateStatus?.status === 'loading'
  const loadError = dateStatus?.status === 'error'

  return (
    <div className="relative h-full min-h-0 bg-surface">
      {loading && (
        <div
          className="absolute inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-video-soft"
          role="progressbar"
          aria-label="Carregando reservas"
        >
          <div className="h-full w-1/3 animate-indeterminate bg-video" />
        </div>
      )}
      {loadError && (
        <div className="absolute inset-x-0 top-0 z-50 flex items-center justify-center gap-2 bg-danger-tint py-1 text-[12px] text-danger-ink">
          Não foi possível carregar as reservas deste dia.
          <button
            type="button"
            className="font-semibold underline"
            onClick={() => void useBookings.getState().loadWeek(date)}
          >
            Tentar de novo
          </button>
        </div>
      )}
      {/*
        Estrutura ARIA de grade válida (grid → row → columnheader/gridcell): as linhas usam
        `display: contents`, então o layout continua sendo o CSS grid de colunas por sala (R42).
      */}
      <div
        ref={setScrollEl}
        tabIndex={0}
        role="grid"
        aria-label={`Grade de salas de ${formatTabLabel(date)}`}
        aria-colcount={rooms.ordered.length}
        className="scroll-thin h-full overflow-auto overscroll-contain outline-none focus-visible:shadow-[inset_0_0_0_2px_var(--color-video)]"
      >
        <div
          className="grid"
          style={{
            gridTemplateColumns: `${TIME_COL_WIDTH}px repeat(${rooms.ordered.length}, minmax(${MIN_ROOM_WIDTH}px, 1fr))`,
            minWidth: TIME_COL_WIDTH + rooms.ordered.length * MIN_ROOM_WIDTH,
          }}
        >
          <div role="row" className="contents">
            <div
              aria-hidden="true"
              className="sticky top-0 left-0 z-40 border-r border-b border-line-strong bg-surface"
              style={{ height: GROUP_HEADER_HEIGHT }}
            />
            {rooms.video.length > 0 && (
              <GroupHeader kind="video" span={rooms.video.length} count={rooms.video.length} />
            )}
            {rooms.normal.length > 0 && (
              <GroupHeader kind="normal" span={rooms.normal.length} count={rooms.normal.length} />
            )}
          </div>

          <div role="row" className="contents">
            <div
              aria-hidden="true"
              className="sticky left-0 z-40 flex items-end border-r border-b border-line-strong bg-surface px-2 pb-1.5"
              style={{ top: GROUP_HEADER_HEIGHT, height: HEADER_HEIGHT - GROUP_HEADER_HEIGHT }}
            >
              <span className="text-[10.5px] font-medium tracking-wide text-faint uppercase">Hora</span>
            </div>
            {rooms.ordered.map((room) => (
              <RoomHeader
                key={room.id}
                room={room}
                kind={kindOf(room)}
                timeZone={config.timezone}
                onMaintenance={setMaintenanceRoom}
              />
            ))}
          </div>

          <div role="row" className="contents">
            <TimeColumn window={w} rowHeight={rowHeight} nowMinutes={nowMinutes} pastUntil={pastUntil} />
            {rooms.ordered.map((room) => (
              <RoomColumn
                key={room.id}
                room={room}
                kind={kindOf(room)}
                window={w}
                rowHeight={rowHeight}
                bookings={bookingsByRoom.get(room.id) ?? EMPTY_BOOKINGS}
                locks={locksByRoom.get(room.id) ?? EMPTY_LOCKS}
                selection={selected?.roomId === room.id ? selected.range : null}
                dropPreview={drag && drag.roomId === room.id ? drag : null}
                draggingId={drag?.bookingId ?? null}
                pendingIds={pendingMoves}
                cursorSlot={cursor && cursor.roomId === room.id && !composer ? cursor.slot : null}
                pastUntil={pastUntil}
                nowMinutes={nowMinutes}
                highlightId={highlightId}
                onCellPointerDown={onCellPointerDown}
                onBookingPointerDown={onBookingPointerDown}
                registerColumn={registerColumn}
              />
            ))}
          </div>
        </div>
      </div>
      <div aria-live="polite" aria-atomic="true" className="sr-only" data-grid-announcer="">
        {announcement}
      </div>
      <MaintenanceDialog room={maintenanceRoom} onClose={() => setMaintenanceRoom(null)} />
    </div>
  )
}

function GridSkeleton() {
  return (
    <div className="flex h-full gap-px bg-line p-px" aria-busy="true" aria-label="Carregando salas">
      <div className="w-14 bg-surface" />
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex flex-1 flex-col gap-2 bg-surface p-2">
          <div className="h-8 animate-shimmer rounded bg-line" />
          <div className="mt-6 h-14 animate-shimmer rounded bg-line/70" />
        </div>
      ))}
    </div>
  )
}
