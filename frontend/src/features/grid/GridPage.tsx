import { useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router'

import type { BookingOut, ISODate } from '../../api/types'
import { useNow } from '../../hooks/useNow'
import { isValidISODate, weekMonday, zonedParts } from '../../lib/time'
import { useBookings, useDateCounts } from '../../store/bookings'
import { useLoadedConfig } from '../../store/config'
import { useRealtime } from '../../store/realtime'
import { useRooms } from '../../store/rooms'
import { useUi } from '../../store/ui'
import { cancelPendingBegin } from './beginBooking'
import { BookingComposerHost } from './composer/BookingComposer'
import { BookingPopoverHost } from './BookingPopover'
import { dayTabDates, DayTabs, stepWorkDay } from './DayTabs'
import { RoomGrid } from './RoomGrid'
import { planSearchNavigation } from './searchNav'
import { TopBar } from './TopBar'
import { hasOpenModal, isEditableTarget } from './useGridKeyboard'

/** Hoje no fuso da recepção (vira à meia-noite sem recarregar). */
export function useToday(timeZone: string): ISODate {
  const now = useNow(60_000)
  const skew = useRealtime((s) => s.clockSkewMs)
  return zonedParts(now + skew, timeZone).date
}

/** Tela principal: barra superior, grade, modal rápido e abas diárias (4 zonas fixas). */
export function GridPage() {
  const config = useLoadedConfig()
  const today = useToday(config.timezone)
  const [params, setParams] = useSearchParams()
  const raw = params.get('date')
  const activeDate = isValidISODate(raw) ? raw : today

  const setDate = useCallback(
    (date: ISODate, replace = false) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set('date', date)
          return next
        },
        { replace },
      )
    },
    [setParams],
  )

  // A data ativa sempre aparece na URL (?date=YYYY-MM-DD), para compartilhar e recarregar.
  useEffect(() => {
    if (raw !== activeDate) setDate(activeDate, true)
  }, [raw, activeDate, setDate])

  useEffect(() => {
    const ui = useUi.getState()
    ui.setActiveDate(activeDate)
    ui.setSelection(null)
    ui.closePopover()
    cancelPendingBegin()
  }, [activeDate])

  // Sair da grade (outra rota, Voltar do navegador) encerra a tentativa de reserva: a trava do modal
  // já é liberada ao desmontar, e o modal não pode reaparecer depois sem trava nem o que foi digitado (R8).
  useEffect(
    () => () => {
      cancelPendingBegin()
      const ui = useUi.getState()
      ui.closeComposer()
      ui.closePopover()
      ui.setCursor(null)
    },
    [],
  )

  // ?highlight=<id> (ex.: links da administração de salas): destaca a reserva no dia e limpa o parâmetro.
  const highlightParam = params.get('highlight')
  useEffect(() => {
    if (!highlightParam) return
    const ui = useUi.getState()
    ui.setFilter('all')
    ui.highlight(highlightParam)
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('highlight')
        return next
      },
      { replace: true },
    )
  }, [highlightParam, setParams])

  const monday = weekMonday(activeDate)
  useEffect(() => {
    void useBookings.getState().loadWeek(monday)
  }, [monday])

  const tabs = useMemo(() => dayTabDates(activeDate, config.work_days), [activeDate, config.work_days])
  const countList = useDateCounts(tabs)
  const counts = useMemo(() => Object.fromEntries(tabs.map((d, i) => [d, countList[i] ?? null])), [tabs, countList])

  const onNavigate = useCallback(
    (booking: BookingOut) => {
      const ui = useUi.getState()
      const plan = planSearchNavigation(booking, ui.filter, useRooms.getState().byId[booking.room_id])
      useBookings.getState().upsert(booking)
      if (plan.resetFilter) ui.setFilter('all')
      setDate(plan.date)
      ui.highlight(plan.highlightId)
    },
    [setDate],
  )

  // Atalhos globais: busca (/ e Ctrl+K) e troca de dia (PageUp/PageDown, Ctrl+←/→).
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      const ui = useUi.getState()
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        ui.focusSearch()
        return
      }
      if (isEditableTarget(event.target) || hasOpenModal() || ui.composer) return
      if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault()
        ui.focusSearch()
        return
      }
      const step =
        event.key === 'PageUp' || (event.ctrlKey && event.key === 'ArrowLeft')
          ? -1
          : event.key === 'PageDown' || (event.ctrlKey && event.key === 'ArrowRight')
            ? 1
            : 0
      if (step !== 0 && !event.altKey && !event.shiftKey) {
        event.preventDefault()
        setDate(stepWorkDay(activeDate, step, config.work_days))
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeDate, config.work_days, setDate])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <TopBar date={activeDate} today={today} onNavigate={onNavigate} />
      <main className="min-h-0 flex-1">
        <RoomGrid date={activeDate} />
      </main>
      <DayTabs
        activeDate={activeDate}
        today={today}
        workDays={config.work_days}
        counts={counts}
        onSelect={(d) => setDate(d)}
      />
      <BookingComposerHost />
      <BookingPopoverHost />
    </div>
  )
}
