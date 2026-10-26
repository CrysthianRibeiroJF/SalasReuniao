import { describe, expect, it } from 'vitest'

import { makeLock } from '../test/fixtures'
import {
  EMPTY_LOCKS,
  othersLocksForDate,
  pruneExpired,
  RELEASED_MEMORY_MS,
  removeLock,
  replaceLocks,
  upsertLock,
} from './locksModel'

const NOW = Date.parse('2026-10-26T14:00:00-03:00')
const at = (offsetMs: number) => new Date(NOW + offsetMs).toISOString()

describe('locksModel', () => {
  it('HELLO substitui tudo e descarta travas vencidas', () => {
    const data = replaceLocks(
      [makeLock({ lock_id: 'a', expires_at: at(60_000) }), makeLock({ lock_id: 'b', expires_at: at(-1_000) })],
      NOW,
    )
    expect(Object.keys(data.byId)).toEqual(['a'])
  })

  it('SLOT_LOCKED insere e renova (novo expires_at)', () => {
    let data = upsertLock(EMPTY_LOCKS, makeLock({ lock_id: 'a', expires_at: at(30_000) }), NOW)
    data = upsertLock(data, makeLock({ lock_id: 'a', expires_at: at(120_000) }), NOW)
    expect(data.byId.a?.expires_at).toBe(at(120_000))
  })

  it('ignora renovação atrasada com expires_at mais antigo', () => {
    let data = upsertLock(EMPTY_LOCKS, makeLock({ lock_id: 'a', expires_at: at(120_000) }), NOW)
    const same = upsertLock(data, makeLock({ lock_id: 'a', expires_at: at(30_000) }), NOW)
    expect(same).toBe(data)
    data = upsertLock(data, makeLock({ lock_id: 'a', expires_at: at(120_000) }), NOW)
    expect(data.byId.a).toBeDefined()
  })

  it('SLOT_UNLOCKED remove e um SLOT_LOCKED atrasado não ressuscita a trava', () => {
    let data = upsertLock(EMPTY_LOCKS, makeLock({ lock_id: 'a' }), NOW)
    data = removeLock(data, 'a', NOW)
    expect(data.byId.a).toBeUndefined()
    data = upsertLock(data, makeLock({ lock_id: 'a', expires_at: at(120_000) }), NOW + 10)
    expect(data.byId.a).toBeUndefined()
  })

  it('remover uma trava desconhecida é idempotente', () => {
    const data = removeLock(EMPTY_LOCKS, 'x', NOW)
    expect(removeLock(data, 'x', NOW).byId).toEqual({})
  })

  it('expira no cliente travas além de expires_at, mesmo sem evento', () => {
    let data = upsertLock(EMPTY_LOCKS, makeLock({ lock_id: 'a', expires_at: at(5_000) }), NOW)
    data = upsertLock(data, makeLock({ lock_id: 'b', expires_at: at(60_000) }), NOW)
    expect(pruneExpired(data, NOW + 1_000)).toBe(data)
    data = pruneExpired(data, NOW + 5_000)
    expect(Object.keys(data.byId)).toEqual(['b'])
  })

  it('não aceita trava que já chega vencida', () => {
    expect(upsertLock(EMPTY_LOCKS, makeLock({ expires_at: at(-1) }), NOW)).toBe(EMPTY_LOCKS)
  })

  it('esquece lápides antigas', () => {
    let data = removeLock(EMPTY_LOCKS, 'a', NOW)
    data = pruneExpired(data, NOW + RELEASED_MEMORY_MS + 1)
    expect(data.released).toEqual({})
    expect(
      upsertLock(
        data,
        makeLock({ lock_id: 'a', expires_at: at(RELEASED_MEMORY_MS + 60_000) }),
        NOW + RELEASED_MEMORY_MS + 2,
      ).byId.a,
    ).toBeDefined()
  })

  it('a grade mostra só as travas de outros usuários no dia', () => {
    const expires_at = at(60_000)
    let data = upsertLock(EMPTY_LOCKS, makeLock({ lock_id: 'mine', user: { id: 1, full_name: 'Eu' }, expires_at }), NOW)
    data = upsertLock(data, makeLock({ lock_id: 'other', user: { id: 2, full_name: 'Beatriz' }, expires_at }), NOW)
    data = upsertLock(data, makeLock({ lock_id: 'tomorrow', date: '2026-10-27', expires_at }), NOW)
    expect(othersLocksForDate(data, '2026-10-26', 1).map((l) => l.lock_id)).toEqual(['other'])
  })
})
