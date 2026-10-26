import { memo, type PointerEvent as ReactPointerEvent } from 'react'
import { AlertTriangle, ArrowLeftRight, CalendarCheck2, Check, ExternalLink, UserX } from 'lucide-react'

import type { BookingOut } from '../../api/types'
import { cn } from '../../lib/cn'
import type { BlockBox } from '../../lib/grid'
import type { RoomGroupKind } from './gridLayout'

type Density = 'xs' | 'sm' | 'md' | 'lg'

function densityFor(height: number): Density {
  if (height < 34) return 'xs'
  if (height < 56) return 'sm'
  if (height < 92) return 'md'
  return 'lg'
}

export interface BookingFlag {
  key: string
  label: string
  short: string
  icon: typeof Check
  tone: string
}

/** Selos da reserva (contrato §7): Dia inteiro, Remanejável, Precisa de vídeo, Check-in, Não compareceu. */
export function bookingFlags(booking: BookingOut): BookingFlag[] {
  const flags: BookingFlag[] = []
  if (booking.is_full_day)
    flags.push({
      key: 'full',
      label: 'Dia inteiro',
      short: 'Dia inteiro',
      icon: CalendarCheck2,
      tone: 'bg-ink/[0.07] text-ink-2 border-ink/10',
    })
  if (booking.is_relocatable)
    flags.push({
      key: 'relocatable',
      label: 'Reunião Presencial — Remanejável',
      short: 'Remanejável',
      icon: ArrowLeftRight,
      tone: 'bg-lock-tint text-lock-ink border-lock/30',
    })
  if (booking.video_unavailable)
    flags.push({
      key: 'video',
      label: '⚠ Precisa de vídeo',
      short: 'Precisa de vídeo',
      icon: AlertTriangle,
      tone: 'bg-danger-tint text-danger-ink border-danger/25',
    })
  if (booking.status === 'CHECKED_IN')
    flags.push({
      key: 'checkin',
      label: 'Check-in',
      short: 'Check-in',
      icon: Check,
      tone: 'bg-success-tint text-success border-success/25',
    })
  if (booking.status === 'NO_SHOW')
    flags.push({
      key: 'noshow',
      label: 'Não compareceu',
      short: 'Não compareceu',
      icon: UserX,
      tone: 'bg-paper text-muted border-line-strong',
    })
  return flags
}

export function bookingAriaLabel(booking: BookingOut): string {
  return [
    booking.ticket_number ? `Chamado ${booking.ticket_number}` : 'Sem chamado',
    booking.requester_name,
    booking.title,
    `${booking.start} às ${booking.end}`,
    ...bookingFlags(booking).map((f) => f.short),
  ]
    .filter(Boolean)
    .join(', ')
}

function TicketLink({ booking, className }: { booking: BookingOut; className?: string }) {
  if (!booking.ticket_number) return null
  const text = `#${booking.ticket_number}`
  if (!booking.ticket_url) return <span className={cn('font-mono font-medium', className)}>{text}</span>
  return (
    <a
      href={booking.ticket_url}
      target="_blank"
      rel="noopener noreferrer"
      title="Abrir chamado em nova aba"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      className={cn(
        'group/ticket inline-flex items-center gap-0.5 font-mono font-medium underline-offset-2 hover:underline',
        className,
      )}
    >
      {text}
      <ExternalLink className="size-2.5 opacity-0 group-hover/ticket:opacity-70" aria-hidden="true" />
    </a>
  )
}

/** Posição do bloco em números (ver lib/grid.blockBox): props primitivas mantêm o memo eficaz. */
export interface BookingBlockProps extends BlockBox {
  booking: BookingOut
  kind: RoomGroupKind
  highlighted: boolean
  past: boolean
  /** Bloco original enquanto é arrastado (fica esmaecido no lugar). */
  ghosted: boolean
  /** Remanejamento otimista aguardando o servidor. */
  pending: boolean
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>, booking: BookingOut) => void
}

