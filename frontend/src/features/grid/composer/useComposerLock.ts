/**
 * Ciclo de vida da trava do modal (contrato §6):
 * - assume a trava obtida na seleção; troca de sala/horário → nova LOCK_SLOT e libera a anterior;
 * - REFRESH_LOCK a cada ~1/3 do TTL do servidor (lock_ttl_seconds, entre 5 e 45 s) enquanto o modal
 *   estiver aberto (se falhar, tenta travar de novo);
 * - perdeu a trava com o modal aberto (SLOT_UNLOCKED dela: expirou, ou o servidor a liberou ao gravar
 *   outra reserva) → trava de novo na hora, sem esperar a próxima renovação;
 * - ao reconectar, a trava antiga já foi liberada pelo servidor: trava de novo;
 * - enquanto salva, não pede travas novas (uma que chegue atrasada é liberada); salvou → o servidor
 *   libera a enviada, e qualquer outra que ainda estiver com o modal é liberada aqui;
 * - fechar/cancelar → UNLOCK_SLOT.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import type { HHMM, ISODate, LockOut } from '../../../api/types'
import { getRealtime, onRealtimeEvent } from '../../../realtime/service'
import { useConfig } from '../../../store/config'
import { useRealtime } from '../../../store/realtime'
import { useUi } from '../../../store/ui'

export const MAX_REFRESH_INTERVAL_MS = 45_000
export const MIN_REFRESH_INTERVAL_MS = 5_000
const RELOCK_DEBOUNCE_MS = 200

/** Intervalo de renovação para o TTL configurado no servidor (AGENDA_LOCK_TTL_SECONDS): ~1/3 dele. */
export function refreshIntervalFor(ttlSeconds: number | null | undefined): number {
  if (!ttlSeconds || ttlSeconds <= 0) return MAX_REFRESH_INTERVAL_MS
  return Math.min(MAX_REFRESH_INTERVAL_MS, Math.max(MIN_REFRESH_INTERVAL_MS, Math.round((ttlSeconds * 1000) / 3)))
}

export interface LockTarget {
  roomId: number
  date: ISODate
  start: HHMM
  end: HHMM
  bookingId: string | null
}

export interface LockDenied {
  message: string
  conflicts: LockOut[]
}

interface Held {
  id: string
  key: string
}

function keyOf(t: LockTarget): string {
  return `${t.roomId}|${t.date}|${t.start}|${t.end}`
}

export function useComposerLock({
  target,
  initialLockId,
  enabled,
}: {
  target: LockTarget
  /** Trava obtida antes de abrir o modal (cobre exatamente `target` inicial). */
  initialLockId: string | null
  /** false = não travar (ex.: edição sem mudança de sala/horário). */
  enabled: boolean
}) {
  const held = useRef<Held | null>(initialLockId ? { id: initialLockId, key: keyOf(target) } : null)
  const saving = useRef(false)
  const saved = useRef(false)
  const [lockId, setLockId] = useState<string | null>(initialLockId)
  const [denied, setDenied] = useState<LockDenied | null>(null)
  const [nonce, setNonce] = useState(0)
  const status = useRealtime((s) => s.status)
  const refreshMs = useConfig((s) => refreshIntervalFor(s.config?.lock_ttl_seconds))

  const forget = useCallback(() => {
    held.current = null
    setLockId(null)
  }, [])

  const release = useCallback(() => {
    const current = held.current
    forget()
    if (current) getRealtime()?.unlock(current.id)
  }, [forget])

  const { roomId, date, start, end, bookingId } = target

  // (Re)travar quando a faixa muda, ao reconectar ou quando a trava se perde.
  useEffect(() => {
    if (!enabled) {
      release()
      setDenied(null)
      return
    }
    const key = keyOf({ roomId, date, start, end, bookingId })
    if (held.current?.key === key) return
    const realtime = getRealtime()
    if (!realtime?.isOpen) return
    let cancelled = false
    const timer = setTimeout(async () => {
      // Salvando: a gravação decide (endSave volta a travar se o modal continuar aberto).
      if (saving.current || saved.current) return
      const result = await realtime.lockSlot({ room_id: roomId, date, start, end, booking_id: bookingId })
      if (cancelled || saving.current || saved.current) {
        // Chegou tarde (outra faixa, ou o salvamento começou): não pode ficar presa até o TTL (R26).
        if (result.ok) realtime.unlock(result.lock.lock_id)
        return
      }
      const previous = held.current
      if (result.ok) {
        held.current = { id: result.lock.lock_id, key }
        setLockId(result.lock.lock_id)
        setDenied(null)
        if (previous && previous.id !== result.lock.lock_id) realtime.unlock(previous.id)
      } else {
        if (previous) realtime.unlock(previous.id)
        forget()
        setDenied(result.reason === 'DENIED' ? { message: result.message, conflicts: result.conflicts } : null)
      }
    }, RELOCK_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [roomId, date, start, end, bookingId, enabled, status, nonce, release, forget])

  // Queda da conexão: o servidor libera as travas da conexão; esquecemos a nossa.
  useEffect(() => {
    if (status !== 'open' && held.current) forget()
  }, [status, forget])

  // A trava do modal sumiu no servidor (expirou, ou foi liberada ao gravar outra reserva): trava de novo
  // já, em vez de deixar o horário livre para as colegas até a próxima renovação (R7/R52).
  useEffect(
    () =>
      onRealtimeEvent((event) => {
        if (event.type !== 'SLOT_UNLOCKED') return
        if (!held.current || event.data.lock.lock_id !== held.current.id) return
        forget()
        if (!saving.current && !saved.current) setNonce((n) => n + 1)
      }),
    [forget],
  )

  // Renovação periódica (~1/3 do TTL do servidor).
  useEffect(() => {
    if (!enabled) return
    const timer = setInterval(async () => {
      const current = held.current
      const realtime = getRealtime()
      if (!current || !realtime?.isOpen) return
      const refreshed = await realtime.refreshLock(current.id)
      if (!refreshed && held.current?.id === current.id) {
        forget()
        if (!saving.current && !saved.current) setNonce((n) => n + 1)
      }
    }, refreshMs)
    return () => clearInterval(timer)
  }, [enabled, refreshMs, forget])

  // Fechou sem salvar: libera. O modal guardado no store não pode reaproveitar esta trava (R8).
  useEffect(
    () => () => {
      if (!saved.current && held.current) getRealtime()?.unlock(held.current.id)
      held.current = null
      const ui = useUi.getState()
      if (initialLockId && ui.composer?.lockId === initialLockId) ui.patchComposer({ lockId: null })
    },
    // initialLockId é fixo durante a vida do modal (o modal é remontado a cada abertura)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  /** Começa a gravar: devolve a trava a enviar como lock_id. */
  const beginSave = useCallback((): string | null => {
    saving.current = true
    return held.current?.id ?? null
  }, [])

  /** Terminou de gravar. `sentLockId` = a trava enviada (o servidor a liberou se deu certo). */
  const endSave = useCallback((ok: boolean, sentLockId: string | null) => {
    saving.current = false
    if (ok) {
      saved.current = true
      const current = held.current
      held.current = null
      if (current && current.id !== sentLockId) getRealtime()?.unlock(current.id)
      return
    }
    // Continua no modal (conflito, erro): se a trava se perdeu no meio, trava de novo.
    setNonce((n) => n + 1)
  }, [])

  return { lockId, denied, beginSave, endSave, release }
}
