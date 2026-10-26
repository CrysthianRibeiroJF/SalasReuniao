import { type KeyboardEvent, useEffect, useId, useRef, useState } from 'react'
import { Search, Video, X } from 'lucide-react'

import { api } from '../../api/endpoints'
import type { BookingOut, ISODate } from '../../api/types'
import { Kbd } from '../../components/ui/Kbd'
import { Spinner } from '../../components/ui/Spinner'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { useDismiss } from '../../hooks/useDismiss'
import { useLatest } from '../../hooks/useLatest'
import { cn } from '../../lib/cn'
import { STATUS_LABELS } from '../../lib/labels'
import { formatTabLabel } from '../../lib/time'
import { useUi } from '../../store/ui'
import { firstPastIndex, moveActiveIndex } from './searchNav'

const MIN_CHARS = 2
const DEBOUNCE_MS = 200

type Status = 'idle' | 'loading' | 'done' | 'error'

/** Busca instantânea por chamado, solicitante ou assunto (atalhos / e Ctrl+K). */
export function SearchBox({ today, onNavigate }: { today: ISODate; onNavigate: (booking: BookingOut) => void }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  // Resultados e o termo a que eles pertencem (a lista pode ser de um termo anterior ao digitado).
  const [found, setFound] = useState<{ term: string | null; list: BookingOut[] }>({ term: null, list: [] })
  const results = found.list
  const [status, setStatus] = useState<Status>('idle')
  const [active, setActive] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  const typed = query.trim()
  const debounced = useDebouncedValue(typed, DEBOUNCE_MS)
  // Enter antes do fim do debounce busca o termo digitado na hora (sem esperar os 200 ms).
  const [flushed, setFlushed] = useState<string | null>(null)
  const term = flushed !== null && flushed === typed ? flushed : debounced
  /** Termo cujo primeiro resultado abre assim que a busca responder (Enter antes da resposta). */
  const pendingEnter = useRef<string | null>(null)
  const focusSeq = useUi((s) => s.searchFocusSeq)

  useDismiss(rootRef, () => setOpen(false), { enabled: open, escape: false })

  useEffect(() => {
    if (focusSeq === 0) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [focusSeq])

  useEffect(() => {
    if (term.length < MIN_CHARS) {
      setFound({ term: null, list: [] })
      setStatus('idle')
      return
    }
    const controller = new AbortController()
    setStatus('loading')
    api.bookings
      .search(term, 20, controller.signal)
      .then((list) => {
        setFound({ term, list })
        setActive(list.length > 0 ? 0 : -1)
        setStatus('done')
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus('error')
      })
    return () => controller.abort()
  }, [term])

  const choose = (booking: BookingOut | undefined) => {
    if (!booking) return
    pendingEnter.current = null
    setOpen(false)
    setQuery('')
    inputRef.current?.blur()
    onNavigate(booking)
  }
  const chooseRef = useLatest(choose)

  // Chegou a resposta do termo em que a pessoa já tinha dado Enter: abre o primeiro resultado.
  useEffect(() => {
    if (status !== 'done' || pendingEnter.current === null || pendingEnter.current !== found.term) return
    pendingEnter.current = null
    chooseRef.current(found.list[0])
  }, [found, status, chooseRef])

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      setActive((i) => moveActiveIndex(i, event.key === 'ArrowDown' ? 1 : -1, results.length))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (typed.length < MIN_CHARS) return
      if (status === 'done' && found.term === typed) {
        choose(results[active] ?? results[0])
      } else {
        // A lista na tela é de um termo anterior (ou a busca ainda não respondeu): abre o primeiro
        // resultado do termo digitado assim que ele chegar, em vez de um resultado desatualizado.
        pendingEnter.current = typed
        setFlushed(typed)
        setOpen(true)
      }
    } else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      if (open && query) setOpen(false)
      else {
        setQuery('')
        inputRef.current?.blur()
      }
    }
  }

  useEffect(() => {
    if (active < 0) return
    document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: 'nearest' })
  }, [active, listId])

  const pastFrom = firstPastIndex(results, today)
  const showDropdown = open && query.trim().length > 0

  return (
    <div ref={rootRef} className="relative w-full max-w-[340px] min-w-[200px] flex-1">
      <Search
        className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted"
        aria-hidden="true"
      />
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-expanded={showDropdown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showDropdown && active >= 0 ? `${listId}-${active}` : undefined}
        aria-label="Buscar reserva por chamado, solicitante ou assunto"
        placeholder="Buscar chamado ou solicitante"
        value={query}
        onChange={(e) => {
          if (pendingEnter.current !== e.target.value.trim()) pendingEnter.current = null
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        autoComplete="off"
        spellCheck={false}
        className="h-8 w-full rounded-md border border-line-strong bg-paper pr-14 pl-8 text-[13px] text-ink outline-none placeholder:text-faint focus:border-video focus:bg-surface focus:ring-3 focus:ring-video/15 [&::-webkit-search-cancel-button]:hidden"
      />
      <div className="absolute top-1/2 right-2 flex -translate-y-1/2 items-center gap-1">
        {status === 'loading' && <Spinner className="size-3.5 text-muted" />}
        {query ? (
          <button
            type="button"
            aria-label="Limpar busca"
            onClick={() => {
              setQuery('')
              inputRef.current?.focus()
            }}
            className="rounded p-0.5 text-muted hover:text-ink"
          >
            <X className="size-3.5" />
          </button>
        ) : (
          <span className="flex gap-0.5" aria-hidden="true">
            <Kbd>/</Kbd>
          </span>
        )}
      </div>

      {showDropdown && (
        <div className="absolute top-full left-0 z-50 mt-1 w-[440px] max-w-[calc(100vw-24px)] animate-pop-in overflow-hidden rounded-lg border border-line-strong bg-surface shadow-float">
          {query.trim().length < MIN_CHARS ? (
            <p className="px-3 py-2.5 text-[12.5px] text-muted">Digite ao menos {MIN_CHARS} caracteres.</p>
          ) : status === 'error' ? (
            <p className="px-3 py-2.5 text-[12.5px] text-danger">Não foi possível buscar agora. Tente de novo.</p>
          ) : status === 'done' && results.length === 0 ? (
            <p className="px-3 py-2.5 text-[12.5px] text-muted">
              Nenhuma reserva encontrada para “<span className="font-medium text-ink">{term}</span>”.
            </p>
          ) : (
            <ul
              id={listId}
              role="listbox"
              aria-label="Resultados da busca"
              className="scroll-thin max-h-[min(420px,60vh)] overflow-y-auto py-1"
            >
              {results.map((booking, i) => (
                <li key={booking.id} role="presentation">
                  {i === pastFrom && (
                    <p className="border-t border-line px-3 pt-2 pb-1 text-[10.5px] font-semibold tracking-wide text-faint uppercase">
                      Anteriores
                    </p>
                  )}
                  <div
                    id={`${listId}-${i}`}
                    role="option"
                    aria-selected={i === active}
                    onPointerDown={(e) => e.preventDefault()}
                    onClick={() => choose(booking)}
                    onPointerEnter={() => setActive(i)}
                    className={cn(
                      'flex cursor-pointer items-center gap-3 px-3 py-1.5',
                      i === active ? 'bg-video-tint' : 'hover:bg-paper',
                      pastFrom >= 0 && i >= pastFrom && 'opacity-75',
                    )}
                  >
                    <span className="w-[76px] shrink-0 truncate font-mono text-[12px] font-medium text-ink-2">
                      {booking.ticket_number ? `#${booking.ticket_number}` : '—'}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium text-ink">{booking.requester_name}</span>
                      {booking.title && (
                        <span className="block truncate text-[11.5px] text-muted">{booking.title}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="tabular block font-mono text-[11.5px] text-ink-2">
                        {formatTabLabel(booking.date)} · {booking.start}–{booking.end}
                      </span>
                      <span className="flex items-center justify-end gap-1 text-[11.5px] text-muted">
                        {booking.requires_video && <Video className="size-3" aria-label="Com vídeo" />}
                        {booking.room_name}
                        {booking.status !== 'CONFIRMED' && <span>· {STATUS_LABELS[booking.status]}</span>}
                      </span>
                    </span>
                  </div>
                </li>
              ))}
              {status === 'loading' && results.length === 0 && (
                <li className="px-3 py-2.5 text-[12.5px] text-muted">Buscando…</li>
              )}
            </ul>
          )}
          {results.length > 0 && (
            <div className="flex items-center gap-3 border-t border-line bg-paper/70 px-3 py-1.5 text-[11px] text-muted">
              <span className="flex items-center gap-1">
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> navegar
              </span>
              <span className="flex items-center gap-1">
                <Kbd>Enter</Kbd> abrir o dia
              </span>
              <span className="flex items-center gap-1">
                <Kbd>Esc</Kbd> fechar
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
