import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import {
  Ban,
  CalendarClock,
  CheckCircle2,
  DoorOpen,
  ExternalLink,
  History,
  NotebookPen,
  Pencil,
  RotateCcw,
  UserX,
  Users,
  Video,
  X,
} from 'lucide-react'

import { errorMessage } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { BookingLogOut, BookingOut } from '../../api/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { FloatingPanel } from '../../components/ui/FloatingPanel'
import { Textarea } from '../../components/ui/Form'
import { Spinner } from '../../components/ui/Spinner'
import { useDismiss } from '../../hooks/useDismiss'
import { logDetailRows, summarizeLog } from '../../lib/auditDiff'
import { cn } from '../../lib/cn'
import { ACTION_LABELS, STATUS_LABELS } from '../../lib/labels'
import { PERIOD_LABELS } from '../../lib/periods'
import { formatDateLong, formatDateTime, formatDuration, formatRelative, toMinutes } from '../../lib/time'
import { useBooking } from '../../store/bookings'
import { useLoadedConfig } from '../../store/config'
import { type AnchorRect, useUi } from '../../store/ui'
import { bookingFlags } from './BookingBlock'
import { cancelBooking, changeStatus } from './bookingActions'

export function BookingPopoverHost() {
  const popover = useUi((s) => s.popover)
  const booking = useBooking(popover?.bookingId)
  const closePopover = useUi((s) => s.closePopover)

  // Reserva sumiu (cancelada por outra pessoa ou mudou de dia): fecha.
  const gone = popover !== null && (!booking || booking.status === 'CANCELLED')
  useEffect(() => {
    if (gone) closePopover()
  }, [gone, closePopover])

  if (!popover || !booking || booking.status === 'CANCELLED') return null
  return <BookingPopover key={booking.id} booking={booking} anchor={popover.anchor} />
}

type Mode = 'details' | 'history' | 'cancel'

