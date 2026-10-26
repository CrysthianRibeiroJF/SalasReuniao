/**
 * Arrastar e soltar de blocos de reserva (pointer events): encaixe em slot_minutes, troca de
 * coluna pela posição horizontal, rolagem automática nas bordas e prévia do destino.
 * Um pointerdown sem movimento é tratado como clique (abre o popover de detalhes).
 */
import { type PointerEvent as ReactPointerEvent, type RefObject, useCallback, useEffect, useRef, useState } from 'react'

import type { BookingOut } from '../../api/types'
import { type DayWindow, dragTarget, type MinuteRange, minutesAtOffset, rangeOf } from '../../lib/grid'
import { useLatest } from '../../hooks/useLatest'
import type { AnchorRect } from '../../store/ui'
import { HEADER_HEIGHT, TIME_COL_WIDTH } from './gridLayout'

const DRAG_THRESHOLD_PX = 5
const EDGE_PX = 36
const MAX_SCROLL_STEP = 18

export interface DropCheck {
  valid: boolean
  /** Rótulo curto da prévia ("Mover para Sala 3", "Horário ocupado"). */
  message: string
  /** Mensagem do aviso quando o destino é inválido. */
  error?: string
}

export interface DragPreview extends DropCheck {
  bookingId: string
  roomId: number
  range: MinuteRange
}

interface Params {
  scrollRef: RefObject<HTMLDivElement | null>
  columnsRef: RefObject<Map<number, HTMLDivElement>>
  window: DayWindow
  rowHeight: number
  checkDrop: (booking: BookingOut, roomId: number, range: MinuteRange) => DropCheck
  onClick: (booking: BookingOut, anchor: AnchorRect) => void
  onDrop: (booking: BookingOut, roomId: number, range: MinuteRange, check: DropCheck) => void
}

interface Session {
  booking: BookingOut
  pointerId: number
  startX: number
  startY: number
  lastX: number
  lastY: number
  grabOffsetMin: number
  duration: number
  /** Dia inteiro só troca de sala (o horário é fixo). */
  lockVertical: boolean
  moved: boolean
  element: HTMLElement
}

function toAnchor(rect: DOMRect): AnchorRect {
  return { top: rect.top, left: rect.left, width: rect.width, height: rect.height }
}

