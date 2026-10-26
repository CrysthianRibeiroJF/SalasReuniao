import { memo } from 'react'
import { Ban, DoorOpen, Users, Video, Wrench } from 'lucide-react'

import type { RoomOut } from '../../api/types'
import { Tooltip } from '../../components/ui/Tooltip'
import { cn } from '../../lib/cn'
import { isInMaintenance } from '../../lib/rooms'
import { formatDateTime } from '../../lib/time'
import { GROUP_HEADER_HEIGHT, ROOM_HEADER_HEIGHT, type RoomGroupKind, TIME_COL_WIDTH } from './gridLayout'

export function GroupHeader({ kind, span, count }: { kind: RoomGroupKind; span: number; count: number }) {
  const video = kind === 'video'
  return (
    <div
      role="columnheader"
      aria-colspan={span}
      className={cn(
        'sticky top-0 z-30 flex items-center border-b border-l border-line-strong first-of-type:border-l-0',
        video ? 'bg-video-tint text-video-ink' : 'bg-normal-tint text-normal-ink',
      )}
      style={{ gridColumn: `span ${span}`, height: GROUP_HEADER_HEIGHT }}
    >
      <span
        className="sticky flex items-center gap-1.5 px-2.5 text-[11px] font-semibold tracking-[0.08em] uppercase"
        style={{ left: TIME_COL_WIDTH }}
      >
        {video ? (
          <Video className="size-3.5" aria-hidden="true" />
        ) : (
          <DoorOpen className="size-3.5" aria-hidden="true" />
        )}
        {video ? 'Videoconferência' : 'Salas Normais'}
        <span className="font-mono text-[10.5px] font-medium tracking-normal opacity-70">{count}</span>
      </span>
    </div>
  )
}

export const RoomHeader = memo(function RoomHeader({
  room,
  kind,
  timeZone,
  onMaintenance,
}: {
  room: RoomOut
  kind: RoomGroupKind
  timeZone: string
  onMaintenance: (room: RoomOut) => void
}) {
  const maintenance = isInMaintenance(room)
  const canToggleVideo = room.room_type === 'VIDEO'
  const details = (
    <div className="space-y-1">
      <p className="font-semibold">{room.name}</p>
      <p>
        {room.room_type === 'VIDEO' ? 'Sala de videoconferência' : 'Sala normal'}
        {room.capacity > 0 ? ` · ${room.capacity} lugares` : ''}
      </p>
      {room.resources.length > 0 ? (
        <ul className="list-inside list-disc text-white/85">
          {room.resources.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : (
        <p className="text-white/70">Sem recursos cadastrados</p>
      )}
      {maintenance && (
        <p className="text-white/85">
          Vídeo em manutenção{room.maintenance_ticket_id ? ` (chamado ${room.maintenance_ticket_id})` : ''}
          {room.maintenance_since ? ` desde ${formatDateTime(room.maintenance_since, timeZone)}` : ''}
        </p>
      )}
      {!room.is_active && (
        <p className="text-white/85">
          Sala desativada: aparece só nos dias em que ainda tem reservas. Mova-as para outra sala ou cancele.
        </p>
      )}
    </div>
  )

  return (
    <div
      role="columnheader"
      aria-label={`${room.name}${room.capacity ? `, ${room.capacity} lugares` : ''}${room.is_active ? '' : ', sala desativada'}`}
      className={cn(
        'group/header sticky z-30 flex min-w-0 flex-col justify-center gap-0.5 border-b border-l border-line-strong bg-surface px-2',
        kind === 'video' && 'shadow-[inset_0_2px_0_var(--color-video)]',
      )}
      style={{ top: GROUP_HEADER_HEIGHT, height: ROOM_HEADER_HEIGHT }}
    >
      <div className="flex min-w-0 items-center gap-1">
        {kind === 'video' && <Video className="size-3.5 shrink-0 text-video" aria-hidden="true" />}
        <Tooltip content={details} className="min-w-0 flex-1">
          <span
            className="block truncate font-condensed text-[13.5px] leading-tight font-semibold text-ink"
            tabIndex={-1}
          >
            {room.name}
          </span>
        </Tooltip>
        {canToggleVideo && (
          <Tooltip content={maintenance ? 'Encerrar manutenção do vídeo' : 'Marcar vídeo em manutenção'}>
            <button
              type="button"
              onClick={() => onMaintenance(room)}
              aria-label={
                maintenance
                  ? `Encerrar manutenção do vídeo da ${room.name}`
                  : `Marcar vídeo da ${room.name} em manutenção`
              }
              className={cn(
                'shrink-0 rounded p-0.5 transition-opacity hover:bg-ink/[0.07] focus-visible:opacity-100',
                maintenance ? 'text-lock' : 'text-muted opacity-0 group-hover/header:opacity-100',
              )}
            >
              <Wrench className="size-3.5" />
            </button>
          </Tooltip>
        )}
      </div>
      {!room.is_active ? (
        <span className="inline-flex max-w-full min-w-0 items-center gap-1 self-start rounded-[4px] border border-line-strong bg-paper px-1 py-px text-[10.5px] font-medium text-muted">
          <Ban className="size-3 shrink-0" aria-hidden="true" />
          <span className="truncate">Sala desativada</span>
        </span>
      ) : maintenance ? (
        <MaintenanceBadge room={room} />
      ) : (
        <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted">
          {room.capacity > 0 && (
            <span className="tabular inline-flex shrink-0 items-center gap-0.5 font-mono">
              <Users className="size-3" aria-hidden="true" />
              {room.capacity}
            </span>
          )}
          {room.resources.length > 0 && <span className="truncate">{room.resources.join(' · ')}</span>}
        </div>
      )}
    </div>
  )
})

function MaintenanceBadge({ room }: { room: RoomOut }) {
  const label = `Vídeo em manutenção${room.maintenance_ticket_id ? ` · Chamado ${room.maintenance_ticket_id}` : ''}`
  // Coluna estreita (10 salas em 1366 px): o texto "Vídeo em manutenção" encolhe primeiro e o nº do
  // chamado continua legível (R33). O rótulo completo fica no nome acessível e na dica.
  const content = (
    <>
      <Wrench className="size-3 shrink-0" aria-hidden="true" />
      <span className="truncate" aria-hidden="true">
        Vídeo em manutenção
      </span>
      {room.maintenance_ticket_id && (
        <span className="shrink-0 font-mono" aria-hidden="true">
          · #{room.maintenance_ticket_id}
        </span>
      )}
    </>
  )
  const className =
    'inline-flex max-w-full min-w-0 items-center gap-1 self-start overflow-hidden rounded-[4px] border border-lock/30 bg-lock-tint px-1 py-px text-[10.5px] font-medium text-lock-ink'
  return (
    <Tooltip content={label}>
      {room.maintenance_ticket_url ? (
        <a
          href={room.maintenance_ticket_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={label}
          className={cn(className, 'hover:underline')}
        >
          {content}
        </a>
      ) : (
        <span role="note" aria-label={label} className={className}>
          {content}
        </span>
      )}
    </Tooltip>
  )
}
