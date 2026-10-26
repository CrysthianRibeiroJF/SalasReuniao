import { memo } from 'react'

import { cn } from '../../lib/cn'
import { type DayWindow, offsetFor, slotCount, slotStart } from '../../lib/grid'
import { fromMinutes } from '../../lib/time'
import { TIME_COL_WIDTH } from './gridLayout'

export const TimeColumn = memo(function TimeColumn({
  window: w,
  rowHeight,
  nowMinutes,
  pastUntil,
}: {
  window: DayWindow
  rowHeight: number
  /** Minuto atual (só no dia de hoje). */
  nowMinutes: number | null
  pastUntil: number | null
}) {
  const rows = slotCount(w)
  const showNow = nowMinutes !== null && nowMinutes >= w.startMin && nowMinutes <= w.endMin
  return (
    <div
      className="sticky left-0 z-20 border-r border-line-strong bg-surface"
      style={{ width: TIME_COL_WIDTH, height: rows * rowHeight }}
      aria-hidden="true"
    >
      {Array.from({ length: rows }, (_, i) => {
        const minutes = slotStart(w, i)
        const fullHour = minutes % 60 === 0
        const past = pastUntil !== null && minutes + w.slot <= pastUntil
        return (
          <div
            key={i}
            className={cn(
              'tabular absolute right-0 left-0 pr-2 text-right font-mono leading-none',
              fullHour ? 'text-[11.5px] font-medium text-ink-2' : 'text-[10.5px] text-faint',
              past && 'opacity-55',
            )}
            style={{ top: i * rowHeight + 4 }}
          >
            {fromMinutes(minutes)}
          </div>
        )
      })}
      {showNow && (
        <div
          className="tabular absolute right-0 z-10 -translate-y-1/2 rounded-l-[4px] bg-now px-1 py-px font-mono text-[10px] leading-none font-medium text-white"
          style={{ top: offsetFor(nowMinutes, w, rowHeight) }}
        >
          {fromMinutes(Math.floor(nowMinutes))}
        </div>
      )}
    </div>
  )
})