export function useBookingDrag(params: Params) {
  const latest = useLatest(params)
  const session = useRef<Session | null>(null)
  const frame = useRef<number | null>(null)
  const [preview, setPreview] = useState<DragPreview | null>(null)
  const previewRef = useRef<DragPreview | null>(null)

  const updatePreview = useCallback((next: DragPreview | null) => {
    const prev = previewRef.current
    if (
      prev &&
      next &&
      prev.roomId === next.roomId &&
      prev.range.start === next.range.start &&
      prev.range.end === next.range.end &&
      prev.valid === next.valid
    ) {
      return
    }
    previewRef.current = next
    setPreview(next)
  }, [])

  const computeTarget = useCallback(
    (s: Session) => {
      const { columnsRef, window: w, rowHeight, checkDrop } = latest.current
      let best: { roomId: number; rect: DOMRect } | null = null
      let bestDistance = Number.POSITIVE_INFINITY
      for (const [roomId, el] of columnsRef.current) {
        const rect = el.getBoundingClientRect()
        const distance = s.lastX < rect.left ? rect.left - s.lastX : s.lastX > rect.right ? s.lastX - rect.right : 0
        if (distance < bestDistance) {
          bestDistance = distance
          best = { roomId, rect }
        }
      }
      if (!best) return
      const original = rangeOf(s.booking)
      const range = s.lockVertical
        ? original
        : dragTarget(w, s.duration, minutesAtOffset(s.lastY - best.rect.top, w, rowHeight), s.grabOffsetMin)
      const check = checkDrop(s.booking, best.roomId, range)
      updatePreview({ bookingId: s.booking.id, roomId: best.roomId, range, ...check })
    },
    [latest, updatePreview],
  )

  const stopAutoScroll = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    frame.current = null
  }

  const autoScroll = useCallback(() => {
    const s = session.current
    const container = latest.current.scrollRef.current
    if (!s || !s.moved || !container) {
      frame.current = null
      return
    }
    const rect = container.getBoundingClientRect()
    const step = (distance: number) => Math.ceil(MAX_SCROLL_STEP * (1 - Math.max(0, distance) / EDGE_PX))
    let dx = 0
    let dy = 0
    if (s.lastY < rect.top + HEADER_HEIGHT + EDGE_PX) dy = -step(s.lastY - rect.top - HEADER_HEIGHT)
    else if (s.lastY > rect.bottom - EDGE_PX) dy = step(rect.bottom - s.lastY)
    if (s.lastX < rect.left + TIME_COL_WIDTH + EDGE_PX) dx = -step(s.lastX - rect.left - TIME_COL_WIDTH)
    else if (s.lastX > rect.right - EDGE_PX) dx = step(rect.right - s.lastX)
    if (dx || dy) {
      container.scrollBy(dx, dy)
      computeTarget(s)
    }
    frame.current = requestAnimationFrame(autoScroll)
  }, [computeTarget, latest])

  const finish = useCallback(
    (commit: boolean) => {
      const s = session.current
      session.current = null
      stopAutoScroll()
      const result = previewRef.current
      updatePreview(null)
      if (!s) return
      if (!s.moved) {
        if (commit) latest.current.onClick(s.booking, toAnchor(s.element.getBoundingClientRect()))
        return
      }
      if (!commit || !result) return
      const original = rangeOf(s.booking)
      const unchanged =
        result.roomId === s.booking.room_id &&
        result.range.start === original.start &&
        result.range.end === original.end
      if (unchanged) return
      latest.current.onDrop(s.booking, result.roomId, result.range, result)
    },
    [latest, updatePreview],
  )

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const s = session.current
      if (!s || event.pointerId !== s.pointerId) return
      s.lastX = event.clientX
      s.lastY = event.clientY
      if (!s.moved) {
        if (Math.hypot(event.clientX - s.startX, event.clientY - s.startY) < DRAG_THRESHOLD_PX) return
        s.moved = true
        document.body.style.cursor = 'grabbing'
      }
      event.preventDefault()
      computeTarget(s)
      if (frame.current === null) frame.current = requestAnimationFrame(autoScroll)
    }
    const onUp = (event: PointerEvent) => {
      if (session.current && event.pointerId === session.current.pointerId) {
        document.body.style.cursor = ''
        finish(true)
      }
    }
    const onCancel = (event: PointerEvent) => {
      if (session.current && event.pointerId === session.current.pointerId) {
        document.body.style.cursor = ''
        finish(false)
      }
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && session.current?.moved) {
        event.preventDefault()
        event.stopPropagation()
        document.body.style.cursor = ''
        finish(false)
      }
    }
    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      window.removeEventListener('keydown', onKey, true)
      stopAutoScroll()
    }
  }, [autoScroll, computeTarget, finish])

  const onBookingPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>, booking: BookingOut) => {
      if (event.button !== 0) return
      event.stopPropagation()
      const element = event.currentTarget
      const rect = element.getBoundingClientRect()
      const { window: w, rowHeight } = latest.current
      const original = rangeOf(booking)
      // O bloco pode estar recortado no topo (começa antes da abertura): mede a partir do início visível.
      const visibleStart = Math.max(original.start, w.startMin)
      session.current = {
        booking,
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        grabOffsetMin: visibleStart - original.start + ((event.clientY - rect.top) / rowHeight) * w.slot,
        duration: original.end - original.start,
        lockVertical: booking.is_full_day || booking.period_type === 'FULL_DAY',
        moved: false,
        element,
      }
    },
    [latest],
  )

  return { preview, onBookingPointerDown }
}
