import { useState } from 'react'
import { Download } from 'lucide-react'

import { errorMessage } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { BookingOut, ISODate } from '../../api/types'
import { Segmented } from '../../components/ui/Segmented'
import { Tooltip } from '../../components/ui/Tooltip'
import { cn } from '../../lib/cn'
import { ROOM_FILTERS, type RoomFilter } from '../../lib/rooms'
import { formatDateLong, formatTabLabel, weekMonday } from '../../lib/time'
import { toast } from '../../store/toasts'
import { useUi } from '../../store/ui'
import { BrandMark } from '../common/BrandMark'
import { ConnectionIndicator } from '../common/ConnectionIndicator'
import { PresenceAvatars } from '../common/PresenceAvatars'
import { UserMenu } from '../common/UserMenu'
import { SearchBox } from './SearchBox'

export function TopBar({
  date,
  today,
  onNavigate,
}: {
  date: ISODate
  today: ISODate
  onNavigate: (booking: BookingOut) => void
}) {
  const filter = useUi((s) => s.filter)
  const setFilter = useUi((s) => s.setFilter)

  return (
    <header className="relative z-40 flex h-12 shrink-0 items-center gap-3 border-b border-line-strong bg-surface px-3">
      <div className="flex shrink-0 items-center gap-2.5">
        <BrandMark className="size-7" />
        <div className="hidden leading-tight xl:block">
          <p className="font-condensed text-[15px] font-semibold tracking-tight text-ink">Agenda de Salas</p>
          <p className="text-[11px] text-muted first-letter:uppercase">{formatDateLong(date, true)}</p>
        </div>
      </div>

      <SearchBox today={today} onNavigate={onNavigate} />

      <Segmented<RoomFilter>
        label="Filtrar salas"
        value={filter}
        onChange={setFilter}
        options={ROOM_FILTERS.map((f) => ({
          value: f.value,
          title: f.label,
          label: (
            <>
              <span className="hidden 2xl:inline">{f.label}</span>
              <span className="2xl:hidden">{f.short}</span>
            </>
          ),
        }))}
      />

      <div className="ml-auto flex shrink-0 items-center gap-3">
        <ExportButtons date={date} />
        <span className="h-5 w-px bg-line-strong" aria-hidden="true" />
        <ConnectionIndicator />
        <PresenceAvatars />
        <UserMenu />
      </div>
    </header>
  )
}

function ExportButtons({ date }: { date: ISODate }) {
  const [busy, setBusy] = useState<'day' | 'week' | null>(null)

  const run = async (kind: 'day' | 'week') => {
    setBusy(kind)
    try {
      const filename = kind === 'day' ? await api.excel.exportDay(date) : await api.excel.exportWeek(weekMonday(date))
      toast.success('Planilha baixada', { description: filename })
    } catch (error) {
      toast.error(errorMessage(error, 'Não foi possível gerar a planilha.'))
    } finally {
      setBusy(null)
    }
  }

  const item = (kind: 'day' | 'week', label: string, hint: string) => (
    <Tooltip content={hint}>
      <button
        type="button"
        onClick={() => void run(kind)}
        disabled={busy !== null}
        aria-busy={busy === kind || undefined}
        className={cn(
          'flex h-7 items-center gap-1 px-2 text-[12.5px] font-medium text-ink-2 hover:bg-paper hover:text-ink disabled:opacity-60',
          kind === 'day' ? 'rounded-l-[5px]' : 'rounded-r-[5px] border-l border-line-strong',
        )}
      >
        <Download className={cn('size-3.5', busy === kind && 'animate-bounce')} aria-hidden="true" />
        {label}
      </button>
    </Tooltip>
  )

  return (
    <div className="flex items-center gap-1.5" role="group" aria-label="Exportar para Excel">
      <span className="hidden text-[11.5px] text-muted 2xl:inline">Exportar</span>
      <div className="flex rounded-md border border-line-strong bg-surface">
        {item('day', 'Dia', `Exportar ${formatTabLabel(date)} (.xlsx)`)}
        {item('week', 'Semana', 'Exportar a semana inteira (.xlsx, uma aba por dia)')}
      </div>
    </div>
  )
}
