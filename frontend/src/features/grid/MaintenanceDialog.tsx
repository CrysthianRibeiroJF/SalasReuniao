import { type FormEvent, useRef, useState } from 'react'
import { Wrench } from 'lucide-react'

import { errorMessage } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { RoomOut } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Dialog } from '../../components/ui/Dialog'
import { Field, Input } from '../../components/ui/Form'
import { isInMaintenance } from '../../lib/rooms'
import { useBookings } from '../../store/bookings'
import { useRooms } from '../../store/rooms'
import { toast } from '../../store/toasts'

/** Marca/desmarca "Vídeo em manutenção" (RF02) — disponível para qualquer usuário logado. */
export function MaintenanceDialog({ room, onClose }: { room: RoomOut | null; onClose: () => void }) {
  return (
    <Dialog
      open={room !== null}
      onClose={onClose}
      size="sm"
      title={room && isInMaintenance(room) ? 'Vídeo voltou a funcionar?' : 'Vídeo em manutenção'}
      description={room?.name}
    >
      {room && <MaintenanceForm room={room} onClose={onClose} />}
    </Dialog>
  )
}

function MaintenanceForm({ room, onClose }: { room: RoomOut; onClose: () => void }) {
  const inMaintenance = isInMaintenance(room)
  const [ticket, setTicket] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const apply = (updated: RoomOut) => {
    useRooms.getState().upsert(updated)
    useBookings.getState().applyRoom(updated)
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!inMaintenance && !ticket.trim()) {
      setError('Informe o número do chamado de TI.')
      inputRef.current?.focus()
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (inMaintenance) {
        apply(await api.rooms.clearMaintenance(room.id))
        toast.success(`${room.name} voltou para Videoconferência`)
      } else {
        apply(await api.rooms.setMaintenance(room.id, ticket.trim()))
        toast.success(`${room.name}: vídeo em manutenção`, {
          description: 'A sala passa a aparecer entre as Salas Normais até o conserto.',
        })
      }
      onClose()
    } catch (err) {
      setError(errorMessage(err))
      setSaving(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {inMaintenance ? (
        <p className="text-[13px] text-ink-2">
          O vídeo está em manutenção
          {room.maintenance_ticket_id ? ` pelo chamado ${room.maintenance_ticket_id}` : ''}. Ao confirmar, a sala volta
          para o bloco de Videoconferência.
        </p>
      ) : (
        <>
          <p className="text-[13px] text-ink-2">
            Enquanto o vídeo estiver com defeito, a sala é tratada como Sala Normal na grade. Reuniões que precisam de
            vídeo nela recebem o selo “Precisa de vídeo”.
          </p>
          <Field label="Nº do chamado de TI" required error={error}>
            {(props) => (
              <Input
                {...props}
                ref={inputRef}
                autoFocus
                maxLength={50}
                value={ticket}
                onChange={(e) => setTicket(e.target.value)}
                placeholder="Ex.: INC0012345"
                className="font-mono"
              />
            )}
          </Field>
        </>
      )}
      {inMaintenance && error && <p className="text-[12.5px] text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose} disabled={saving}>
          Voltar
        </Button>
        <Button type="submit" variant="primary" loading={saving} icon={<Wrench className="size-4" />}>
          {inMaintenance ? 'Encerrar manutenção' : 'Marcar manutenção'}
        </Button>
      </div>
    </form>
  )
}
