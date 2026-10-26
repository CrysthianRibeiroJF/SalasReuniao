import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useId, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronRight, Lightbulb, Lock, Users, Video, WifiOff, X } from 'lucide-react'

import { asConflict, asSlotLocked, asStaleVersion, errorMessage, isApiError } from '../../../api/errors'
import { api } from '../../../api/endpoints'
import type {
  BookingConflictDetail,
  BookingCreate,
  BookingOut,
  BookingUpdate,
  BookingWriteResult,
  HHMM,
  ISODate,
  LockOut,
  PeriodType,
  RoomBrief,
  RoomOut,
} from '../../../api/types'
import { Button } from '../../../components/ui/Button'
import { FloatingPanel } from '../../../components/ui/FloatingPanel'
import { Field, Input, Select, Textarea } from '../../../components/ui/Form'
import { Kbd } from '../../../components/ui/Kbd'
import { Segmented } from '../../../components/ui/Segmented'
import { Switch } from '../../../components/ui/Switch'
import { useDismiss } from '../../../hooks/useDismiss'
import { useFocusTrap } from '../../../hooks/useFocusTrap'
import { useLatest } from '../../../hooks/useLatest'
import { cn } from '../../../lib/cn'
import { dayWindow, slotTimeOptions } from '../../../lib/grid'
import { PERIOD_OPTIONS, periodFromShortcut, resolvePeriod, type TimeRange } from '../../../lib/periods'
import { fitsAttendees, groupRooms, preferFitting } from '../../../lib/rooms'
import { formatDuration, formatTabLabel, fromMinutes, isValidISODate, toMinutes } from '../../../lib/time'
import { useBooking, useBookings } from '../../../store/bookings'
import { useLoadedConfig } from '../../../store/config'
import { useRealtime } from '../../../store/realtime'
import { useGridRooms, useRooms } from '../../../store/rooms'
import { toast } from '../../../store/toasts'
import { type ComposerState, useUi } from '../../../store/ui'
import { showWarnings, STALE_MESSAGE } from '../bookingActions'
import { hasOpenModal, isEditableTarget } from '../useGridKeyboard'
import { ConflictPanel } from './ConflictPanel'
import { roomOptionLabel, SeatsSuffix } from './SeatsSuffix'
import { useComposerLock } from './useComposerLock'
import { useFreeRooms } from './useFreeRooms'

// ------------------------------------------------------------------ formulário (puro)

export interface ComposerForm {
  roomId: number
  date: ISODate
  period: PeriodType
  /** Início/fim do Horário Específico (os presets não sobrescrevem; voltar para Específico os recupera). */
  start: HHMM
  end: HHMM
  ticket: string
  requester: string
  title: string
  requiresVideo: boolean
  attendees: string
  notes: string
}

type FieldName = 'ticket' | 'requester' | 'title' | 'attendees' | 'notes' | 'end'

export function initialForm(composer: ComposerState, booking?: BookingOut): ComposerForm {
  if (composer.mode === 'edit' && booking) {
    return {
      roomId: booking.room_id,
      date: booking.date,
      period: booking.period_type,
      start: booking.start,
      end: booking.end,
      ticket: booking.ticket_number ?? '',
      requester: booking.requester_name,
      title: booking.title ?? '',
      requiresVideo: booking.requires_video,
      attendees: booking.attendees ? String(booking.attendees) : '',
      notes: booking.notes ?? '',
    }
  }
  return {
    roomId: composer.roomId,
    date: composer.date,
    period: composer.period,
    start: composer.start,
    end: composer.end,
    ticket: '',
    requester: '',
    title: '',
    requiresVideo: false,
    attendees: '',
    notes: '',
  }
}

const textOrNull = (value: string) => value.trim() || null
const numberOrNull = (value: string) => (value.trim() ? Number(value) : null)

export function validateForm(form: ComposerForm, range: TimeRange): Partial<Record<FieldName, string>> {
  const errors: Partial<Record<FieldName, string>> = {}
  if (!form.requester.trim()) errors.requester = 'Informe o solicitante.'
  if (form.attendees.trim() && !(Number.isInteger(Number(form.attendees)) && Number(form.attendees) > 0)) {
    errors.attendees = 'Use um número inteiro maior que zero.'
  }
  if (toMinutes(range.end) <= toMinutes(range.start)) errors.end = 'O fim deve ser depois do início.'
  return errors
}