export const BookingBlock = memo(function BookingBlock({
  booking,
  top,
  height,
  clippedTop,
  clippedBottom,
  kind,
  highlighted,
  past,
  ghosted,
  pending,
  onPointerDown,
}: BookingBlockProps) {
  const density = densityFor(height)
  const flags = bookingFlags(booking)
  const noShow = booking.status === 'NO_SHOW'
  const accent = noShow
    ? 'border-l-faint bg-paper'
    : booking.status === 'CHECKED_IN'
      ? cn('border-l-success', kind === 'video' ? 'bg-video-tint' : 'bg-normal-tint')
      : kind === 'video'
        ? 'border-l-video bg-video-tint hover:bg-video-soft/70'
        : 'border-l-normal bg-normal-tint hover:bg-normal-soft/70'

  return (
    <div
      role="button"
      tabIndex={-1}
      data-booking-id={booking.id}
      aria-label={bookingAriaLabel(booking)}
      onPointerDown={(event) => onPointerDown(event, booking)}
      className={cn(
        'absolute right-[3px] left-[3px] z-10 flex touch-none flex-col overflow-hidden rounded-[5px] border-l-[3px] text-ink shadow-block transition-[background-color,opacity] select-none',
        accent,
        booking.is_relocatable && 'outline-1 -outline-offset-1 outline-lock/60 outline-dashed',
        density === 'xs' ? 'justify-center px-1.5' : 'px-1.5 py-[3px]',
        past && 'opacity-70 saturate-[0.6]',
        ghosted && 'opacity-35',
        pending && 'animate-shimmer',
        highlighted && 'z-[11] animate-highlight',
        clippedTop && 'rounded-t-none',
        clippedBottom && 'rounded-b-none',
      )}
      style={{ top: top + 1, height: Math.max(height - 2, 14) }}
    >
      {density === 'xs' ? (
        <div className="flex min-w-0 items-center gap-1 text-[11.5px] leading-tight">
          <span className={cn('min-w-0 truncate font-semibold', noShow && 'text-muted line-through')}>
            {booking.requester_name}
          </span>
          <TicketLink booking={booking} className="min-w-0 shrink-[4] truncate text-[10.5px] text-muted" />
          <FlagIcons flags={flags} />
        </div>
      ) : (
        <>
          {/* Coluna estreita (ex.: 10 salas em 1366 px): o fim some antes de cortar o horário pela metade. */}
          <div className="@container flex min-w-0 items-center gap-1 text-[11px] leading-tight text-ink-2">
            <TicketLink booking={booking} className="shrink-0" />
            <span className="tabular ml-auto shrink-0 font-mono text-[10.5px] text-muted">
              {booking.start}
              <span className="hidden @min-[7.5rem]:inline">–{booking.end}</span>
            </span>
          </div>
          <p className={cn('truncate text-[12.5px] leading-snug font-semibold', noShow && 'text-muted line-through')}>
            {booking.requester_name}
          </p>
          {density !== 'sm' && booking.title && (
            <p className={cn('text-[11.5px] leading-snug text-muted', density === 'lg' ? 'line-clamp-2' : 'truncate')}>
              {booking.title}
            </p>
          )}
          {density === 'sm'
            ? flags.length > 0 && (
                <div className="absolute right-1 bottom-[3px]">
                  <FlagIcons flags={flags} />
                </div>
              )
            : flags.length > 0 && (
                <div
                  className="mt-auto flex min-w-0 flex-wrap gap-0.5 overflow-hidden pt-0.5"
                  style={{ maxHeight: density === 'md' ? 18 : 40 }}
                >
                  {flags.map((f) => (
                    <span
                      key={f.key}
                      title={f.label}
                      className={cn(
                        'inline-flex max-w-full items-center gap-0.5 rounded-[3px] border px-1 text-[10px] leading-[14px] font-medium whitespace-nowrap',
                        f.tone,
                      )}
                    >
                      <f.icon className="size-2.5 shrink-0" aria-hidden="true" />
                      <span className="truncate">{density === 'lg' ? f.label.replace(/^⚠ /, '') : f.short}</span>
                    </span>
                  ))}
                </div>
              )}
        </>
      )}
    </div>
  )
})

function FlagIcons({ flags }: { flags: BookingFlag[] }) {
  if (flags.length === 0) return null
  return (
    <span className="ml-auto flex shrink-0 items-center gap-0.5">
      {flags.map((f) => (
        <span
          key={f.key}
          title={f.label}
          className={cn('inline-flex size-3.5 items-center justify-center rounded-[3px] border', f.tone)}
        >
          <f.icon className="size-2.5" aria-hidden="true" />
        </span>
      ))}
    </span>
  )
}
