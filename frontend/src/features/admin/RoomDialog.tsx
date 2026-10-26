import { type FormEvent, useRef, useState } from 'react'
import { CalendarClock, ExternalLink } from 'lucide-react'

import { asRoomHasFutureBookings, errorMessage, isApiError } from '../../api/errors'
import { api } from '../../api/endpoints'
import type {
  BookingOut,
  RoomCreate,
  RoomHasFutureBookingsDetail,
  RoomOut,
  RoomType,
  RoomUpdate,
} from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Dialog } from '../../components/ui/Dialog'
import { Field, Input } from '../../components/ui/Form'
import { Segmented } from '../../components/ui/Segmented'
import { Switch } from '../../components/ui/Switch'
import { TagInput } from '../../components/ui/TagInput'
import { isInMaintenance } from '../../lib/rooms'
import { formatDateTime, formatTabLabel } from '../../lib/time'
import { useBookings } from '../../store/bookings'
import { useConfig } from '../../store/config'
import { useRooms } from '../../store/rooms'
import { toast } from '../../store/toasts'

const RESOURCE_SUGGESTIONS = ['TV', 'Projetor', 'Quadro branco', 'Webcam', 'Viva-voz', 'Ar-condicionado']

export interface RoomFormState {
  name: string
  roomType: RoomType
  capacity: string
  displayOrder: string
  resources: string[]
  isActive: boolean
  maintenance: boolean
  maintenanceTicket: string
}

export function roomFormFrom(room: RoomOut | null): RoomFormState {
  return {
    name: room?.name ?? '',
    roomType: room?.room_type ?? 'NORMAL',
    capacity: room ? String(room.capacity) : '',
    displayOrder: room ? String(room.display_order) : '',
    resources: room?.resources ?? [],
    isActive: room?.is_active ?? true,
    maintenance: room ? isInMaintenance(room) : false,
    maintenanceTicket: room?.maintenance_ticket_id ?? '',
  }
}

const sameList = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((v, i) => v === b[i])

/** PATCH só com os campos alterados. */
export function roomPatch(room: RoomOut, form: RoomFormState): RoomUpdate {
  const patch: RoomUpdate = {}
  if (form.name.trim() !== room.name) patch.name = form.name.trim()
  if (form.roomType !== room.room_type) patch.room_type = form.roomType
  const capacity = Number(form.capacity || 0)
  if (capacity !== room.capacity) patch.capacity = capacity
  if (form.displayOrder.trim() !== '' && Number(form.displayOrder) !== room.display_order)
    patch.display_order = Number(form.displayOrder)
  if (!sameList(form.resources, room.resources)) patch.resources = form.resources
  if (form.isActive !== room.is_active) patch.is_active = form.isActive
  return patch
}

type Errors = Partial<Record<'name' | 'capacity' | 'displayOrder' | 'maintenanceTicket', string>>

function validate(form: RoomFormState): Errors {
  const errors: Errors = {}
  if (!form.name.trim()) errors.name = 'Informe o nome da sala.'
  if (form.capacity && !(Number.isInteger(Number(form.capacity)) && Number(form.capacity) >= 0)) {
    errors.capacity = 'Use um número inteiro (0 = não informado).'
  }
  if (form.displayOrder && !Number.isInteger(Number(form.displayOrder))) errors.displayOrder = 'Use um número inteiro.'
  if (form.roomType === 'VIDEO' && form.maintenance && !form.maintenanceTicket.trim()) {
    errors.maintenanceTicket = 'Informe o chamado de TI da manutenção.'
  }
  return errors
}

export function RoomDialog({ open, room, onClose }: { open: boolean; room: RoomOut | null; onClose: () => void }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={room ? `Editar ${room.name}` : 'Nova sala'}
      description={room ? undefined : 'A sala aparece na grade assim que for salva.'}
    >
      {open && <RoomForm room={room} onClose={onClose} />}
    </Dialog>
  )
}