function BookingPopover({ booking, anchor }: { booking: BookingOut; anchor: AnchorRect }) {
  const config = useLoadedConfig()
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const [mode, setMode] = useState<Mode>('details')
  const [busy, setBusy] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const close = () => useUi.getState().closePopover()
  useDismiss(panelRef, close, { enabled: busy === null })

  useEffect(() => {
    panelRef.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus({ preventScroll: true })
  }, [mode])

  const run = async (key: string, action: () => Promise<boolean>) => {
    // O popover aberto pode ter mudado durante a espera (clique em outra reserva): só fecha este (R39).
    const mine = useUi.getState().popover
    setBusy(key)
    const ok = await action()
    setBusy(null)
    if (ok && useUi.getState().popover === mine) close()
  }

  const edit = () => {
    useUi.getState().openComposer({
      mode: 'edit',
      bookingId: booking.id,
      roomId: booking.room_id,
      date: booking.date,
      period: booking.period_type,
      start: booking.start,
      end: booking.end,
      lockId: null,
      anchor,
    })
  }

  const flags = bookingFlags(booking)
  const duration = toMinutes(booking.end) - toMinutes(booking.start)

  return (
    <FloatingPanel
      ref={panelRef}
      anchor={anchor}
      role="dialog"
      aria-labelledby={titleId}
      className="w-[392px] max-w-[calc(100vw-20px)]"
    >
      <div className="flex max-h-[calc(100dvh-20px)] flex-col">
        <header className="border-b border-line px-4 pt-3 pb-2.5">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-[12.5px]">
                {booking.ticket_number ? (
                  booking.ticket_url ? (
                    <a
                      href={booking.ticket_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-mono font-medium text-video hover:underline"
                    >
                      Chamado #{booking.ticket_number}
                      <ExternalLink className="size-3" aria-hidden="true" />
                    </a>
                  ) : (
                    <span className="font-mono font-medium text-ink-2">Chamado #{booking.ticket_number}</span>
                  )
                ) : (
                  <span className="text-muted">Sem nº de chamado</span>
                )}
                <Badge
                  tone={
                    booking.status === 'CHECKED_IN' ? 'success' : booking.status === 'NO_SHOW' ? 'neutral' : 'video'
                  }
                >
                  {STATUS_LABELS[booking.status]}
                </Badge>
              </div>
              <h2 id={titleId} className="mt-1 truncate text-[15px] font-semibold text-ink">
                {booking.requester_name}
              </h2>
              {booking.title && <p className="text-[12.5px] text-muted">{booking.title}</p>}
            </div>
            <button
              type="button"
              onClick={close}
              aria-label="Fechar"
              className="-mt-0.5 -mr-1.5 rounded p-1 text-muted hover:bg-ink/[0.06] hover:text-ink"
            >
              <X className="size-4" />
            </button>
          </div>
          {flags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {flags.map((f) => (
                <span
                  key={f.key}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-[4px] border px-1.5 py-px text-[11px] font-medium',
                    f.tone,
                  )}
                >
                  <f.icon className="size-3" aria-hidden="true" />
                  {f.label.replace(/^⚠ /, '')}
                </span>
              ))}
            </div>
          )}
        </header>

        <div className="flex gap-1 border-b border-line px-3 pt-1.5" role="tablist" aria-label="Seções da reserva">
          <TabButton active={mode !== 'history'} onClick={() => setMode('details')}>
            Detalhes
          </TabButton>
          <TabButton active={mode === 'history'} onClick={() => setMode('history')}>
            <History className="size-3.5" aria-hidden="true" /> Histórico
          </TabButton>
        </div>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {mode === 'history' ? (
            <BookingHistory bookingId={booking.id} timeZone={config.timezone} />
          ) : (
            <dl className="space-y-2 text-[12.5px]">
              <Row
                icon={booking.requires_video ? <Video className="size-3.5" /> : <DoorOpen className="size-3.5" />}
                label="Sala"
              >
                {booking.room_name}
                <span className="text-muted"> · {booking.requires_video ? 'precisa de vídeo' : 'sem vídeo'}</span>
              </Row>
              <Row icon={<CalendarClock className="size-3.5" />} label="Quando">
                <span className="first-letter:uppercase">{formatDateLong(booking.date)}</span>
                <span className="tabular block font-mono text-ink-2">
                  {booking.start}–{booking.end}{' '}
                  <span className="font-sans text-muted">
                    · {formatDuration(duration)} · {PERIOD_LABELS[booking.period_type]}
                  </span>
                </span>
              </Row>
              {booking.attendees && (
                <Row icon={<Users className="size-3.5" />} label="Participantes">
                  {booking.attendees}
                </Row>
              )}
              {booking.notes && (
                <Row icon={<NotebookPen className="size-3.5" />} label="Observações">
                  <span className="whitespace-pre-wrap">{booking.notes}</span>
                </Row>
              )}
              <p className="pt-1 text-[11.5px] text-muted">
                Criada por {booking.created_by.full_name} em {formatDateTime(booking.created_at, config.timezone)}
                {booking.updated_by && booking.updated_at !== booking.created_at && (
                  <>
                    <br />
                    Alterada por {booking.updated_by.full_name} em {formatDateTime(booking.updated_at, config.timezone)}
                  </>
                )}
              </p>
            </dl>
          )}
        </div>

        {mode === 'cancel' ? (
          <footer className="space-y-2 rounded-b-lg border-t border-danger/25 bg-danger-tint/50 px-4 py-3">
            <p className="text-[12.5px] font-medium text-danger-ink">
              Cancelar esta reserva? O horário fica livre para outras pessoas.
            </p>
            <label htmlFor={`${titleId}-reason`} className="sr-only">
              Motivo do cancelamento
            </label>
            <Textarea
              id={`${titleId}-reason`}
              data-autofocus
              rows={2}
              maxLength={500}
              placeholder="Motivo (opcional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="resize-none bg-surface"
            />
            <div className="flex justify-end gap-2">
              <Button size="sm" onClick={() => setMode('details')} disabled={busy !== null}>
                Voltar
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={busy === 'cancel'}
                icon={<Ban className="size-3.5" />}
                onClick={() => void run('cancel', () => cancelBooking(booking, reason))}
              >
                Cancelar reserva
              </Button>
            </div>
          </footer>
        ) : (
          <footer className="flex flex-wrap items-center gap-1.5 rounded-b-lg border-t border-line bg-paper/70 px-3 py-2.5">
            <Button
              size="sm"
              variant="primary"
              icon={<Pencil className="size-3.5" />}
              onClick={edit}
              data-autofocus
              disabled={busy !== null}
            >
              Editar
            </Button>
            {booking.status === 'CONFIRMED' ? (
              <>
                <Button
                  size="sm"
                  icon={<CheckCircle2 className="size-3.5 text-success" />}
                  loading={busy === 'checkin'}
                  disabled={busy !== null}
                  onClick={() => void run('checkin', () => changeStatus(booking, 'CHECKED_IN'))}
                >
                  Check-in
                </Button>
                <Button
                  size="sm"
                  icon={<UserX className="size-3.5 text-muted" />}
                  loading={busy === 'noshow'}
                  disabled={busy !== null}
                  onClick={() => void run('noshow', () => changeStatus(booking, 'NO_SHOW'))}
                >
                  Não compareceu
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                icon={<RotateCcw className="size-3.5" />}
                loading={busy === 'confirm'}
                disabled={busy !== null}
                onClick={() => void run('confirm', () => changeStatus(booking, 'CONFIRMED'))}
              >
                Voltar para confirmada
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto text-danger hover:bg-danger-tint"
              icon={<Ban className="size-3.5" />}
              disabled={busy !== null}
              onClick={() => setMode('cancel')}
            >
              Cancelar
            </Button>
          </footer>
        )}
      </div>
    </FloatingPanel>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        '-mb-px flex items-center gap-1 border-b-2 px-2 pb-1.5 text-[12.5px] font-medium',
        active ? 'border-ink text-ink' : 'border-transparent text-muted hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}

function Row({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <dt className="mt-0.5 shrink-0 text-muted" title={label}>
        <span aria-hidden="true">{icon}</span>
        <span className="sr-only">{label}</span>
      </dt>
      <dd className="min-w-0 flex-1 text-ink">{children}</dd>
    </div>
  )
}

/** Linha do tempo de /api/bookings/{id}/logs (mais antigo primeiro → exibimos o mais recente no topo). */
export function BookingHistory({ bookingId, timeZone }: { bookingId: string; timeZone: string }) {
  const [logs, setLogs] = useState<BookingLogOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.bookings
      .logs(bookingId)
      .then((list) => alive && setLogs([...list].reverse()))
      .catch((err: unknown) => alive && setError(errorMessage(err, 'Não foi possível carregar o histórico.')))
    return () => {
      alive = false
    }
  }, [bookingId])

  if (error) return <p className="text-[12.5px] text-danger">{error}</p>
  if (!logs)
    return (
      <p className="flex items-center gap-2 text-[12.5px] text-muted">
        <Spinner className="size-3.5" /> Carregando histórico…
      </p>
    )
  if (logs.length === 0) return <p className="text-[12.5px] text-muted">Sem registros.</p>

  const now = Date.now()
  return (
    <ol className="relative space-y-3 border-l border-line-strong pl-4">
      {logs.map((log) => {
        // Campos que mudaram junto (ex.: troca de sala + chamado), além do que o resumo já diz (R31).
        const diff = logDetailRows(log)
        return (
          <li key={log.id} className="relative">
            <span
              className="absolute top-1 -left-[21px] size-2.5 rounded-full border-2 border-surface bg-ink-2"
              aria-hidden="true"
            />
            <p className="text-[12.5px] text-ink">
              <span className="font-semibold">{ACTION_LABELS[log.action]}</span>
              <span className="text-muted"> · {log.user?.full_name ?? 'Sistema'}</span>
            </p>
            <p className="text-[12px] text-ink-2">{summarizeLog(log)}</p>
            {diff.length > 0 && (
              <ul className="mt-0.5 space-y-px text-[11.5px] text-muted">
                {diff.map((d) => (
                  <li key={d.field}>
                    {d.label}: <span className="line-through">{d.before ?? '—'}</span> →{' '}
                    <span className="text-ink-2">{d.after ?? '—'}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[11px] text-faint" title={formatDateTime(log.created_at, timeZone)}>
              {formatRelative(log.created_at, now, timeZone)}
            </p>
          </li>
        )
      })}
    </ol>
  )
}
