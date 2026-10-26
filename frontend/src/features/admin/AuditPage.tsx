import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, History, X } from 'lucide-react'

import { errorMessage } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { AuditEntryOut, AuditQuery, ISODate, LogAction, UserBrief } from '../../api/types'
import { Badge, type BadgeTone } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { EmptyState } from '../../components/ui/EmptyState'
import { Field, Input, Select } from '../../components/ui/Form'
import { Skeleton } from '../../components/ui/Skeleton'
import { snapshotDiff, summarizeLog } from '../../lib/auditDiff'
import { cn } from '../../lib/cn'
import { ACTION_LABELS } from '../../lib/labels'
import { formatDateTime, formatTabLabel, isValidISODate } from '../../lib/time'
import { useAuth, useIsAdmin } from '../../store/auth'
import { useLoadedConfig } from '../../store/config'
import { DataTable, PageHeader, Td, Th } from './AdminLayout'

const PAGE_SIZE = 50

export const ACTION_TONES: Record<LogAction, BadgeTone> = {
  CREATED: 'success',
  IMPORTED: 'normal',
  UPDATED: 'neutral',
  MOVED_ROOM: 'video',
  RESCHEDULED: 'video',
  STATUS_CHANGED: 'warning',
  CANCELLED: 'danger',
}

interface Filters {
  date: ISODate | ''
  bookingDate: ISODate | ''
  userId: string
  action: LogAction | ''
}

const EMPTY_FILTERS: Filters = { date: '', bookingDate: '', userId: '', action: '' }

const collator = new Intl.Collator('pt-BR', { sensitivity: 'base' })