function RoomForm({ room, onClose }: { room: RoomOut | null; onClose: () => void }) {
  const [form, setForm] = useState(() => roomFormFrom(room))
  const [errors, setErrors] = useState<Errors>({})
  const [banner, setBanner] = useState<string | null>(null)
  /** 409 ROOM_HAS_FUTURE_BOOKINGS: reservas que impedem a desativação. */
  const [blocking, setBlocking] = useState<RoomHasFutureBookingsDetail | null>(null)
  const timeZone = useConfig((s) => s.config?.timezone ?? 'America/Sao_Paulo')
  const [saving, setSaving] = useState(false)
  const nameRef = useRef<HTMLInputElement>(null)
  const set = (patch: Partial<RoomFormState>) => setForm((f) => ({ ...f, ...patch }))

  const apply = (updated: RoomOut) => {
    useRooms.getState().upsert(updated)
    useBookings.getState().applyRoom(updated)
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const found = validate(form)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    setSaving(true)
    setBanner(null)
    setBlocking(null)
    try {
      let saved: RoomOut
      if (room) {
        const patch = roomPatch(room, form)
        saved = Object.keys(patch).length > 0 ? await api.rooms.update(room.id, patch) : room
      } else {
        const body: RoomCreate = {
          name: form.name.trim(),
          room_type: form.roomType,
          capacity: Number(form.capacity || 0),
          resources: form.resources,
          display_order: form.displayOrder.trim() ? Number(form.displayOrder) : null,
          is_active: form.isActive,
        }
        saved = await api.rooms.create(body)
      }
      // Manutenção de vídeo tem rotas próprias (PUT/DELETE /maintenance).
      if (saved.room_type === 'VIDEO') {
        const ticket = form.maintenanceTicket.trim()
        if (form.maintenance && (!isInMaintenance(saved) || saved.maintenance_ticket_id !== ticket)) {
          saved = await api.rooms.setMaintenance(saved.id, ticket)
        } else if (!form.maintenance && isInMaintenance(saved)) {
          saved = await api.rooms.clearMaintenance(saved.id)
        }
      }
      apply(saved)
      toast.success(room ? 'Sala atualizada' : 'Sala cadastrada', { description: saved.name })
      onClose()
    } catch (error) {
      const future = asRoomHasFutureBookings(error)
      if (future) {
        // D1: a sala ainda tem reservas ativas por vir; nada foi gravado. A lista abre cada uma na grade.
        setBlocking(future)
      } else if (isApiError(error) && error.code === 'ROOM_NAME_TAKEN') {
        setErrors({ name: error.message || 'Já existe uma sala com este nome.' })
        nameRef.current?.focus()
      } else {
        setBanner(errorMessage(error, 'Não foi possível salvar a sala.'))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {banner && (
        <p
          role="alert"
          className="rounded-md border border-danger/25 bg-danger-tint px-3 py-2 text-[12.5px] text-danger-ink"
        >
          {banner}
        </p>
      )}
      <Field label="Nome" required error={errors.name}>
        {(props) => (
          <Input
            {...props}
            ref={nameRef}
            value={form.name}
            maxLength={100}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="Ex.: Sala 03 — Executiva"
          />
        )}
      </Field>

      <div className="flex flex-col gap-1">
        <span className="text-[12px] font-medium text-ink-2">Tipo</span>
        <Segmented<RoomType>
          label="Tipo de sala"
          value={form.roomType}
          onChange={(roomType) => set({ roomType })}
          options={[
            { value: 'VIDEO', label: 'Videoconferência' },
            { value: 'NORMAL', label: 'Normal' },
          ]}
          className="self-start"
        />
        {room?.room_type === 'VIDEO' && form.roomType === 'NORMAL' && isInMaintenance(room) && (
          <p className="text-[11.5px] text-muted">Mudar para Normal encerra a manutenção de vídeo registrada.</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Capacidade" hint="Lugares (0 = não informado)" error={errors.capacity}>
          {(props) => (
            <Input
              {...props}
              type="number"
              min={0}
              inputMode="numeric"
              value={form.capacity}
              onChange={(e) => set({ capacity: e.target.value })}
            />
          )}
        </Field>
        <Field label="Ordem de exibição" hint="Vazio = fim do grupo" error={errors.displayOrder}>
          {(props) => (
            <Input
              {...props}
              type="number"
              inputMode="numeric"
              value={form.displayOrder}
              onChange={(e) => set({ displayOrder: e.target.value })}
            />
          )}
        </Field>
      </div>

      <Field label="Recursos fixos" hint="Enter ou vírgula para adicionar">
        {(props) => (
          <TagInput
            id={props.id}
            aria-describedby={props['aria-describedby']}
            value={form.resources}
            onChange={(resources) => set({ resources })}
            placeholder="TV, quadro branco…"
            suggestions={RESOURCE_SUGGESTIONS}
          />
        )}
      </Field>

      <Switch
        checked={form.isActive}
        onChange={(isActive) => {
          set({ isActive })
          if (isActive) setBlocking(null)
        }}
        tone="ink"
        label="Sala ativa"
        description={
          form.isActive
            ? 'Aparece na grade'
            : 'Some da grade (o histórico é mantido). Só dá para desativar sem reservas futuras.'
        }
      />
      {blocking && <FutureBookingsNotice detail={blocking} timeZone={timeZone} />}

      {form.roomType === 'VIDEO' && (
        <div className="space-y-2 rounded-lg border border-line bg-paper/60 p-3">
          <Switch
            checked={form.maintenance}
            onChange={(maintenance) => set({ maintenance })}
            tone="danger"
            label="Vídeo em manutenção"
            description="Enquanto ligado, a sala é tratada como Sala Normal na grade."
          />
          {form.maintenance && (
            <Field label="Nº do chamado de TI" required error={errors.maintenanceTicket}>
              {(props) => (
                <Input
                  {...props}
                  value={form.maintenanceTicket}
                  maxLength={50}
                  onChange={(e) => set({ maintenanceTicket: e.target.value })}
                  className="font-mono"
                  placeholder="Ex.: INC0012345"
                />
              )}
            </Field>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2 border-t border-line pt-3">
        <Button onClick={onClose} disabled={saving}>
          Cancelar
        </Button>
        <Button type="submit" variant="primary" loading={saving}>
          {room ? 'Salvar alterações' : 'Cadastrar sala'}
        </Button>
      </div>
    </form>
  )
}

/** Link que abre a reserva na grade (outra aba), no dia dela e destacada. */
export function bookingGridHref(booking: Pick<BookingOut, 'id' | 'date'>): string {
  return `/?date=${booking.date}&highlight=${encodeURIComponent(booking.id)}`
}

/**
 * 409 ROOM_HAS_FUTURE_BOOKINGS (D1): a sala não pode ser desativada enquanto tiver reservas ativas que
 * ainda não terminaram. Lista as reservas (até 50) com link para cada uma na grade, onde podem ser
 * movidas para outra sala ou canceladas; depois é só salvar de novo.
 */
function FutureBookingsNotice({ detail, timeZone }: { detail: RoomHasFutureBookingsDetail; timeZone: string }) {
  const hidden = detail.count - detail.bookings.length
  return (
    <section
      role="alert"
      aria-label="Reservas que impedem a desativação"
      className="rounded-lg border border-warning/30 bg-warning-tint px-3 py-2.5 text-[12.5px] text-ink-2"
    >
      <p className="flex items-start gap-2 font-semibold text-warning">
        <CalendarClock className="mt-px size-4 shrink-0" aria-hidden="true" />
        {detail.message ||
          `A sala tem ${detail.count} reserva${detail.count === 1 ? '' : 's'} ativa${detail.count === 1 ? '' : 's'} por vir.`}
      </p>
      <p className="mt-1 text-[12px] text-muted">
        Nada foi salvo. Mova essas reservas para outra sala (ou cancele) e salve de novo. Cada link abre a reserva na
        grade, em outra aba.
      </p>
      <ul className="scroll-thin mt-2 max-h-56 space-y-1 overflow-y-auto pr-1">
        {detail.bookings.map((booking) => (
          <li
            key={booking.id}
            className="flex items-center gap-2 rounded-md border border-line bg-surface px-2 py-1.5"
            data-booking-row={booking.id}
          >
            <span className="tabular shrink-0 font-mono text-[12px] text-ink">
              {formatTabLabel(booking.date)} · {booking.start}–{booking.end}
            </span>
            <span className="min-w-0 flex-1 truncate">
              <span className="font-medium text-ink">{booking.requester_name}</span>
              {booking.ticket_number && (
                <span className="ml-1.5 font-mono text-[12px] text-muted">#{booking.ticket_number}</span>
              )}
            </span>
            <a
              href={bookingGridHref(booking)}
              target="_blank"
              rel="noopener noreferrer"
              title={`Criada por ${booking.created_by.full_name} em ${formatDateTime(booking.created_at, timeZone)}`}
              className="inline-flex shrink-0 items-center gap-1 font-medium text-video hover:underline"
            >
              Abrir na grade
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <p className="mt-1.5 text-[12px] text-muted">
          Mostrando {detail.bookings.length} de {detail.count} reservas (as mais próximas primeiro).
        </p>
      )}
    </section>
  )
}
