import { useMemo, useState } from 'react'
import { AlertTriangle, DoorClosed, Pencil, Plus, RefreshCw, Video, Wrench } from 'lucide-react'

import type { RoomOut } from '../../api/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { EmptyState } from '../../components/ui/EmptyState'
import { Skeleton } from '../../components/ui/Skeleton'
import { Switch } from '../../components/ui/Switch'
import { cn } from '../../lib/cn'
import { isInMaintenance } from '../../lib/rooms'
import { useAllRooms, useRooms } from '../../store/rooms'
import { DataTable, PageHeader, Td, Th } from './AdminLayout'
import { RoomDialog } from './RoomDialog'

const collator = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' })

/** Ordem da planilha: tipo original (vídeo primeiro), ordem de exibição, nome. */
function adminOrder(a: RoomOut, b: RoomOut): number {
  if (a.room_type !== b.room_type) return a.room_type === 'VIDEO' ? -1 : 1
  if (a.display_order !== b.display_order) return a.display_order - b.display_order
  return collator.compare(a.name, b.name)
}

export function RoomsAdminPage() {
  const rooms = useAllRooms()
  const status = useRooms((s) => s.status)
  const error = useRooms((s) => s.error)
  const [showInactive, setShowInactive] = useState(true)
  const [dialog, setDialog] = useState<{ room: RoomOut | null } | null>(null)

  const list = useMemo(
    () => [...rooms].filter((r) => showInactive || r.is_active).sort(adminOrder),
    [rooms, showInactive],
  )
  const inactiveCount = rooms.filter((r) => !r.is_active).length

  return (
    <>
      <PageHeader
        title="Salas"
        description="Cadastro das salas da grade (RF01). Salas não são apagadas: desative para tirá-las da grade sem perder o histórico."
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setDialog({ room: null })}>
            Nova sala
          </Button>
        }
      />

      {inactiveCount > 0 && (
        <Switch
          checked={showInactive}
          onChange={setShowInactive}
          tone="ink"
          label={`Mostrar salas inativas (${inactiveCount})`}
          className="mb-3 max-w-xs"
        />
      )}

      {status === 'error' ? (
        <EmptyState
          icon={<AlertTriangle className="size-5" />}
          title="Não foi possível carregar as salas"
          description={`${error ?? 'O servidor não respondeu.'} Tentando de novo automaticamente…`}
          action={
            <Button icon={<RefreshCw className="size-4" />} onClick={() => void useRooms.getState().load()}>
              Tentar de novo
            </Button>
          }
        />
      ) : status !== 'ready' ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          icon={<DoorClosed className="size-5" />}
          title="Nenhuma sala cadastrada"
          description="Cadastre as salas de videoconferência e as salas normais na ordem em que aparecem na planilha."
          action={
            <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setDialog({ room: null })}>
              Cadastrar a primeira sala
            </Button>
          }
        />
      ) : (
        <DataTable>
          <thead>
            <tr>
              <Th className="w-14 text-right">Ordem</Th>
              <Th>Nome</Th>
              <Th>Tipo</Th>
              <Th className="text-right">Lugares</Th>
              <Th>Recursos</Th>
              <Th>Vídeo</Th>
              <Th>Situação</Th>
              <Th className="w-20" />
            </tr>
          </thead>
          <tbody>
            {list.map((room) => (
              <tr key={room.id} className={cn('hover:bg-paper/60', !room.is_active && 'text-muted')}>
                <Td className="tabular text-right font-mono text-[12px]">{room.display_order}</Td>
                <Td className="font-medium">{room.name}</Td>
                <Td>
                  {room.room_type === 'VIDEO' ? (
                    <Badge tone="video" icon={<Video className="size-3" aria-hidden="true" />}>
                      Videoconferência
                    </Badge>
                  ) : (
                    <Badge tone="normal">Normal</Badge>
                  )}
                </Td>
                <Td className="tabular text-right font-mono">{room.capacity || '—'}</Td>
                <Td>
                  <div className="flex max-w-[280px] flex-wrap gap-1">
                    {room.resources.length === 0 ? (
                      <span className="text-faint">—</span>
                    ) : (
                      room.resources.map((r) => (
                        <span
                          key={r}
                          className="rounded-[4px] border border-line-strong bg-paper px-1.5 text-[11.5px] text-ink-2"
                        >
                          {r}
                        </span>
                      ))
                    )}
                  </div>
                </Td>
                <Td>
                  {room.room_type !== 'VIDEO' ? (
                    <span className="text-faint">—</span>
                  ) : isInMaintenance(room) ? (
                    <Badge tone="lock" icon={<Wrench className="size-3" aria-hidden="true" />}>
                      Em manutenção{room.maintenance_ticket_id ? ` · ${room.maintenance_ticket_id}` : ''}
                    </Badge>
                  ) : (
                    <Badge tone="success">Operacional</Badge>
                  )}
                </Td>
                <Td>
                  {room.is_active ? (
                    <Badge tone="neutral">Ativa</Badge>
                  ) : (
                    <Badge tone="neutral" className="opacity-70">
                      Inativa
                    </Badge>
                  )}
                </Td>
                <Td className="text-right">
                  <Button
                    size="xs"
                    variant="ghost"
                    icon={<Pencil className="size-3.5" />}
                    onClick={() => setDialog({ room })}
                  >
                    Editar
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}

      <RoomDialog open={dialog !== null} room={dialog?.room ?? null} onClose={() => setDialog(null)} />
    </>
  )
}