export function createPayload(form: ComposerForm, range: TimeRange, lockId: string | null): BookingCreate {
  return {
    room_id: form.roomId,
    date: form.date,
    period_type: form.period,
    ...(form.period === 'CUSTOM' ? { start: range.start, end: range.end } : {}),
    ticket_number: textOrNull(form.ticket),
    title: textOrNull(form.title),
    requester_name: form.requester.trim(),
    requires_video: form.requiresVideo,
    attendees: numberOrNull(form.attendees),
    notes: textOrNull(form.notes),
    lock_id: lockId,
  }
}

/** PATCH só com o que mudou (+ version e lock_id). */
export function updatePayload(
  original: BookingOut,
  form: ComposerForm,
  range: TimeRange,
  version: number,
  lockId: string | null,
): BookingUpdate {
  const patch: BookingUpdate = { version }
  if (form.roomId !== original.room_id) patch.room_id = form.roomId
  if (form.date !== original.date) patch.date = form.date
  if (form.period !== original.period_type) patch.period_type = form.period
  if (
    form.period === 'CUSTOM' &&
    (range.start !== original.start || range.end !== original.end || form.period !== original.period_type)
  ) {
    patch.period_type = 'CUSTOM'
    patch.start = range.start
    patch.end = range.end
  }
  if (textOrNull(form.ticket) !== original.ticket_number) patch.ticket_number = textOrNull(form.ticket)
  if (textOrNull(form.title) !== original.title) patch.title = textOrNull(form.title)
  if (form.requester.trim() !== original.requester_name) patch.requester_name = form.requester.trim()
  if (form.requiresVideo !== original.requires_video) patch.requires_video = form.requiresVideo
  if (numberOrNull(form.attendees) !== original.attendees) patch.attendees = numberOrNull(form.attendees)
  if (textOrNull(form.notes) !== original.notes) patch.notes = textOrNull(form.notes)
  if (lockId && Object.keys(patch).length > 1) patch.lock_id = lockId
  return patch
}

/**
 * Edição concorrente (STALE_VERSION): `form` foi montado sobre `before`, mas o servidor já está em
 * `now`. Campos que a pessoa não alterou passam para o valor novo; os que ela alterou ficam.
 */
export function rebaseForm(form: ComposerForm, before: ComposerForm, now: ComposerForm): ComposerForm {
  const keys = Object.keys(form) as (keyof ComposerForm)[]
  return Object.fromEntries(keys.map((k) => [k, form[k] === before[k] ? now[k] : form[k]])) as unknown as ComposerForm
}

const FIELD_FROM_API: Record<string, FieldName> = {
  ticket_number: 'ticket',
  requester_name: 'requester',
  title: 'title',
  attendees: 'attendees',
  notes: 'notes',
  start: 'end',
  end: 'end',
}

// ------------------------------------------------------------------ componente

export function BookingComposerHost() {
  const composer = useUi((s) => s.composer)
  if (!composer) return null
  return <BookingComposer key={composer.key} composer={composer} />
}

interface SaveError {
  message: string
  locks?: LockOut[]
}

/** O que os atalhos do modal leem de um evento de teclado (do React ou do documento). */
interface ComposerKey {
  key: string
  code: string
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  target: EventTarget | null
  isComposing: boolean
  preventDefault: () => void
  stopPropagation: () => void
}

/**
 * Clique fora fecha o modal, mas dentro da grade só um clique em célula ou reserva (que começa outra
 * seleção) fecha: barra de rolagem, cabeçalhos, coluna de horários e travas alheias não descartam o
 * que foi digitado (R16).
 */
export function composerDismissesOn(event: PointerEvent): boolean {
  const target = event.target instanceof Element ? event.target : null
  if (!target) return true
  if (!target.closest('[role="grid"]')) return true
  return target.closest('[data-cell], [data-booking-id]') !== null
}

type ConflictState = Pick<BookingConflictDetail, 'message' | 'conflicts' | 'alternatives'>

