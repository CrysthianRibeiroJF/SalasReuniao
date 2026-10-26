/**
 * Teclado da grade (RNF03): setas movem o cursor de célula, Shift+↑/↓ estende a seleção,
 * Enter abre o modal (ou o popover, se o cursor estiver sobre uma reserva), Esc limpa,
 * Home/End vão ao primeiro/último horário. Ignorado enquanto se digita ou com modal aberto.
 */
import { useEffect } from 'react'

import type { BookingOut, ISODate, RoomOut } from '../../api/types'
import { useLatest } from '../../hooks/useLatest'
import {
  clamp,
  type DayWindow,
  extendSelection,
  isSlotFree,
  type MinuteRange,
  rangeFromSlots,
  slotCount,
} from '../../lib/grid'
import { fromMinutes } from '../../lib/time'
import { type AnchorRect, useUi } from '../../store/ui'
import { beginBooking, cancelPendingBegin, hasPendingBegin } from './beginBooking'

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag !== 'INPUT') return false
  const type = (target as HTMLInputElement).type
  return !['button', 'checkbox', 'radio', 'submit', 'reset', 'range', 'color'].includes(type)
}

export function hasOpenModal(): boolean {
  return document.querySelector('[role="dialog"][aria-modal="true"]') !== null
}

interface Params {
  enabled: boolean
  rooms: readonly RoomOut[]
  date: ISODate
  window: DayWindow
  occupiedFor: (roomId: number) => MinuteRange[]
  bookingAt: (roomId: number, slot: number) => BookingOut | undefined
  anchorFor: (roomId: number, range: MinuteRange) => AnchorRect | null
  /** Enter num slot ocupado sem reserva (travado por outra pessoa). */
  notifyBlocked: (roomId: number, slot: number) => void
  openBooking: (booking: BookingOut) => void
  initialSlot: () => number
  revealCell: (roomId: number, slot: number) => void
}

export function useGridKeyboard(params: Params): void {
  const latest = useLatest(params)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const p = latest.current
      if (!p.enabled || event.defaultPrevented || event.altKey || event.metaKey || event.ctrlKey) return
      if (isEditableTarget(event.target) || hasOpenModal()) return
      const ui = useUi.getState()
      if (ui.composer || ui.popover) return

      const handled = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', 'Escape', 'Home', 'End']
      if (!handled.includes(event.key) || p.rooms.length === 0) return
      // Enter/Espaço num botão focado (ex.: menu, abas) continua sendo do botão.
      if (
        event.key === 'Enter' &&
        event.target instanceof HTMLElement &&
        event.target.closest('button, a, [role="tab"]')
      )
        return

      const rows = slotCount(p.window)
      let cursor = ui.cursor && p.rooms.some((r) => r.id === ui.cursor?.roomId) ? ui.cursor : null

      if (event.key === 'Escape') {
        // Esc antes da resposta da trava: a resposta que chegar depois é liberada e o modal não abre (R32).
        if (hasPendingBegin()) {
          cancelPendingBegin()
          ui.setSelection(null)
        } else if (ui.selection) ui.setSelection(null)
        else if (cursor) ui.setCursor(null)
        else return
        event.preventDefault()
        return
      }

      event.preventDefault()
      if (!cursor) {
        cursor = { roomId: p.rooms[0]!.id, slot: clamp(p.initialSlot(), 0, rows - 1) }
        ui.setCursor(cursor)
        p.revealCell(cursor.roomId, cursor.slot)
        if (event.key !== 'Enter') return
      }

      const roomIndex = p.rooms.findIndex((r) => r.id === cursor.roomId)
      const moveTo = (roomId: number, slot: number) => {
        ui.setCursor({ roomId, slot })
        p.revealCell(roomId, slot)
      }

      switch (event.key) {
        case 'ArrowUp':
        case 'ArrowDown': {
          const delta = event.key === 'ArrowUp' ? -1 : 1
          if (event.shiftKey) {
            const occupied = p.occupiedFor(cursor.roomId)
            const current =
              ui.selection && ui.selection.roomId === cursor.roomId && ui.selection.date === p.date
                ? ui.selection
                : { roomId: cursor.roomId, date: p.date, anchor: cursor.slot, focus: cursor.slot }
            if (!isSlotFree(p.window, current.anchor, occupied)) return
            const [, focus] = extendSelection(p.window, current.anchor, current.focus + delta, occupied)
            ui.setSelection({ ...current, focus })
            moveTo(cursor.roomId, focus)
          } else {
            ui.setSelection(null)
            moveTo(cursor.roomId, clamp(cursor.slot + delta, 0, rows - 1))
          }
          return
        }
        case 'ArrowLeft':
        case 'ArrowRight': {
          const next = p.rooms[clamp(roomIndex + (event.key === 'ArrowLeft' ? -1 : 1), 0, p.rooms.length - 1)]!
          ui.setSelection(null)
          moveTo(next.id, cursor.slot)
          return
        }
        case 'Home':
        case 'End':
          ui.setSelection(null)
          moveTo(cursor.roomId, event.key === 'Home' ? 0 : rows - 1)
          return
        case 'Enter': {
          const booking = p.bookingAt(cursor.roomId, cursor.slot)
          if (booking) {
            p.openBooking(booking)
            return
          }
          const selection =
            ui.selection && ui.selection.roomId === cursor.roomId && ui.selection.date === p.date ? ui.selection : null
          const anchorSlot = selection?.anchor ?? cursor.slot
          if (!isSlotFree(p.window, anchorSlot, p.occupiedFor(cursor.roomId))) {
            p.notifyBlocked(cursor.roomId, anchorSlot)
            return
          }
          const range = rangeFromSlots(p.window, anchorSlot, selection?.focus ?? cursor.slot)
          if (!selection)
            ui.setSelection({ roomId: cursor.roomId, date: p.date, anchor: cursor.slot, focus: cursor.slot })
          void beginBooking({
            roomId: cursor.roomId,
            date: p.date,
            start: fromMinutes(range.start),
            end: fromMinutes(range.end),
            anchor: p.anchorFor(cursor.roomId, range),
          })
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [latest])
}
