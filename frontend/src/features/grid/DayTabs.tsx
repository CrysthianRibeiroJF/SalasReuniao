import { type FormEvent, type KeyboardEvent, useRef, useState } from 'react'
import { CalendarPlus, ChevronLeft, ChevronRight } from 'lucide-react'

import type { ISODate } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Tooltip } from '../../components/ui/Tooltip'
import { useDismiss } from '../../hooks/useDismiss'
import { cn } from '../../lib/cn'
import {
  addDays,
  formatDateLong,
  formatTabLabel,
  formatWeekRange,
  isValidISODate,
  weekDates,
  weekdayIndex,
} from '../../lib/time'

/** Abas da semana exibida: dias úteis + o dia ativo se ele cair fora deles (aba temporária). */
export function dayTabDates(activeDate: ISODate, workDays: readonly number[]): ISODate[] {
  return weekDates(activeDate).filter((d) => workDays.includes(weekdayIndex(d)) || d === activeDate)
}

/** Próximo/anterior dia útil (PageUp/PageDown, Ctrl+←/→). */
export function stepWorkDay(date: ISODate, delta: 1 | -1, workDays: readonly number[]): ISODate {
  let next = date
  for (let i = 0; i < 7; i++) {
    next = addDays(next, delta)
    if (workDays.length === 0 || workDays.includes(weekdayIndex(next))) return next
  }
  return addDays(date, delta)
}

export interface DayTabsProps {
  activeDate: ISODate
  today: ISODate
  workDays: readonly number[]
  counts: Readonly<Record<ISODate, number | null>>
  onSelect: (date: ISODate) => void
}

export function DayTabs({ activeDate, today, workDays, counts, onSelect }: DayTabsProps) {
  const tabs = dayTabDates(activeDate, workDays)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (!delta) return
    event.preventDefault()
    event.stopPropagation()
    const next = (index + delta + tabs.length) % tabs.length
    onSelect(tabs[next]!)
    requestAnimationFrame(() => tabRefs.current[next]?.focus())
  }

  return (
    <nav
      aria-label="Dias da semana"
      className="relative z-30 flex h-10 shrink-0 items-stretch border-t border-line-strong bg-sheet text-[13px]"
    >
      <div className="flex items-center gap-0.5 border-r border-line-strong px-1.5">
        <Tooltip content="Semana anterior">
          <button
            type="button"
            aria-label="Semana anterior"
            onClick={() => onSelect(addDays(activeDate, -7))}
            className="rounded p-1 text-ink-2 hover:bg-surface/70"
          >
            <ChevronLeft className="size-4" />
          </button>
        </Tooltip>
        <Tooltip content="Próxima semana">
          <button
            type="button"
            aria-label="Próxima semana"
            onClick={() => onSelect(addDays(activeDate, 7))}
            className="rounded p-1 text-ink-2 hover:bg-surface/70"
          >
            <ChevronRight className="size-4" />
          </button>
        </Tooltip>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => onSelect(today)}
          disabled={activeDate === today}
          className="ml-0.5 text-[12px]"
        >
          Hoje
        </Button>
      </div>

      <div role="tablist" aria-label="Dia exibido" className="flex min-w-0 items-stretch overflow-x-auto">
        {tabs.map((date, i) => {
          const active = date === activeDate
          const extra = !workDays.includes(weekdayIndex(date))
          const count = counts[date]
          return (
            <button
              key={date}
              ref={(el) => {
                tabRefs.current[i] = el
              }}
              type="button"
              role="tab"
              aria-selected={active}
              aria-label={`${formatDateLong(date)}${count != null ? `, ${count} reserva${count === 1 ? '' : 's'}` : ''}${date === today ? ', hoje' : ''}`}
              tabIndex={active ? 0 : -1}
              onClick={() => onSelect(date)}
              onKeyDown={(e) => onTabKeyDown(e, i)}
              className={cn(
                'relative flex shrink-0 items-center gap-2 border-r border-line-strong px-3.5 whitespace-nowrap transition-colors',
                active
                  ? '-mt-px bg-surface font-semibold text-ink shadow-[inset_0_-2px_0_var(--color-ink)]'
                  : 'text-muted hover:bg-surface/60 hover:text-ink',
                extra && 'border-l border-dashed border-l-line-strong italic',
              )}
            >
              {date === today && <span className="size-1.5 rounded-full bg-video" aria-hidden="true" />}
              <span className="font-condensed text-[13.5px] tracking-tight">{formatTabLabel(date)}</span>
              {count != null && (
                <span
                  aria-hidden="true"
                  className={cn(
                    'tabular min-w-[18px] rounded-[4px] px-1 text-center font-mono text-[10.5px] leading-[16px] font-medium not-italic',
                    active ? 'bg-ink text-white' : 'bg-surface/70 text-ink-2',
                    count === 0 && 'opacity-60',
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <CalendarPicker activeDate={activeDate} onSelect={onSelect} />

      <div className="ml-auto hidden items-center px-3 font-mono text-[11.5px] text-muted md:flex">
        {formatWeekRange(tabs[0] ?? activeDate, tabs[tabs.length - 1] ?? activeDate)}
      </div>
    </nav>
  )
}

function CalendarPicker({ activeDate, onSelect }: { activeDate: ISODate; onSelect: (date: ISODate) => void }) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState(activeDate)
  const rootRef = useRef<HTMLDivElement>(null)
  useDismiss(rootRef, () => setOpen(false), { enabled: open })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!isValidISODate(value)) return
    onSelect(value)
    setOpen(false)
  }

  return (
    <div ref={rootRef} className="relative flex items-stretch border-r border-line-strong">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          setValue(activeDate)
          setOpen((v) => !v)
        }}
        className={cn(
          'flex items-center gap-1.5 px-3 text-[12.5px] font-medium text-ink-2 hover:bg-surface/60',
          open && 'bg-surface/80',
        )}
      >
        <CalendarPlus className="size-4" aria-hidden="true" />+ Calendário
      </button>
      {open && (
        <form
          role="dialog"
          aria-label="Escolher data"
          onSubmit={submit}
          className="absolute bottom-full left-0 mb-1.5 flex w-64 animate-pop-in flex-col gap-2 rounded-lg border border-line-strong bg-surface p-3 shadow-float"
        >
          <label htmlFor="calendar-date" className="text-[12px] font-medium text-ink-2">
            Ir para a data
          </label>
          <input
            id="calendar-date"
            type="date"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="h-9 rounded-md border border-line-strong px-2 text-[13px] outline-none focus:border-video focus:ring-3 focus:ring-video/15"
          />
          <p className="text-[11.5px] text-muted">Sábados e domingos aparecem como uma aba extra.</p>
          <Button type="submit" variant="primary" size="sm" disabled={!isValidISODate(value)}>
            Abrir dia
          </Button>
        </form>
      )}
    </div>
  )
}