export function BookingComposer({ composer }: { composer: ComposerState }) {
  const config = useLoadedConfig()
  const w = useMemo(() => dayWindow(config), [config])
  const timeOptions = useMemo(() => slotTimeOptions(w), [w])
  const rooms = useGridRooms()
  const live = useBooking(composer.bookingId)
  // Versão do servidor sobre a qual o formulário foi montado: o PATCH leva a `version` dela e só os
  // campos que a pessoa mudou em relação a ela. Fica fixa mesmo que a reserva mude ou saia do estado
  // local durante a edição (nunca vira uma criação por engano); depois de um STALE_VERSION passa a
  // ser a versão atual (ver rebaseForm).
  const [base, setBase] = useState(live)
  const original = base ?? live
  const editing = composer.mode === 'edit' && original !== undefined
  const realtimeStatus = useRealtime((s) => s.status)

  const [form, setForm] = useState<ComposerForm>(() => initialForm(composer, original))
  const [showMore, setShowMore] = useState(Boolean(original?.notes || original?.attendees))
  const [saving, setSaving] = useState(false)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [error, setError] = useState<SaveError | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [conflict, setConflict] = useState<ConflictState | null>(null)

  const panelRef = useRef<HTMLDivElement>(null)
  const ticketRef = useRef<HTMLInputElement>(null)
  const requesterRef = useRef<HTMLInputElement>(null)
  const feedbackRef = useRef<HTMLDivElement>(null)
  const lastFocused = useRef<HTMLElement | null>(null)
  const titleId = useId()

  // A sala atual pode estar desativada (reserva antiga): ela continua identificada no modal, mas só as
  // ativas são oferecidas para trocar.
  const room: RoomOut | undefined = useRooms((s) => s.byId[form.roomId])
  const range = resolvePeriod(form.period, config, { start: form.start, end: form.end })
  const duration = toMinutes(range.end) - toMinutes(range.start)

  // A grade mostra a prévia da seleção a partir do estado do modal.
  useEffect(() => {
    useUi
      .getState()
      .patchComposer({ roomId: form.roomId, date: form.date, period: form.period, start: range.start, end: range.end })
  }, [form.roomId, form.date, form.period, range.start, range.end])

  const slotChanged =
    !editing ||
    original.room_id !== form.roomId ||
    original.date !== form.date ||
    original.start !== range.start ||
    original.end !== range.end
  const lock = useComposerLock({
    target: { roomId: form.roomId, date: form.date, start: range.start, end: range.end, bookingId: composer.bookingId },
    initialLockId: composer.lockId,
    enabled: slotChanged,
  })

  const excludeId = editing ? original.id : undefined
  const isVideoRoom = room?.effective_type === 'VIDEO'
  const { rooms: freeNormal } = useFreeRooms(
    isVideoRoom && !form.requiresVideo
      ? { date: form.date, start: range.start, end: range.end, room_type: 'NORMAL', exclude_booking_id: excludeId }
      : null,
    form.roomId,
  )
  const needsVideoRoom = form.requiresVideo && room !== undefined && !isVideoRoom
  const { rooms: freeVideo } = useFreeRooms(
    needsVideoRoom
      ? { date: form.date, start: range.start, end: range.end, room_type: 'VIDEO', exclude_booking_id: excludeId }
      : null,
    form.roomId,
  )
  const attendees = Number(form.attendees)
  const overCapacity =
    room !== undefined && room.capacity > 0 && Number.isInteger(attendees) && attendees > room.capacity
  /** Participantes informados (para ordenar as salas sugeridas pela capacidade); null = não informado. */
  const attendeesCount = Number.isInteger(attendees) && attendees > 0 ? attendees : null

  const close = () => useUi.getState().closeComposer()

  useFocusTrap(panelRef)
  useDismiss(panelRef, close, { escape: false, enabled: !saving, shouldDismiss: composerDismissesOn })

  useEffect(() => {
    ticketRef.current?.focus({ preventScroll: true })
  }, [])

  // O botão clicado fica desabilitado enquanto salva e o navegador tira o foco dele (vai para o
  // <body>): sem devolver o foco ao modal, Esc, Enter e Tab deixariam de funcionar depois de um erro.
  const busy = saving || busyKey !== null
  useEffect(() => {
    if (busy) return
    const active = document.activeElement
    if (active && active !== document.body) return
    const previous = lastFocused.current
    const target =
      previous?.isConnected && !previous.matches(':disabled')
        ? previous
        : (feedbackRef.current?.querySelector<HTMLElement>('button:not(:disabled)') ?? ticketRef.current)
    target?.focus({ preventScroll: true })
  }, [busy])

  const rememberFocus = () => {
    const active = document.activeElement
    if (active instanceof HTMLElement && panelRef.current?.contains(active)) lastFocused.current = active
  }

  // Erro ou painel de conflito aparecem no fim do formulário: traz para a vista.
  useEffect(() => {
    if (error || conflict) feedbackRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [error, conflict])

  const update = (patch: Partial<ComposerForm>) => {
    setForm((f) => ({ ...f, ...patch }))
    if (
      patch.roomId !== undefined ||
      patch.period !== undefined ||
      patch.start !== undefined ||
      patch.end !== undefined ||
      patch.date !== undefined
    ) {
      setConflict(null)
      setError(null)
    }
  }

  const setStart = (start: HHMM) => {
    // Mantém a duração ao mudar o início (limitada ao fechamento).
    const length = Math.max(w.slot, toMinutes(form.end) - toMinutes(form.start))
    update({ start, end: fromMinutes(Math.min(w.endMin, toMinutes(start) + length)) })
  }

  /** Grava a reserva; true se gravou (o modal fecha). */
  const save = async (override: Partial<ComposerForm> = {}): Promise<boolean> => {
    const next = { ...form, ...override }
    const nextRange = resolvePeriod(next.period, config, { start: next.start, end: next.end })
    const errors = validateForm(next, nextRange)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) {
      if (errors.requester) requesterRef.current?.focus()
      if (errors.attendees) setShowMore(true)
      return false
    }
    if (editing && Object.keys(updatePayload(original, next, nextRange, original.version, null)).length === 1) {
      close()
      return true
    }
    rememberFocus()
    setSaving(true)
    setError(null)
    setConflict(null)
    const sentLockId = lock.beginSave()
    try {
      const result: BookingWriteResult = editing
        ? await api.bookings.update(original.id, updatePayload(original, next, nextRange, original.version, sentLockId))
        : await api.bookings.create(createPayload(next, nextRange, sentLockId))
      lock.endSave(true, sentLockId)
      useBookings.getState().upsert(result.booking)
      // Só fecha se ainda for este modal (um clique em outra célula durante a gravação abre outro: R39).
      if (useUi.getState().composer?.key === composer.key) close()
      const b = result.booking
      toast.success(editing ? 'Reserva atualizada' : 'Reserva criada', {
        description: `${b.room_name} · ${formatTabLabel(b.date)} · ${b.start}–${b.end} · ${b.requester_name}`,
      })
      showWarnings(result.warnings)
      return true
    } catch (err) {
      lock.endSave(false, sentLockId)
      handleSaveError(err)
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleSaveError = (err: unknown) => {
    const conflictDetail = asConflict(err)
    if (conflictDetail) {
      setConflict(conflictDetail)
      return
    }
    const locked = asSlotLocked(err)
    if (locked) {
      setError({ message: locked.message || 'Outra pessoa está reservando este horário agora.', locks: locked.locks })
      return
    }
    const stale = asStaleVersion(err)
    if (stale) {
      useBookings.getState().upsert(stale.current)
      // Traz as mudanças da outra pessoa para os campos que não foram mexidos aqui; salvar de novo
      // envia só as alterações desta pessoa, sem desfazer as da outra.
      if (original) {
        const before = initialForm(composer, original)
        const now = initialForm(composer, stale.current)
        setForm((f) => rebaseForm(f, before, now))
      }
      setBase(stale.current)
      toast.warning(STALE_MESSAGE)
      setError({
        message: `${STALE_MESSAGE}. O formulário já mostra a versão nova, com as suas alterações mantidas; confira e salve de novo.`,
      })
      return
    }
    if (isApiError(err) && err.fieldErrors.length > 0) {
      const mapped: Partial<Record<FieldName, string>> = {}
      for (const fe of err.fieldErrors) {
        const name = fe.field ? FIELD_FROM_API[fe.field] : undefined
        if (name) mapped[name] = fe.message
      }
      setFieldErrors(mapped)
    }
    setError({ message: errorMessage(err, 'Não foi possível salvar a reserva.') })
  }

  const pickAlternative = async (alt: RoomBrief) => {
    rememberFocus()
    setBusyKey(`use-${alt.id}`)
    update({ roomId: alt.id })
    await save({ roomId: alt.id })
    setBusyKey(null)
  }

  const relocate = async (booking: BookingOut, target: RoomOut) => {
    rememberFocus()
    setBusyKey(`move-${booking.id}-${target.id}`)
    try {
      let moved: BookingWriteResult
      try {
        moved = await api.bookings.update(booking.id, { version: booking.version, room_id: target.id })
      } catch (err) {
        const stale = asStaleVersion(err)
        if (stale) useBookings.getState().upsert(stale.current)
        const message = stale
          ? 'A reunião conflitante foi alterada por outra pessoa. Confira as opções atualizadas.'
          : errorMessage(err, 'Não foi possível remanejar a reunião conflitante.')
        // Tenta salvar de novo para o painel mostrar a situação atual do servidor (R27).
        if (!(await save())) setError({ message })
        return
      }
      useBookings.getState().upsert(moved.booking)
      toast.success(`Reunião de ${booking.requester_name} movida para ${target.name}`)
      // Capacidade/vídeo da sala de destino: os avisos do servidor não podem se perder (R12).
      showWarnings(moved.warnings, `Reunião de ${booking.requester_name}`)
      await save()
    } finally {
      setBusyKey(null)
    }
  }

  const handleKey = (event: ComposerKey) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      if (!saving) close()
      return
    }
    const period = periodFromShortcut(event)
    if (period) {
      event.preventDefault()
      update({ period })
      return
    }
    if (event.key !== 'Enter' || event.isComposing) return
    const target = event.target instanceof HTMLElement ? event.target : null
    const tag = target?.tagName
    if (tag === 'TEXTAREA' && !(event.ctrlKey || event.metaKey)) return
    // Enter num campo, ou com o foco no próprio painel (clique no título/espaços): salva. Num botão, é do botão.
    const onPanel = !target || target === panelRef.current || target === document.body
    const submit = event.ctrlKey || event.metaKey || tag === 'INPUT' || tag === 'SELECT' || onPanel
    if (submit) {
      event.preventDefault()
      if (!saving) void save()
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) =>
    handleKey({
      key: event.key,
      code: event.code,
      altKey: event.altKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      shiftKey: event.shiftKey,
      target: event.target,
      isComposing: event.nativeEvent.isComposing,
      preventDefault: () => event.preventDefault(),
      stopPropagation: () => event.stopPropagation(),
    })

  // Foco fora do modal mas sem dono (o <body>, depois de um botão ficar desabilitado; ou a grade, depois
  // de arrastar a barra de rolagem dela): Esc, Enter e Alt+1..4 continuam valendo para o modal, como no
  // Dialog (R36/R16). Campos de outras áreas (ex.: busca) e diálogos abertos por cima têm prioridade.
  const keyHandler = useLatest(handleKey)
  useEffect(() => {
    const onDocumentKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || hasOpenModal()) return
      const active = document.activeElement
      if (active && panelRef.current?.contains(active)) return // o onKeyDown do painel já trata
      if (active && active !== document.body && (isEditableTarget(active) || !active.closest('[role="grid"]'))) return
      keyHandler.current(event)
    }
    document.addEventListener('keydown', onDocumentKeyDown)
    return () => document.removeEventListener('keydown', onDocumentKeyDown)
  }, [keyHandler])

  // Um clique fora que não fecha o modal (barra de rolagem, cabeçalho da grade) não tira o cursor do
  // campo em que se estava digitando: ao soltar o botão, o foco volta para ele (R16).
  useEffect(() => {
    let restore: HTMLElement | null = null
    const onPointerDown = (event: PointerEvent) => {
      const panel = panelRef.current
      const active = document.activeElement
      restore =
        panel && !panel.contains(event.target as Node) && active instanceof HTMLElement && panel.contains(active)
          ? active
          : null
    }
    const onPointerUp = () => {
      const target = restore
      restore = null
      if (target?.isConnected && document.activeElement !== target && !isEditableTarget(document.activeElement)) {
        target.focus({ preventScroll: true })
      }
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('pointerup', onPointerUp, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('pointerup', onPointerUp, true)
    }
  }, [])

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!saving) void save()
  }

  const groups = groupRooms(rooms)
  const kind = isVideoRoom ? 'video' : 'normal'
  const showTimes = form.period === 'CUSTOM'
  const startOptions = timeOptions.starts.includes(form.start)
    ? timeOptions.starts
    : [form.start, ...timeOptions.starts].sort()
  const endOptions = (
    timeOptions.ends.includes(form.end) ? timeOptions.ends : [...timeOptions.ends, form.end].sort()
  ).filter((t) => toMinutes(t) > toMinutes(form.start))

  return (
    <FloatingPanel
      ref={panelRef}
      anchor={composer.anchor}
      role="dialog"
      aria-modal={false}
      aria-labelledby={titleId}
      // Focável: um clique no título ou nos espaços deixa o foco no modal (os atalhos continuam valendo).
      tabIndex={-1}
      onKeyDown={onKeyDown}
      className="w-[432px] max-w-[calc(100vw-20px)] outline-none"
    >
      <form onSubmit={onSubmit} noValidate className="flex max-h-[calc(100dvh-20px)] flex-col">
        <header
          className={cn(
            'rounded-t-lg border-b px-4 pt-3 pb-2.5',
            kind === 'video' ? 'border-video-soft bg-video-tint/70' : 'border-normal-soft bg-normal-tint/70',
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <h2 id={titleId} className="text-[14.5px] font-semibold text-ink">
              {editing ? 'Editar reserva' : 'Nova reserva'}
            </h2>
            <button
              type="button"
              onClick={close}
              aria-label="Fechar sem salvar"
              className="-mr-1 rounded p-1 text-muted hover:bg-ink/[0.07] hover:text-ink"
            >
              <X className="size-4" />
            </button>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-ink-2">
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-[4px] px-1.5 py-px font-condensed text-[13px] font-semibold text-white',
                kind === 'video' ? 'bg-video' : 'bg-normal',
              )}
            >
              {kind === 'video' && <Video className="size-3.5" aria-hidden="true" />}
              {room?.name ?? 'Sala'}
            </span>
            <span className="font-medium">{formatTabLabel(form.date)}</span>
            <span className="tabular font-mono">
              {range.start}–{range.end}
            </span>
            <span className="text-muted">· {formatDuration(duration)}</span>
          </p>
        </header>

        <div className="scroll-thin flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-3">
          {editing && (
            <div className="grid grid-cols-[1fr_150px] gap-2.5">
              <Field label="Sala">
                {(props) => (
                  <Select {...props} value={form.roomId} onChange={(e) => update({ roomId: Number(e.target.value) })}>
                    {groups.video.length > 0 && (
                      <optgroup label="Videoconferência">
                        {groups.video.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    <optgroup label="Salas Normais">
                      {groups.normal.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </optgroup>
                    {room && !room.is_active && (
                      <optgroup label="Sala desativada">
                        <option value={room.id}>{room.name} (desativada)</option>
                      </optgroup>
                    )}
                  </Select>
                )}
              </Field>
              <Field label="Data">
                {(props) => (
                  <Input
                    {...props}
                    type="date"
                    value={form.date}
                    onChange={(e) => isValidISODate(e.target.value) && update({ date: e.target.value })}
                  />
                )}
              </Field>
            </div>
          )}

          <div className="grid grid-cols-[150px_1fr] gap-2.5">
            <Field label="Nº do Chamado" error={fieldErrors.ticket}>
              {(props) => (
                <Input
                  {...props}
                  ref={ticketRef}
                  value={form.ticket}
                  maxLength={50}
                  inputMode="text"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Ex.: 123456"
                  onChange={(e) => update({ ticket: e.target.value })}
                  className="font-mono"
                />
              )}
            </Field>
            <Field label="Solicitante" required error={fieldErrors.requester}>
              {(props) => (
                <Input
                  {...props}
                  ref={requesterRef}
                  value={form.requester}
                  maxLength={150}
                  autoComplete="off"
                  placeholder="Quem pediu a sala"
                  onChange={(e) => update({ requester: e.target.value })}
                />
              )}
            </Field>
          </div>

          <Field label="Assunto/Área" error={fieldErrors.title}>
            {(props) => (
              <Input
                {...props}
                value={form.title}
                maxLength={200}
                autoComplete="off"
                placeholder="Ex.: Comitê de Crédito · Diretoria Financeira"
                onChange={(e) => update({ title: e.target.value })}
              />
            )}
          </Field>

          <div className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between">
              <span className="text-[12px] font-medium text-ink-2" id={`${titleId}-period`}>
                Tipo de Período
              </span>
              <span className="flex items-center gap-1 text-[11px] text-muted">
                <Kbd>Alt</Kbd>+<Kbd>1</Kbd>…<Kbd>4</Kbd>
              </span>
            </div>
            <Segmented<PeriodType>
              label="Tipo de Período"
              value={form.period}
              onChange={(period) => update({ period })}
              className="w-full"
              itemClassName="flex-1"
              options={PERIOD_OPTIONS.map((o) => ({
                value: o.type,
                label: o.label,
                title: `${o.label} (Alt+${o.shortcut})`,
              }))}
            />
          </div>

          {showTimes ? (
            <div className="grid grid-cols-2 gap-2.5">
              <Field label="Início">
                {(props) => (
                  <Select
                    {...props}
                    value={form.start}
                    onChange={(e) => setStart(e.target.value)}
                    className="font-mono"
                  >
                    {startOptions.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Fim" error={fieldErrors.end}>
                {(props) => (
                  <Select
                    {...props}
                    value={form.end}
                    onChange={(e) => update({ end: e.target.value })}
                    className="font-mono"
                  >
                    {endOptions.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>
          ) : (
            <p className="tabular rounded-md bg-paper px-2.5 py-1.5 font-mono text-[12px] text-ink-2">
              {range.start}–{range.end} <span className="font-sans text-muted">· horário fixo do período</span>
            </p>
          )}

          <Switch
            checked={form.requiresVideo}
            onChange={(requiresVideo) => update({ requiresVideo })}
            label="Precisa de Videoconferência?"
            description={
              form.requiresVideo ? 'Sim — a reunião usará o equipamento de vídeo' : 'Não — reunião presencial'
            }
          />

          {isVideoRoom && !form.requiresVideo && freeNormal.length > 0 && (
            <Notice tone="info" icon={<Lightbulb className="size-4" />}>
              <p>
                Esta é uma sala de videoconferência e a reunião não precisa de vídeo. Há salas normais livres neste
                horário:
              </p>
              <RoomButtons rooms={freeNormal} attendees={attendeesCount} onPick={(r) => update({ roomId: r.id })} />
            </Notice>
          )}

          {needsVideoRoom && (
            <Notice tone="danger" icon={<AlertTriangle className="size-4" />}>
              <p>{room?.name} não tem videoconferência funcionando agora.</p>
              {freeVideo.length > 0 ? (
                <RoomButtons rooms={freeVideo} attendees={attendeesCount} onPick={(r) => update({ roomId: r.id })} />
              ) : (
                <p className="text-muted">Nenhuma sala de vídeo livre neste horário.</p>
              )}
            </Notice>
          )}

          {overCapacity && (
            <Notice tone="warning" icon={<Users className="size-4" />}>
              {room.name} comporta {room.capacity} pessoas; foram informados {attendees} participantes.
            </Notice>
          )}

          {lock.denied && (
            <Notice tone="warning" icon={<Lock className="size-4" />}>
              {lock.denied.message || 'Outra pessoa está reservando parte deste horário agora.'}
            </Notice>
          )}

          <div>
            <button
              type="button"
              aria-expanded={showMore}
              onClick={() => setShowMore((v) => !v)}
              className="flex items-center gap-1 text-[12.5px] font-medium text-ink-2 hover:text-ink"
            >
              <ChevronRight
                className={cn('size-3.5 transition-transform', showMore && 'rotate-90')}
                aria-hidden="true"
              />
              Mais detalhes
              {!showMore && (form.attendees || form.notes) && (
                <span className="size-1.5 rounded-full bg-video" aria-label="preenchido" />
              )}
            </button>
            {showMore && (
              <div className="mt-2 grid grid-cols-[110px_1fr] gap-2.5">
                <Field label="Participantes" error={fieldErrors.attendees}>
                  {(props) => (
                    <Input
                      {...props}
                      type="number"
                      min={1}
                      inputMode="numeric"
                      value={form.attendees}
                      onChange={(e) => update({ attendees: e.target.value })}
                    />
                  )}
                </Field>
                <Field label="Observações" hint="Ctrl+Enter salva" error={fieldErrors.notes}>
                  {(props) => (
                    <Textarea
                      {...props}
                      rows={2}
                      maxLength={2000}
                      value={form.notes}
                      onChange={(e) => update({ notes: e.target.value })}
                      className="resize-none"
                    />
                  )}
                </Field>
              </div>
            )}
          </div>

          <div ref={feedbackRef} className="flex flex-col gap-3 empty:hidden">
            {error && (
              <div
                role="alert"
                className="rounded-md border border-danger/25 bg-danger-tint px-3 py-2 text-[12.5px] text-danger-ink"
              >
                <p>{error.message}</p>
                {error.locks && error.locks.length > 0 && (
                  <ul className="mt-1 text-[12px]">
                    {error.locks.map((l) => (
                      <li key={l.lock_id}>
                        {l.user.full_name}: {l.start}–{l.end}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {conflict && (
              <ConflictPanel
                conflict={conflict}
                wholeDay={form.period === 'FULL_DAY'}
                requiresVideo={form.requiresVideo}
                attendees={attendeesCount}
                currentRoomId={form.roomId}
                date={form.date}
                range={range}
                ownBookingId={editing ? original.id : null}
                busyKey={busyKey}
                onUseRoom={(alt) => void pickAlternative(alt)}
                onRelocate={(booking, target) => void relocate(booking, target)}
              />
            )}
          </div>
        </div>

        <footer className="flex items-center gap-2 rounded-b-lg border-t border-line bg-paper/70 px-4 py-2.5">
          <div className="flex min-w-0 flex-1 items-center gap-2 text-[11px] text-muted">
            {realtimeStatus !== 'open' ? (
              <span
                className="flex items-center gap-1 text-warning"
                title="As colegas não verão esta seleção até a conexão voltar."
              >
                <WifiOff className="size-3.5" aria-hidden="true" /> Sem tempo real
              </span>
            ) : (
              <>
                <span className="flex items-center gap-1">
                  <Kbd>Enter</Kbd> salvar
                </span>
                <span className="flex items-center gap-1">
                  <Kbd>Esc</Kbd> cancelar
                </span>
              </>
            )}
          </div>
          <Button size="sm" onClick={close} disabled={saving}>
            Cancelar
          </Button>
          <Button type="submit" size="sm" variant="primary" loading={saving}>
            {editing ? 'Salvar alterações' : 'Salvar'}
          </Button>
        </footer>
      </form>
    </FloatingPanel>
  )
}

function Notice({
  tone,
  icon,
  children,
}: {
  tone: 'info' | 'warning' | 'danger'
  icon: ReactNode
  children: ReactNode
}) {
  const tones = {
    info: 'border-video-soft bg-video-tint/60 text-video-ink',
    warning: 'border-warning/30 bg-warning-tint text-warning',
    danger: 'border-danger/25 bg-danger-tint text-danger-ink',
  }
  return (
    <div
      role="status"
      className={cn('flex gap-2 rounded-md border px-2.5 py-2 text-[12.5px] leading-snug', tones[tone])}
    >
      <span className="mt-px shrink-0" aria-hidden="true">
        {icon}
      </span>
      <div className="min-w-0 flex-1 space-y-1.5">{children}</div>
    </div>
  )
}

/** Salas sugeridas (RF06 / vídeo): as que comportam os participantes primeiro, com a capacidade à vista. */
function RoomButtons({
  rooms,
  attendees,
  onPick,
}: {
  rooms: RoomOut[]
  attendees: number | null
  onPick: (room: RoomOut) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {preferFitting(rooms, attendees)
        .slice(0, 6)
        .map((r) => (
          <Button
            key={r.id}
            size="xs"
            variant={r.effective_type === 'VIDEO' ? 'video' : 'normal'}
            aria-label={roomOptionLabel(`Usar ${r.name}`, r.capacity)}
            onClick={() => onPick(r)}
            title={fitsAttendees(r, attendees) ? undefined : `Pequena para ${attendees} participantes`}
          >
            Usar {r.name}
            <SeatsSuffix capacity={r.capacity} tooSmall={!fitsAttendees(r, attendees)} />
          </Button>
        ))}
    </div>
  )
}