/** Auditoria (RF07): quem criou, alterou, moveu ou cancelou cada reserva. */
export function AuditPage() {
  const config = useLoadedConfig()
  const isAdmin = useIsAdmin()
  const me = useAuth((s) => s.user)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [entries, setEntries] = useState<AuditEntryOut[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [knownUsers, setKnownUsers] = useState<UserBrief[]>([])
  /** Quantos registros o servidor já devolveu para os filtros atuais (offset da próxima página). */
  const fetched = useRef(0)

  // Admin vê a lista completa de usuários; recepção usa os autores já vistos nos registros.
  useEffect(() => {
    if (!isAdmin) return
    api.users
      .list()
      .then((users) => setKnownUsers(users.map((u) => ({ id: u.id, full_name: u.full_name }))))
      .catch(() => {})
  }, [isAdmin])

  const query = useMemo<AuditQuery>(
    () => ({
      date: filters.date || undefined,
      booking_date: filters.bookingDate || undefined,
      user_id: filters.userId ? Number(filters.userId) : undefined,
      action: filters.action || undefined,
      limit: PAGE_SIZE,
    }),
    [filters],
  )

  const mergeUsers = useCallback((list: AuditEntryOut[]) => {
    setKnownUsers((users) => {
      const byId = new Map(users.map((u) => [u.id, u]))
      for (const entry of list) if (entry.user) byId.set(entry.user.id, entry.user)
      return [...byId.values()]
    })
  }, [])

  useEffect(() => {
    let alive = true
    setEntries(null)
    setError(null)
    setExpanded(new Set())
    api.audit
      .list({ ...query, offset: 0 })
      .then((list) => {
        if (!alive) return
        fetched.current = list.length
        setEntries(list)
        setHasMore(list.length === PAGE_SIZE)
        mergeUsers(list)
      })
      .catch((err: unknown) => alive && setError(errorMessage(err, 'Não foi possível carregar a auditoria.')))
    return () => {
      alive = false
    }
  }, [query, mergeUsers])

  const loadMore = async () => {
    if (!entries) return
    setLoadingMore(true)
    try {
      const offset = fetched.current
      const list = await api.audit.list({ ...query, offset })
      // Registros novos entram no topo e empurram a página seguinte: as linhas repetidas são puladas
      // (sem chaves React duplicadas) e o próximo offset segue o que o servidor já devolveu (R37).
      fetched.current = offset + list.length
      const seen = new Set(entries.map((e) => e.id))
      setEntries([...entries, ...list.filter((e) => !seen.has(e.id))])
      setHasMore(list.length === PAGE_SIZE)
      mergeUsers(list)
    } catch (err) {
      setError(errorMessage(err, 'Não foi possível carregar mais registros.'))
    } finally {
      setLoadingMore(false)
    }
  }

  const userOptions = useMemo(() => {
    const byId = new Map(knownUsers.map((u) => [u.id, u]))
    if (me) byId.set(me.id, { id: me.id, full_name: me.full_name })
    return [...byId.values()].sort((a, b) => collator.compare(a.full_name, b.full_name))
  }, [knownUsers, me])

  const toggle = (id: number) =>
    setExpanded((set) => {
      const next = new Set(set)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const filtered = filters.date || filters.bookingDate || filters.userId || filters.action

  return (
    <>
      <PageHeader
        title="Auditoria"
        description="Registro automático de cada criação, alteração, troca de sala, mudança de status e cancelamento."
      />

      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-line-strong bg-surface p-3">
        <Field label="Dia da alteração" className="w-44">
          {(props) => (
            <Input
              {...props}
              type="date"
              value={filters.date}
              onChange={(e) =>
                setFilters((f) => ({ ...f, date: isValidISODate(e.target.value) ? e.target.value : '' }))
              }
            />
          )}
        </Field>
        <Field label="Dia da reserva" className="w-44">
          {(props) => (
            <Input
              {...props}
              type="date"
              value={filters.bookingDate}
              onChange={(e) =>
                setFilters((f) => ({ ...f, bookingDate: isValidISODate(e.target.value) ? e.target.value : '' }))
              }
            />
          )}
        </Field>
        <Field label="Usuário" className="w-56">
          {(props) => (
            <Select
              {...props}
              value={filters.userId}
              onChange={(e) => setFilters((f) => ({ ...f, userId: e.target.value }))}
            >
              <option value="">Todos</option>
              {userOptions.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Ação" className="w-48">
          {(props) => (
            <Select
              {...props}
              value={filters.action}
              onChange={(e) => setFilters((f) => ({ ...f, action: e.target.value as LogAction | '' }))}
            >
              <option value="">Todas</option>
              {(Object.keys(ACTION_LABELS) as LogAction[]).map((action) => (
                <option key={action} value={action}>
                  {ACTION_LABELS[action]}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {filtered && (
          <Button variant="ghost" size="sm" icon={<X className="size-3.5" />} onClick={() => setFilters(EMPTY_FILTERS)}>
            Limpar filtros
          </Button>
        )}
      </div>

      {error && (
        <p
          role="alert"
          className="mb-3 rounded-md border border-danger/25 bg-danger-tint px-3 py-2 text-[12.5px] text-danger-ink"
        >
          {error}
        </p>
      )}

      {entries === null && !error ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </div>
      ) : entries && entries.length === 0 ? (
        <EmptyState
          icon={<History className="size-5" />}
          title={filtered ? 'Nenhum registro para estes filtros' : 'Nenhum registro ainda'}
          description={
            filtered
              ? 'Tente outra data, usuário ou ação.'
              : 'As alterações nas reservas aparecem aqui automaticamente.'
          }
        />
      ) : (
        entries && (
          <>
            <DataTable>
              <thead>
                <tr>
                  <Th className="w-36">Quando</Th>
                  <Th className="w-44">Usuário</Th>
                  <Th className="w-36">Ação</Th>
                  <Th>Reserva</Th>
                  <Th>O que mudou</Th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => {
                  const open = expanded.has(entry.id)
                  const diff = snapshotDiff(entry.previous_state, entry.new_state)
                  return (
                    <Fragment key={entry.id}>
                      <tr className="hover:bg-paper/60">
                        <Td className="tabular font-mono text-[12px] whitespace-nowrap text-ink-2">
                          {formatDateTime(entry.created_at, config.timezone)}
                        </Td>
                        <Td>{entry.user?.full_name ?? <span className="text-muted">Sistema</span>}</Td>
                        <Td>
                          <Badge tone={ACTION_TONES[entry.action]}>{ACTION_LABELS[entry.action]}</Badge>
                        </Td>
                        <Td>
                          <p className="truncate">
                            {entry.booking_ticket_number && (
                              <span className="mr-1.5 font-mono text-[12px] text-ink-2">
                                #{entry.booking_ticket_number}
                              </span>
                            )}
                            <span className="font-medium">{entry.booking_requester_name ?? '—'}</span>
                          </p>
                          <p className="text-[11.5px] text-muted">
                            {[entry.booking_room_name, entry.booking_date ? formatTabLabel(entry.booking_date) : null]
                              .filter(Boolean)
                              .join(' · ')}
                          </p>
                        </Td>
                        <Td>
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-[12.5px] text-ink-2">{summarizeLog(entry)}</span>
                            {diff.length > 0 && (
                              <button
                                type="button"
                                aria-expanded={open}
                                onClick={() => toggle(entry.id)}
                                className="flex shrink-0 items-center gap-0.5 text-[12px] font-medium text-video hover:underline"
                              >
                                <ChevronRight
                                  className={cn('size-3.5 transition-transform', open && 'rotate-90')}
                                  aria-hidden="true"
                                />
                                {open ? 'Ocultar' : 'Detalhes'}
                              </button>
                            )}
                          </div>
                        </Td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={5} className="border-b border-line bg-paper/70 px-4 py-3">
                            <DiffTable rows={diff} hasPrevious={entry.previous_state !== null} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </DataTable>
            {hasMore && (
              <div className="mt-3 flex justify-center">
                <Button onClick={() => void loadMore()} loading={loadingMore}>
                  Carregar mais
                </Button>
              </div>
            )}
          </>
        )
      )}
    </>
  )
}

export function DiffTable({ rows, hasPrevious }: { rows: ReturnType<typeof snapshotDiff>; hasPrevious: boolean }) {
  return (
    <table className="w-full max-w-2xl text-[12.5px]">
      <thead>
        <tr className="text-left text-[11px] tracking-wide text-muted uppercase">
          <th className="w-40 py-1 pr-3 font-semibold">Campo</th>
          {hasPrevious && <th className="py-1 pr-3 font-semibold">Antes</th>}
          <th className="py-1 font-semibold">{hasPrevious ? 'Depois' : 'Valor'}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.field} className="border-t border-line">
            <td className="py-1 pr-3 text-muted">{row.label}</td>
            {hasPrevious && (
              <td className="py-1 pr-3 text-danger-ink line-through decoration-danger/40">{row.before ?? '—'}</td>
            )}
            <td className="py-1 font-medium text-ink">{row.after ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
