import { create } from 'zustand'

import type { OnlineUser } from '../api/types'

interface PresenceState {
  online: OnlineUser[]
  setOnline: (online: OnlineUser[]) => void
  reset: () => void
}

export const usePresence = create<PresenceState>()((set) => ({
  online: [],
  setOnline: (online) => set({ online }),
  reset: () => set({ online: [] }),
}))
