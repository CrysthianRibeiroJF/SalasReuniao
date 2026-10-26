import { create } from 'zustand'

import type { LockOut } from '../api/types'
import { EMPTY_LOCKS, type LocksData, pruneExpired, removeLock, replaceLocks, upsertLock } from './locksModel'
import { serverNow } from './realtime'

interface LocksState {
  data: LocksData
  replaceAll: (locks: LockOut[]) => void
  upsert: (lock: LockOut) => void
  remove: (lockId: string) => void
  prune: () => void
  reset: () => void
}

export const useLocks = create<LocksState>()((set) => ({
  data: EMPTY_LOCKS,
  replaceAll: (locks) => set({ data: replaceLocks(locks, serverNow()) }),
  upsert: (lock) => set((s) => ({ data: upsertLock(s.data, lock, serverNow()) })),
  remove: (lockId) => set((s) => ({ data: removeLock(s.data, lockId, serverNow()) })),
  prune: () =>
    set((s) => {
      const data = pruneExpired(s.data, serverNow())
      return data === s.data ? s : { data }
    }),
  reset: () => set({ data: EMPTY_LOCKS }),
}))
