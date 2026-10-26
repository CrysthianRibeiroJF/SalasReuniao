/**
 * Soft locks visíveis na grade. Regras puras:
 * - SLOT_LOCKED insere/renova (novo expires_at); SLOT_UNLOCKED remove e deixa uma "lápide" para
 *   que um SLOT_LOCKED atrasado da mesma trava não a ressuscite;
 * - travas vencidas (expires_at no passado, pelo relógio do servidor) somem mesmo sem evento.
 */
import type { ISODate, LockOut } from '../api/types'

export interface LocksData {
  byId: Record<string, LockOut>
  /** lock_id liberado → instante (ms) até quando ignorar eventos atrasados dele. */
  released: Record<string, number>
}

export const EMPTY_LOCKS: LocksData = { byId: {}, released: {} }

/** Por quanto tempo lembrar de uma trava liberada. */
export const RELEASED_MEMORY_MS = 5 * 60_000

export function expiresAtMs(lock: Pick<LockOut, 'expires_at'>): number {
  const ms = Date.parse(lock.expires_at)
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms
}

/** Estado completo enviado no HELLO (descarta travas já vencidas). */
export function replaceLocks(locks: readonly LockOut[], nowMs: number): LocksData {
  const byId: Record<string, LockOut> = {}
  for (const lock of locks) if (expiresAtMs(lock) > nowMs) byId[lock.lock_id] = lock
  return { byId, released: {} }
}

export function upsertLock(data: LocksData, lock: LockOut, nowMs: number): LocksData {
  if (data.released[lock.lock_id] !== undefined) return data
  if (expiresAtMs(lock) <= nowMs) return data
  const existing = data.byId[lock.lock_id]
  if (existing && expiresAtMs(existing) > expiresAtMs(lock)) return data
  return { ...data, byId: { ...data.byId, [lock.lock_id]: lock } }
}

export function removeLock(data: LocksData, lockId: string, nowMs: number): LocksData {
  const { [lockId]: _removed, ...byId } = data.byId
  return { byId, released: { ...data.released, [lockId]: nowMs + RELEASED_MEMORY_MS } }
}

export function pruneExpired(data: LocksData, nowMs: number): LocksData {
  let changed = false
  const byId: Record<string, LockOut> = {}
  for (const [id, lock] of Object.entries(data.byId)) {
    if (expiresAtMs(lock) > nowMs) byId[id] = lock
    else changed = true
  }
  const released: Record<string, number> = {}
  for (const [id, until] of Object.entries(data.released)) {
    if (until > nowMs) released[id] = until
    else changed = true
  }
  return changed ? { byId, released } : data
}

/** Travas de OUTROS usuários num dia (as próprias aparecem como seleção, não como hachurado). */
export function othersLocksForDate(data: LocksData, date: ISODate, myUserId: number | null): LockOut[] {
  return Object.values(data.byId).filter((l) => l.date === date && l.user.id !== myUserId)
}
